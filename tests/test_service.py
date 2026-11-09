"""The service end to end: revisions, durable jobs, cancellation, restart, export and app/CLI/export
parity. Runs execute in real worker processes."""

import dataclasses
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import fixture_ops  # noqa: F401  registers tests.sleep and tests.abort for validation in this process
from conftest import IMPORT_DECLARATION, TESTS_DIR, bars_csv, client_for, new_strategy, run_bundle, save, start, wait
from qrs import datasets, jobs, registry, templates
from qrs.api import create_app
from qrs.compiler import compile_strategy
from qrs.contracts import Edge, Fill, Node, PortRef, StrategyDocument
from qrs.registry import Params, Temporal
from qrs.store import Store


def test_only_loopback_hosts_are_served(home):
    with TestClient(create_app(home), base_url="http://evil.example") as client:
        assert client.get("/api/operators").status_code == 400


def test_stale_saves_are_rejected(home):
    with client_for(home) as client:
        strategy = new_strategy(client)
        assert save(client, strategy, {**strategy["document"], "name": "Mine"}) == 2
        stale = client.put(f"/api/strategies/{strategy['id']}", json={"revision": 1, "document": strategy["document"]})
        assert stale.status_code == 409 and stale.json()["detail"]["revision"] == 2
        assert client.get(f"/api/strategies/{strategy['id']}").json()["document"]["name"] == "Mine"
        assert client.get(f"/api/strategies/{strategy['id']}?revision=1").json()["document"]["name"] == "EMA crossover"


def test_invalid_strategies_and_missing_data_are_refused_before_queuing(home):
    with client_for(home) as client:
        strategy = new_strategy(client)
        doc = strategy["document"]
        doc["edges"] = [e for e in doc["edges"] if e["target"]["node"] != "backtest"]
        revision = save(client, strategy, doc)
        refused = client.post(f"/api/strategies/{strategy['id']}/runs", json={"revision": revision})
        assert refused.status_code == 422
        assert {"node": "backtest", "message": "Input 'bars' is not connected."} in refused.json()["detail"]["issues"]

        missing = client.post("/api/strategies", json={"template_id": "ema_crossover", "dataset_id": "sha256:" + "e" * 64}).json()
        refused = client.post(f"/api/strategies/{missing['id']}/runs", json={"revision": 1})
        assert refused.status_code == 422 and "not found" in refused.json()["detail"]["issues"][0]["message"]
        assert client.get("/api/runs").json() == []


def test_compile_reports_each_nodes_effective_warm_up(home):
    with client_for(home) as client:
        doc = new_strategy(client)["document"]
        compiled = client.post("/api/compile", json={"document": doc}).json()
        # EMA(20) and EMA(50) warm up for 19 and 49 sessions; a cross needs one more session.
        assert compiled["warm_up"] == {"bars": 0, "fast": 19, "slow": 49, "cross_up": 50, "cross_down": 50, "position": 50, "backtest": 50}
        doc["edges"] = doc["edges"][1:]  # an unconnected input makes the strategy invalid
        invalid = client.post("/api/compile", json={"document": doc}).json()
        assert invalid["issues"] and invalid["warm_up"] is None


def test_export_problems_are_reported_not_server_errors(home):
    with client_for(home) as client:
        missing = client.post("/api/strategies", json={"template_id": "ema_crossover", "dataset_id": "sha256:" + "e" * 64}).json()
        response = client.get(f"/api/strategies/{missing['id']}/export")
        assert response.status_code == 422 and "not found" in response.json()["detail"]["issues"][0]["message"]
        assert client.post("/api/strategies", json={"template_id": "../schemas/qrs.schema"}).status_code == 404


def test_data_too_short_for_the_warm_up_is_refused_by_run_and_export(home):
    with client_for(home) as client:
        short = datasets.create_synthetic(home, seed=1, sessions=40)
        strategy = client.post("/api/strategies", json={"template_id": "ema_crossover", "dataset_id": short}).json()
        for refused in (client.post(f"/api/strategies/{strategy['id']}/runs", json={"revision": 1}), client.get(f"/api/strategies/{strategy['id']}/export")):
            assert refused.status_code == 422 and "warm-up of 50 sessions" in refused.json()["detail"]["issues"][0]["message"]


