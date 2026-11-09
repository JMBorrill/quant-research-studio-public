"""The Ollama and Anthropic adapters (Phase 4B workstream 4), against loopback test servers that stand
in for Ollama and the Anthropic API: what each request carries, what each response records, how
failures stop a recording, why text cannot leave the machine without consent, and that every
provider gives the same rows for the same answers."""

import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd
import pytest

from qrs import datasets, export, extraction, providers
from qrs.extraction import ExtractionError
from qrs.hashing import sha256_hex
from qrs.providers import ollama
from test_extraction import ANSWERS, DOCUMENT_TEXTS, TITLES, answers_file, documents, feature_strategy, spec
from test_observations import import_fixture

MODEL = "llama3.1:8b"
DIGEST = "sha256:" + "ab" * 32
WINDOW = {"num_ctx": 4096}  # holds each fixture request whole: about 1,200 tokens at most
SENTINEL = "sk-ant-api03-SENTINEL-credential-0123456789"
BY_PROMPT = {sha256_hex(extraction.render("Headline: {title}\n\n{text}", TITLES[d], DOCUMENT_TEXTS[d, r]).encode()): a for (d, r), a in ANSWERS.items()}


class Server:
    """A loopback HTTP server that records each request and answers with `respond(method, path, body)`."""

    def __init__(self, respond):
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def handle_one(self, method):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                parsed = json.loads(body) if body else None
                server.requests.append({"method": method, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": parsed})
                status, answer = respond(method, self.path, parsed)
                content = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def do_GET(self):
                self.handle_one("GET")

            def do_POST(self):
                self.handle_one("POST")

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def calls(self, path: str) -> list[dict]:
        return [r for r in self.requests if r["path"] == path]


@pytest.fixture
def no_proxies(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


def ollama_server(models=None, answers=BY_PROMPT, done_reason="stop", status=200, context_length=WINDOW["num_ctx"]):
    # By default the model's context length is exactly WINDOW's, which Ollama accepts as it is.
    listed = models if models is not None else [{"name": MODEL, "model": MODEL, "digest": DIGEST}]
    model_info = {"general.architecture": "qwen2"} | ({} if context_length is None else {"qwen2.context_length": context_length})

    def respond(method, path, body):
        if path == "/api/tags":
            return 200, {"models": listed}
        if path == "/api/show":
            return 200, {"model_info": model_info}
        if status != 200:
            return status, {"error": "model failed to load"}
        prompt = body["messages"][1]["content"]
        return 200, {
            "model": MODEL,
            "created_at": "2026-11-01T12:00:00Z",
            "message": {"role": "assistant", "content": answers.get(sha256_hex(prompt.encode()), "no idea")},
            "done": True,
            "done_reason": done_reason,
            "prompt_eval_count": 40,
            "eval_count": 7,
        }

    return Server(respond)


def anthropic_server(answers=BY_PROMPT, stop_reason="end_turn", refusal=None):
    def respond(method, path, body):
        prompt = body["messages"][0]["content"]
        message = {
            "id": "msg_" + sha256_hex(prompt.encode())[:12],
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": answers.get(sha256_hex(prompt.encode()), "{}")}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "stop_details": {"type": "refusal", "category": refusal, "explanation": "declined"} if refusal else None,
            "usage": {"input_tokens": 120, "output_tokens": 30, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        }
        return 200, message

    return Server(respond)


def ollama_spec(tmp_path, server, **changes):
    return spec(documents(tmp_path), server.url, provider="ollama", model=MODEL, **{"options": WINDOW, **changes})


def anthropic_spec(tmp_path, monkeypatch, server, hosted_processing=True, **changes):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    return spec(documents(tmp_path, hosted_processing=hosted_processing), None, provider="anthropic", model="claude-opus-5-5", **changes)


def record(tmp_path, extracted) -> str:
    return extraction.extract(tmp_path, extracted, record=True, report=lambda _: None)


def test_ollama_is_asked_for_the_schema_on_its_chat_api_with_no_tools(tmp_path, no_proxies):
    server = ollama_server()
    dataset_id = record(tmp_path, ollama_spec(tmp_path, server, options={"temperature": 0.0, "seed": 7, **WINDOW}))
    chats = server.calls("/api/chat")
    assert len(chats) == 4 and len(server.calls("/api/tags")) == 5  # once to check, then once per request
    assert [show["body"] for show in server.calls("/api/show")] == [{"model": MODEL}] * 4
    body = chats[0]["body"]
    assert set(body) == {"model", "messages", "format", "stream", "shift", "options"}
    # So Ollama refuses a prompt longer than the window rather than cutting it.
    assert all(chat["body"]["shift"] is False for chat in chats)
    assert body["format"] == providers.answer_schema(spec("sha256:" + "0" * 64, None).fields)
    assert "minimum" not in json.dumps(body["format"])
    assert (body["stream"], body["options"]) == (False, {"temperature": 0.0, "seed": 7, "num_ctx": 4096, "num_predict": 50})
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    d = datasets.read_manifest(tmp_path, dataset_id).derivation
    assert (d.reported_models, d.hosted, d.input_tokens, d.output_tokens, d.invalid_rows) == ([f"{MODEL}@{DIGEST}"], False, 160, 28, {})


def test_an_ollama_answer_stopped_at_its_length_is_truncated(tmp_path, no_proxies):
    dataset_id = record(tmp_path, ollama_spec(tmp_path, ollama_server(done_reason="length")))
    assert datasets.read_manifest(tmp_path, dataset_id).derivation.invalid_rows == {"truncated": 4}


def test_an_ollama_error_or_silence_stops_the_recording_and_stores_nothing(tmp_path, no_proxies):
    with pytest.raises(ExtractionError, match="Recording stopped: Ollama at .* answered 500"):
        record(tmp_path, ollama_spec(tmp_path, ollama_server(status=500)))
    assert not list((tmp_path / "responses").glob("*.json"))
    closed = ollama_server()
    closed.httpd.shutdown()
    closed.httpd.server_close()
    with pytest.raises(ExtractionError, match="Recording stopped: Ollama at .* did not answer"):
        record(tmp_path, ollama_spec(tmp_path, closed))


@pytest.mark.parametrize(
    ("endpoint", "message"),
    [
        ("http://192.0.2.1:11434", r"resolves to \['192.0.2.1'\]: only loopback addresses keep text on this machine"),
        ("https://127.0.0.1:11434", "must be a base http:// address"),
        ("http://127.0.0.1:11434/api", "must be a base http:// address"),
        ("127.0.0.1:11434", "must be a base http:// address"),
        ("http://127.0.0.1:99999", "must be a base http:// address"),
        ("http://127.0.0.1:0", "must be a base http:// address"),
        ("http://[::1", "must be a base http:// address"),
        ("http://user:secret@127.0.0.1:11434", "must be a base http:// address"),
    ],
)
def test_an_ollama_endpoint_that_is_not_loopback_is_refused_before_connecting(tmp_path, endpoint, message):
    with pytest.raises(ExtractionError, match=message):
        record(tmp_path, spec(documents(tmp_path), endpoint, provider="ollama", model=MODEL, options=WINDOW))
    assert not (tmp_path / "responses").exists()


def test_ollama_requests_ignore_proxy_settings_and_go_straight_to_loopback(tmp_path):
    # In a fresh process, so that no proxy handler cached by an earlier request can hide one: every
    # proxy variable points at a recording server, which must never be asked anything.
    server, proxy = ollama_server(), Server(lambda method, path, body: (200, {}))
    extracted = ollama_spec(tmp_path, server)
    (tmp_path / "extraction.json").write_text(extracted.model_dump_json())
    env = {k: v for k, v in os.environ.items() if k.lower() != "no_proxy"}
    env |= {name: proxy.url for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy")}
    command = [sys.executable, "-m", "qrs.cli", "extract", str(tmp_path / "extraction.json"), "--record", "--home", str(tmp_path)]
    recorded = subprocess.run(command, capture_output=True, text=True, env=env)
    assert recorded.returncode == 0, recorded.stderr
    assert proxy.requests == [] and len(server.calls("/api/chat")) == 4


def test_ollama_connects_to_the_address_it_checked_not_the_name(tmp_path, monkeypatch, no_proxies):
    # Connecting by name would resolve it again, which DNS rebinding could point elsewhere.
    server, connected = ollama_server(), []
    real = ollama.http.client.HTTPConnection

    def connection(host, *args, **kwargs):
        connected.append(host)
        return real(host, *args, **kwargs)

    monkeypatch.setattr(ollama.http.client, "HTTPConnection", connection)
    record(tmp_path, spec(documents(tmp_path), server.url.replace("127.0.0.1", "localhost"), provider="ollama", model=MODEL, options=WINDOW))
    assert set(connected) == {"127.0.0.1"} and len(connected) == 13  # tags 5, show 4, chat 4


def test_an_ollama_host_resolving_to_any_address_off_this_machine_is_refused(tmp_path, monkeypatch):
    real = ollama.socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        if host == "mixed.test":  # one loopback address and one elsewhere
            return [(2, 1, 6, "", ("127.0.0.1", 11434)), (2, 1, 6, "", ("192.0.2.1", 11434))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(ollama.socket, "getaddrinfo", resolve)
    with pytest.raises(ExtractionError, match=r"resolves to \['127.0.0.1', '192.0.2.1'\]: only loopback"):
        record(tmp_path, spec(documents(tmp_path), "http://mixed.test:11434", provider="ollama", model=MODEL, options=WINDOW))


def test_an_ollama_server_that_is_not_http_or_too_slow_stops_the_recording(tmp_path, monkeypatch, no_proxies):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def not_http():
        while True:
            connection, _ = listener.accept()
            connection.sendall(b"SSH-2.0-OpenSSH_9.6\r\n")
            connection.close()

    threading.Thread(target=not_http, daemon=True).start()
    with pytest.raises(ExtractionError, match="Recording stopped: Ollama at .* did not answer"):
        record(tmp_path, spec(documents(tmp_path), f"http://127.0.0.1:{listener.getsockname()[1]}", provider="ollama", model=MODEL, options=WINDOW))
    slow = Server(lambda method, path, body: (time.sleep(1), (200, {"models": []}))[1])
    monkeypatch.setattr(ollama, "TIMEOUT_SECONDS", 0.2)
    with pytest.raises(ExtractionError, match="Recording stopped: Ollama at .* did not answer: timed out"):
        record(tmp_path, ollama_spec(tmp_path, slow))
    assert not (tmp_path / "responses").exists()


@pytest.mark.parametrize(
    ("models", "message"),
    [
        ([{"name": MODEL, "model": MODEL, "digest": DIGEST, "remote_host": "https://ollama.com:443", "remote_model": "gpt-oss:120b"}], "runs .* remotely"),
        ([{"name": "other:7b", "model": "other:7b", "digest": DIGEST}], "has no local model"),
        ([{"name": MODEL, "model": MODEL}], "without a digest"),
    ],
)
def test_an_ollama_model_that_is_remote_or_not_local_is_refused_before_any_chat(tmp_path, no_proxies, models, message):
    server = ollama_server(models=models)
    with pytest.raises(ExtractionError, match=message):
        record(tmp_path, ollama_spec(tmp_path, server))
    assert server.calls("/api/chat") == []


def test_ollama_options_are_checked_before_any_call(tmp_path):
    with pytest.raises(ExtractionError, match="Option 'seed' must be a int"):
        record(tmp_path, spec(documents(tmp_path), "http://127.0.0.1:1", provider="ollama", model=MODEL, options={"seed": 1.5}))
    with pytest.raises(ExtractionError, match="ollama accepts no option 'effort'"):
        record(tmp_path, spec(documents(tmp_path), "http://127.0.0.1:1", provider="ollama", model=MODEL, options={"effort": "low"}))


def test_ollama_must_be_given_a_context_window_that_holds_every_request_whole(tmp_path, no_proxies):
    # Ollama cuts a prompt longer than num_ctx and still reports a normal finish. So an extraction
    # through it is refused, in recording and in replay, unless num_ctx holds its largest request:
    # its UTF-8 bytes, 1,024 tokens of framing and its output limit.
    server = ollama_server()
    extracted = ollama_spec(tmp_path, server)
    needed = max(sum(providers.token_bound(extraction.request_for(extracted, TITLES[d], text))) for (d, _), text in DOCUMENT_TEXTS.items())
    for options, said in (({}, "the default"), ({"num_ctx": needed - 1}, str(needed - 1))):
        for recording in (True, False):
            with pytest.raises(ExtractionError, match=rf"options.num_ctx must hold every request whole: at least {needed} tokens .*, not {said}\. Nothing"):
                extraction.extract(tmp_path, extracted.model_copy(update={"options": options}), record=recording, report=lambda _: None)
    assert server.requests == []
    record(tmp_path, extracted.model_copy(update={"options": {"num_ctx": needed}}))
    assert len(server.calls("/api/chat")) == 4


@pytest.mark.parametrize(
    ("context_length", "message"),
    [
        (WINDOW["num_ctx"] - 1, "has a context length of 4095 tokens, which Ollama would use instead of num_ctx 4096"),
        (None, "did not report the context length"),
    ],
)
def test_ollama_is_refused_a_context_window_beyond_the_models_before_any_chat(tmp_path, no_proxies, context_length, message):
    # Ollama lowers a larger num_ctx to the model's context length, and then cuts a longer prompt.
    server = ollama_server(context_length=context_length)
    with pytest.raises(ExtractionError, match=f"Recording stopped: .*{message}"):
        record(tmp_path, ollama_spec(tmp_path, server))
    assert server.calls("/api/chat") == [] and not list((tmp_path / "responses").glob("*.json"))


def test_anthropic_is_asked_for_the_schema_with_no_tools_fallbacks_or_sampling(tmp_path, monkeypatch, no_proxies):
    server = anthropic_server()
    dataset_id = record(tmp_path, anthropic_spec(tmp_path, monkeypatch, server, options={"effort": "low"}))
    calls = server.calls("/v1/messages")
    assert len(calls) == 4
    body = calls[0]["body"]
    assert set(body) == {"model", "max_tokens", "system", "messages", "output_config"}
    assert body["output_config"] == {
        "format": {"type": "json_schema", "schema": providers.answer_schema(spec("sha256:" + "0" * 64, None).fields)},
        "effort": "low",
    }
    assert (body["model"], body["max_tokens"], len(body["messages"])) == ("claude-opus-5-5", 50, 1)
    assert calls[0]["headers"]["authorization"] == f"Bearer {SENTINEL}"  # sent to the provider, and nowhere else
    assert "anthropic-beta" not in calls[0]["headers"]  # no beta features, such as server-side fallbacks
    manifest = datasets.read_manifest(tmp_path, dataset_id)
    d = manifest.derivation
    assert (d.reported_models, d.hosted, d.input_tokens, d.output_tokens, d.invalid_rows) == (["claude-opus-5-5"], True, 480, 120, {})
    line = json.loads((datasets.directory(tmp_path, dataset_id) / "responses.jsonl").read_text().splitlines()[0])
    assert line["record"]["response_id"].startswith("msg_") and line["record"]["stop_reason"] == "end_turn"


@pytest.mark.parametrize(
    ("stop_reason", "refusal", "reason"), [("refusal", "general_harms", "refused"), ("max_tokens", None, "truncated"), ("pause_turn", None, "truncated")]
)
def test_an_anthropic_answer_that_did_not_finish_normally_has_no_values(tmp_path, monkeypatch, no_proxies, stop_reason, refusal, reason):
    dataset_id = record(tmp_path, anthropic_spec(tmp_path, monkeypatch, anthropic_server(stop_reason=stop_reason, refusal=refusal)))
    assert datasets.read_manifest(tmp_path, dataset_id).derivation.invalid_rows == {reason: 4}
    line = json.loads((datasets.directory(tmp_path, dataset_id) / "responses.jsonl").read_text().splitlines()[0])
    assert line["record"]["stop_reason"] == (f"{stop_reason} ({refusal})" if refusal else stop_reason)


def test_an_overloaded_anthropic_answer_is_asked_once_and_stops_the_recording(tmp_path, monkeypatch, no_proxies):
    overloaded = Server(lambda method, path, body: (529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}))
    with pytest.raises(ExtractionError, match="Recording stopped: Anthropic did not answer: OverloadedError"):
        record(tmp_path, anthropic_spec(tmp_path, monkeypatch, overloaded))
    assert len(overloaded.requests) == 1  # SDK retries are off
    assert not list((tmp_path / "responses").glob("*.json"))


def test_hosted_extraction_needs_the_documents_consent_before_any_call(tmp_path, monkeypatch, no_proxies):
    server = anthropic_server()
    with pytest.raises(ExtractionError, match="may not be sent to a hosted provider"):
        record(tmp_path, anthropic_spec(tmp_path, monkeypatch, server, hosted_processing=False))
    assert server.requests == []
    with pytest.raises(ExtractionError, match="Option 'effort' must be one of low, medium, high, xhigh, max"):
        record(tmp_path, anthropic_spec(tmp_path, monkeypatch, server, options={"effort": "extreme"}))
    with pytest.raises(ExtractionError, match="max_output_tokens 16001 is over anthropic's limit of 16000"):
        record(tmp_path, anthropic_spec(tmp_path, monkeypatch, server, max_output_tokens=16001))
    assert server.requests == []


def test_without_the_sdk_only_recording_through_anthropic_fails_and_its_responses_still_replay(tmp_path, monkeypatch, no_proxies):
    extracted = anthropic_spec(tmp_path, monkeypatch, anthropic_server())
    recorded_id = record(tmp_path, extracted)
    (tmp_path / "extraction.json").write_text(extracted.model_dump_json())
    # A fresh process without the SDK, or any network, replays the recorded extraction.
    script = (
        "import sys, socket\n"
        "sys.modules['anthropic'] = None\n"
        "def refuse(*a, **k): raise OSError('network used')\n"
        "socket.socket.connect = refuse; socket.create_connection = refuse; socket.getaddrinfo = refuse\n"
        "from qrs import cli\n"
        f"sys.exit(cli.main(['extract', {str(tmp_path / 'extraction.json')!r}, '--home', {str(tmp_path)!r}]))\n"
    )
    replayed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert replayed.returncode == 0, replayed.stderr
    assert replayed.stdout.splitlines()[0] == f"dataset  {recorded_id}"
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(ExtractionError, match=r"needs its SDK: install the optional extra, qrs\[hosted\]"):
        record(tmp_path, extracted.model_copy(update={"capture": "again"}))


def test_every_provider_gives_the_same_rows_for_the_same_answers(tmp_path, monkeypatch, no_proxies):
    frames = {}
    for name, extracted in (
        ("fixture", spec(documents(tmp_path), answers_file(tmp_path, ANSWERS))),
        ("ollama", ollama_spec(tmp_path, ollama_server())),
        ("anthropic", anthropic_spec(tmp_path, monkeypatch, anthropic_server())),
    ):
        frames[name] = datasets.load_observations(tmp_path, record(tmp_path, extracted))
    # The declared 300 seconds exceed any loopback latency, so every row has the same time.
    pd.testing.assert_frame_equal(frames["fixture"], frames["ollama"])
    pd.testing.assert_frame_equal(frames["fixture"], frames["anthropic"])


def test_a_credential_reaches_no_file_record_log_or_export(tmp_path, monkeypatch, capfd, no_proxies):
    bars_id, _ = import_fixture(tmp_path)
    features_id = record(tmp_path, anthropic_spec(tmp_path, monkeypatch, anthropic_server()))
    bundle = export.build(feature_strategy(bars_id, features_id), tmp_path)
    strategy = tmp_path / "strategy.json"
    strategy.write_text(feature_strategy(bars_id, features_id).model_dump_json())
    ran = subprocess.run([sys.executable, "-m", "qrs.cli", "run", str(strategy), "--data", str(tmp_path), "--out", str(tmp_path / "run")], capture_output=True)
    assert ran.returncode == 0, ran.stderr
    secret = SENTINEL.encode()
    leaked = [str(p) for p in tmp_path.rglob("*") if p.is_file() and secret in p.read_bytes()]
    with zipfile.ZipFile(io.BytesIO(bundle)) as z:
        leaked += [name for name in z.namelist() if secret in z.read(name)]
    out, err = capfd.readouterr()
    assert leaked == [] and SENTINEL not in out + err and secret not in ran.stdout + ran.stderr


# Run in every process started with it on PYTHONPATH: records any attempt to import a provider
# adapter or the SDK.
IMPORT_GUARD = """
import os, sys
class Guard:
    def find_spec(self, name, path=None, target=None):
        if name.startswith("qrs.providers.") or name.split(".")[0] == "anthropic":
            with open(os.environ["QRS_IMPORT_LOG"], "a") as log:
                log.write(f"{os.getpid()} {name}\\n")
        return None
sys.meta_path.insert(0, Guard())
"""


def test_runs_experiments_and_exports_import_no_provider_module(tmp_path):
    bars_id, _ = import_fixture(tmp_path)
    features_id = record(tmp_path, spec(documents(tmp_path, evidence_class="synthetic"), answers_file(tmp_path, ANSWERS)))
    strategy = tmp_path / "strategy.json"
    strategy.write_text(feature_strategy(bars_id, features_id).model_dump_json())
    (tmp_path / "guard").mkdir()
    (tmp_path / "guard" / "sitecustomize.py").write_text(IMPORT_GUARD)
    log = tmp_path / "imports.log"
    # A fresh process, with every process it starts: a CLI run, an export and its bundle run, and
    # through the service a run, an experiment's trials and its holdout evaluation in workers.
    script = f"""
import io, subprocess, sys, time, zipfile
from pathlib import Path
from fastapi.testclient import TestClient
from qrs import cli, export
from qrs.api import create_app
from qrs.contracts import StrategyDocument
home, strategy = Path({str(tmp_path)!r}), Path({str(strategy)!r})
assert cli.main(["run", str(strategy), "--data", str(home), "--out", str(home / "cli")]) == 0
doc = StrategyDocument.model_validate_json(strategy.read_text())
zipfile.ZipFile(io.BytesIO(export.build(doc, home))).extractall(home / "bundle")
subprocess.run([sys.executable, "run.py", str(home / "bundle-out")], cwd=home / "bundle", check=True)

def finished(client, run_id):
    while (run := client.get(f"/api/runs/{{run_id}}").json())["status"] not in ("succeeded", "failed"):
        time.sleep(0.1)
    assert run["status"] == "succeeded", run

with TestClient(create_app(home), base_url="http://localhost") as client:
    created = client.post("/api/strategies", json={{"document": doc.model_dump(mode="json")}}).json()
    finished(client, client.post(f"/api/strategies/{{created['id']}}/runs", json={{"revision": created["revision"]}}).json()["id"])
    body = {{"name": "Thresholds", "strategy_id": created["id"], "revision": created["revision"], "search": "grid",
            "axes": [{{"node": "entry", "param": "value", "values": [0.0, 0.1]}}], "objective": "net_pnl",
            "direction": "maximize", "holdout_start": "2024-12-02"}}
    experiment = client.post("/api/experiments", json=body)
    assert experiment.status_code == 200, experiment.text
    for trial in experiment.json()["trials"]:
        finished(client, trial["run"]["id"])
    holdout = client.post(f"/api/experiments/{{experiment.json()['id']}}/holdout", json={{"trial": 1}})
    assert holdout.status_code == 200, holdout.text
    finished(client, client.get(f"/api/experiments/{{experiment.json()['id']}}").json()["holdout"][0]["run"]["id"])
print(sorted(m for m in sys.modules if m.startswith("qrs.providers.") or m.split(".")[0] == "anthropic"))
"""
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "guard"), "QRS_IMPORT_LOG": str(log)}
    ran = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=300)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip().splitlines()[-1] == "[]"
    assert not log.exists(), log.read_text()


def test_ollama_loopback_check_accepts_localhost_and_ipv6_loopback():
    assert ollama.loopback("http://localhost:11434")[0] in ("127.0.0.1", "::1")
    assert ollama.loopback("http://[::1]:11434") == ("::1", 11434)
    assert ollama.loopback("http://127.0.0.1") == ("127.0.0.1", 11434)