def test_past_results_do_not_depend_on_operators_registered_now(home, monkeypatch):
    with client_for(home) as client:
        strategy = new_strategy(client)
        run = wait(client, start(client, strategy)["id"])
        before = client.get(f"/api/runs/{run['id']}/result").json()

        class Changed(Params):
            window_kind: str  # a new required parameter

        ema = registry.get("qrs.ema", 1)
        monkeypatch.setitem(registry._operators, ("qrs.ema", 1), dataclasses.replace(ema, params=Changed, temporal=Temporal(warm_up=lambda p: 0)))
        assert client.get(f"/api/runs/{run['id']}/result").json() == before
        monkeypatch.delitem(registry._operators, ("qrs.ema", 1))
        assert client.get(f"/api/runs/{run['id']}/result").json() == before
        assert before["results"]["backtest"]["warm_up_sessions"] == 50


def test_failed_runs_keep_their_error_and_log(home):
    with client_for(home) as client:
        strategy = new_strategy(client)
        doc = strategy["document"]
        next(n for n in doc["nodes"] if n["id"] == "backtest")["params"]["initial_cash"] = 100
        run = wait(client, start(client, strategy, save(client, strategy, doc))["id"])
        assert run["status"] == "failed" and "Insufficient capital" in run["error"]
        assert "Traceback" in client.get(f"/api/runs/{run['id']}/log").text
        assert client.get(f"/api/runs/{run['id']}/result").status_code == 409


def test_runs_persist_across_restart_and_orphans_become_interrupted(home):
    with client_for(home) as client:
        strategy = new_strategy(client)
        run = wait(client, start(client, strategy)["id"])
        assert run["status"] == "succeeded"
        result = client.get(f"/api/runs/{run['id']}/result").json()
        backtest = result["results"]["backtest"]
        assert backtest["warm_up_sessions"] == 50 and backtest["first_signal_session"] == backtest["equity"][50]["session"]

    # What a service killed mid-run leaves behind: a running row and an unpublished staging folder.
    Store(home / "qrs.sqlite").create_run("orphan", strategy["id"], 1, "0" * 64)
    Store(home / "qrs.sqlite").update_run("orphan", status="running")
    (home / "runs" / ".staging" / "orphan").mkdir()
    (home / "runs" / ".staging" / "orphan" / "log.txt").write_text("partial log")

    with client_for(home) as client:  # a new service process over the same data
        assert client.get(f"/api/runs/{run['id']}").json()["status"] == "succeeded"
        assert client.get(f"/api/runs/{run['id']}/result").json() == result
        orphan = client.get("/api/runs/orphan").json()
        assert orphan["status"] == "interrupted" and "service stopped" in orphan["error"]
        assert client.get("/api/runs/orphan/log").text == "partial log"


def test_a_worker_stops_when_the_service_goes_away(home, tmp_path):
    with client_for(home) as client:
        doc = with_step_before_backtest(new_strategy(client)["document"], Node(id="pause", operator="tests.sleep", version=1, params={"seconds": 60}))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "strategy.py").write_text(compile_strategy(StrategyDocument.model_validate(doc)))
    worker = subprocess.Popen([sys.executable, "-m", "qrs.worker", str(run_dir), str(home)], stdin=subprocess.PIPE)
    time.sleep(1)
    worker.stdin.close()  # what the operating system does when the service process dies
    assert worker.wait(timeout=10) == 1


def with_step_before_backtest(doc: dict, node: Node) -> dict:
    """Insert `node` (bars -> bars) between the dataset and the backtest."""
    doc["nodes"].append(node.model_dump())
    doc["edges"] = [e for e in doc["edges"] if e["target"] != {"node": "backtest", "port": "bars"}]
    doc["edges"] += [
        Edge(source=PortRef(node="bars", port="bars"), target=PortRef(node=node.id, port="bars")).model_dump(),
        Edge(source=PortRef(node=node.id, port="bars"), target=PortRef(node="backtest", port="bars")).model_dump(),
    ]
    return doc


def test_cancelling_a_running_worker(home):
    with client_for(home) as client:
        strategy = new_strategy(client)
        doc = with_step_before_backtest(strategy["document"], Node(id="pause", operator="tests.sleep", version=1, params={"seconds": 60}))
        slow = start(client, strategy, save(client, strategy, doc))
        queued = start(client, strategy)  # waits behind the slow run (one worker at a time)
        wait(client, slow["id"], until=("running",))
        assert client.post(f"/api/runs/{queued['id']}/cancel").json()["status"] == "cancelled"
        started = time.monotonic()
        client.post(f"/api/runs/{slow['id']}/cancel")
        cancelled = wait(client, slow["id"])
        assert cancelled["status"] == "cancelled" and time.monotonic() - started < 10
        assert client.get(f"/api/runs/{slow['id']}/result").status_code == 409
        assert (home / "runs" / slow["id"] / "strategy.py").exists()  # diagnostics retained
        assert client.post(f"/api/runs/{slow['id']}/cancel").status_code == 409


def test_a_run_past_its_time_limit_is_stopped_and_the_next_run_starts(home):
    with client_for(home, time_limit=10) as client:
        strategy = new_strategy(client)
        doc = with_step_before_backtest(strategy["document"], Node(id="pause", operator="tests.sleep", version=1, params={"seconds": 60}))
        slow = start(client, strategy, save(client, strategy, doc))
        queued = start(client, strategy)  # waits behind the slow run
        started = time.monotonic()
        stopped = wait(client, slow["id"])
        assert stopped["status"] == "failed" and stopped["error"] == "Stopped after the time limit of 10 seconds."
        assert time.monotonic() - started < 40  # well before the 60-second sleep would end
        assert (home / "runs" / slow["id"] / "log.txt").exists()
        assert wait(client, queued["id"])["status"] == "succeeded"  # the queue moved on; under the limit, unaffected


def test_a_worker_that_ignores_termination_is_killed_at_its_time_limit(home, monkeypatch):
    monkeypatch.setattr(jobs, "KILL_AFTER", 3)
    with client_for(home, time_limit=6) as client:
        strategy = new_strategy(client)
        pause = Node(id="pause", operator="tests.sleep", version=1, params={"seconds": 60, "ignore_terminate": True})
        started = time.monotonic()
        stubborn = start(client, strategy, save(client, strategy, with_step_before_backtest(strategy["document"], pause)))
        stopped = wait(client, stubborn["id"])
        elapsed = time.monotonic() - started
        assert stopped["status"] == "failed" and stopped["error"] == "Stopped after the time limit of 6 seconds."
        assert 6 + 3 <= elapsed < 40  # it outlasted the termination, so only the kill stopped it


def test_a_crashing_worker_fails_only_its_own_run(home):
    with client_for(home) as client:
        strategy = new_strategy(client)
        doc = with_step_before_backtest(strategy["document"], Node(id="crash", operator="tests.abort", version=1))
        crashing = start(client, strategy, save(client, strategy, doc))
        healthy = start(client, strategy)  # queued behind the crashing run
        crashed = wait(client, crashing["id"])
        assert crashed["status"] == "failed" and "terminated by signal 6" in crashed["error"]
        assert wait(client, healthy["id"])["status"] == "succeeded"


def upload(client, content: bytes, declaration: dict) -> str:
    params = {"declaration": json.dumps(declaration)}
    response = client.post("/api/datasets/import", params=params, content=content, headers={"Content-Type": "text/csv"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def upload_synthetic_csv(client) -> str:
    """The synthetic bars without one week, uploaded as a CSV file; returns the new dataset's ID."""
    frame = datasets.synthetic_bars(seed=42, sessions=300)
    return upload(client, bars_csv(frame.drop(frame.index[150:155])), IMPORT_DECLARATION)


def upload_fixture(client, home, template_id: str) -> dict[str, str]:
    """The template's fixture bars, uploaded, and any features it reads, prepared in the service's
    home as `qrs extract` would (extraction is CLI-only): the dataset IDs by kind."""
    declaration = templates.load(template_id).fixture.declaration.model_dump(mode="json")
    chosen = {datasets.BARS: upload(client, (templates.TEMPLATE_DIR / f"{template_id}.csv").read_bytes(), declaration)}
    features = templates.prepare_features(template_id, home)
    return chosen | ({datasets.OBSERVATIONS: features} if features else {})


@pytest.mark.parametrize(
    "template_id, source",
    [("ema_crossover", "synthetic"), ("ema_crossover", "uploaded"), *((t["id"], "fixture") for t in templates.list_templates())],
)
def test_app_cli_and_export_produce_identical_results(home, tmp_path_factory, template_id, source):
    with client_for(home) as client:
        if source == "fixture":
            created = client.post("/api/strategies", json={"template_id": template_id, "datasets": upload_fixture(client, home, template_id)})
            assert created.status_code == 200, created.text
            strategy = created.json()
        else:
            strategy = new_strategy(client, upload_synthetic_csv(client) if source == "uploaded" else None, template_id)
        app_run = wait(client, start(client, strategy)["id"])
        app_result = client.get(f"/api/runs/{app_run['id']}/result").json()
        app_record = app_result["record"]
        if source == "fixture":
            app_fills = [Fill.model_validate(fill) for fill in app_result["results"]["backtest"]["fills"]]
            assert app_fills == templates.load(template_id).fixture.fills
        bundle = client.get(f"/api/strategies/{strategy['id']}/export").content
        assert client.get(f"/api/strategies/{strategy['id']}/export").content == bundle  # deterministic

    work = tmp_path_factory.mktemp("parity")
    (work / "strategy.json").write_text(json.dumps(strategy["document"]))
    command = [sys.executable, "-m", "qrs.cli", "run", str(work / "strategy.json"), "--data", str(home), "--out", str(work / "cli")]
    cli = subprocess.run(command, capture_output=True, text=True)
    assert cli.returncode == 0, cli.stderr
    cli_record = json.loads((work / "cli" / "record.json").read_text())
    export_record = run_bundle(bundle, work)

    lock = (Path(__file__).parents[1] / "src" / "qrs" / "runtime.lock").read_bytes()
    assert zipfile.ZipFile(io.BytesIO(bundle)).read("requirements.txt") == lock
    assert app_record["runtime"]["lock_sha256"] == hashlib.sha256(lock).hexdigest()
    for record in (cli_record, export_record):
        assert record["code_sha256"] == app_record["code_sha256"] == app_run["code_sha256"]
        assert record["runtime"] == app_record["runtime"]
        assert record["datasets"] == app_record["datasets"]
        assert record["outputs"] == app_record["outputs"]
        assert record["result_sha256"] == app_record["result_sha256"] == app_run["result_sha256"]


def test_extension_operator_needs_no_editor_or_compiler_change(home, tmp_path):
    loaded = subprocess.run(
        [sys.executable, "-c", "from qrs import registry; print(registry.find('ext.roc', 1).title)"],
        env={**os.environ, "QRS_OPERATOR_MODULES": "qrs_roc"},
        capture_output=True,
        text=True,
    )
    assert loaded.stdout.strip() == "Rate of change", loaded.stderr
    import qrs_roc  # noqa: F401  same registration the environment variable performs at startup

    with client_for(home) as client:
        palette = {o["id"]: o for o in client.get("/api/operators").json()}
        roc, candle = palette["ext.roc"], palette["ext.candle"]
        assert roc["inputs"] == [{"name": "bars", "type": "bars"}] and roc["params_schema"]["properties"]["period"]["default"] == 10
        assert candle["outputs"] == [{"name": "table", "type": "table"}] and candle["family"] == "features"

        strategy = new_strategy(client)
        doc = strategy["document"]
        for node in doc["nodes"]:
            if node["id"] in ("fast", "slow"):
                node["operator"] = "ext.roc"
        # A custom feature table, widened by a built-in merge: it runs beside the trades.
        doc["nodes"] += [
            {"id": "candle", "operator": "ext.candle", "version": 1, "params": {}},
            {"id": "change", "operator": "qrs.returns", "version": 1, "params": {}},
            {"id": "features", "operator": "qrs.merge", "version": 1, "params": {"name": "change"}},
        ]
        doc["edges"] += [Edge(source=PortRef(node="bars", port="bars"), target=PortRef(node=n, port="bars")).model_dump() for n in ("candle", "change")] + [
            Edge(source=PortRef(node="candle", port="table"), target=PortRef(node="features", port="table")).model_dump(),
            Edge(source=PortRef(node="change", port="series"), target=PortRef(node="features", port="series")).model_dump(),
        ]
        compiled = client.post("/api/compile", json={"document": doc}).json()
        assert compiled["issues"] == [] and "import qrs_roc" in compiled["code"] and "'ext.candle', 1" in compiled["code"]
        run = wait(client, start(client, strategy, save(client, strategy, doc))["id"])
        assert run["status"] == "succeeded", run["error"]
        record = client.get(f"/api/runs/{run['id']}/result").json()["record"]
        assert record["operators"]["ext.roc@1"]["module"] == record["operators"]["ext.candle@1"]["module"] == "qrs_roc"
        bundle = client.get(f"/api/strategies/{strategy['id']}/export").content

    assert "qrs_roc.py" in zipfile.ZipFile(io.BytesIO(bundle)).namelist()
    assert run_bundle(bundle, tmp_path)["result_sha256"] == record["result_sha256"]

    # The editor and compiler contain no knowledge of the new operator.
    root = TESTS_DIR.parent
    sources = [root / "src/qrs/compiler.py", root / "src/qrs/graph.py", *(root / "apps/studio/src").rglob("*.ts*")]
    assert not [p for p in sources if re.search(r"\broc\b|qrs_roc|candle", p.read_text(), re.IGNORECASE)]
