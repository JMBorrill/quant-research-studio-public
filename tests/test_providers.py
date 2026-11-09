"""The provider interface, strict answer parsing, cost bounds, the write-once response store and the
fixture provider (Phase 4B workstream 2)."""

import base64
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from qrs import providers, responses
from qrs.contracts import OutputField, ProviderRequest, ResponseRecord
from qrs.hashing import sha256_hex
from qrs.providers import fixture

FIELDS = [
    OutputField(name="sentiment", description="Tone towards the market.", unit="score", minimum=-1, maximum=1),
    OutputField(name="relevance", description="How much the story concerns the market.", unit="probability", minimum=0, maximum=1),
]


def request(**changes) -> ProviderRequest:
    asked = {
        "provider": "fixture",
        "model": "fixture",
        "instructions": "Rate the story. Answer with JSON only.",
        "prompt": "Headline: Rates hold\n\nThe committee kept rates at 4.5%.",
        "answer_schema": providers.answer_schema(FIELDS),
        "max_output_tokens": 200,
        "options": {"temperature": 0.7},
        "capture": "2026-11-01",
    }
    return ProviderRequest.model_validate({**asked, **changes})


def record(asked: ProviderRequest | None = None, **changes) -> ResponseRecord:
    asked = asked or request()
    stored = {
        "request": asked,
        "raw_base64": base64.b64encode(b'{"text": "..."}').decode(),
        "text": '{"sentiment": 0.5, "relevance": 1}',
        "response_id": "r1",
        "models": ["fixture:abc"],
        "finish": "end",
        "stop_reason": "end",
        "input_tokens": 20,
        "output_tokens": 9,
        "cost": 0.0,
        "captured_at": datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
        "latency_seconds": 0.25,
    }
    return ResponseRecord.model_validate({**stored, **changes})


# The request above, hashed once and pinned: the key must not change between processes or platforms.
PINNED_KEY = "b8db13d2d32a83da0dc92406d181d6167400a9c34d6d0825d70b845adf2787d7"


def test_the_request_hash_is_stable_across_processes_and_platforms():
    key = responses.request_sha256(request())
    script = "from test_providers import request; from qrs import responses; print(responses.request_sha256(request()))"
    other = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True, cwd=Path(__file__).parent).stdout.strip()
    assert other == key == PINNED_KEY


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "other"},
        {"model": "fixture-2"},
        {"instructions": "Rate it."},
        {"prompt": "Headline: Rates cut"},
        {"answer_schema": providers.answer_schema(FIELDS[:1])},
        {"max_output_tokens": 201},
        {"options": {"seed": 1}},
        {"capture": "2026-11-02"},
    ],
)
def test_the_request_hash_changes_with_every_part_of_the_request(change):
    assert responses.request_sha256(request(**change)) != responses.request_sha256(request())


def test_a_request_holds_nothing_but_what_is_asked():
    # Credentials, endpoints and budgets are not part of a request, so they cannot reach the store.
    for extra in ({"api_key": "secret"}, {"endpoint": "http://127.0.0.1:11434"}, {"budget": {"max_cost": 1}}):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            request(**extra)


def test_the_answer_schema_asks_for_exactly_the_declared_numbers_without_bounds():
    assert providers.answer_schema(FIELDS[:1]) == {
        "type": "object",
        "properties": {"sentiment": {"type": "number", "description": "Tone towards the market. In score, from -1.0 to 1.0."}},
        "required": ["sentiment"],
        "additionalProperties": False,
    }


@pytest.mark.parametrize(
    ("text", "finish", "reason"),
    [
        ('{"sentiment": 0.5, "relevance": 1}', "truncated", "truncated"),
        ('{"sentiment": 0.5, "relevance": 1}', "refused", "refused"),
        (None, "end", "no_answer"),
        ("Sure! Here it is:", "end", "not_json"),
        ('```json\n{"sentiment": 0.5, "relevance": 1}\n```', "end", "not_json"),
        ('{"sentiment": 0.5, "relevance": 1} extra', "end", "not_json"),
        ('{"sentiment": 0.5, "relevance": 1, "sentiment": 0.6}', "end", "not_json"),
        ("[0.5, 1]", "end", "not_json"),
        ('{"sentiment": NaN, "relevance": 1}', "end", "not_json"),
        ('{"sentiment": Infinity, "relevance": 1}', "end", "not_json"),
        ('{"sentiment": 0.5}', "end", "wrong_fields"),
        ('{"sentiment": 0.5, "relevance": 1, "confidence": 1}', "end", "wrong_fields"),
        ('{"sentiment": "0.5", "relevance": 1}', "end", "not_a_number"),
        ('{"sentiment": true, "relevance": 1}', "end", "not_a_number"),
        ('{"sentiment": null, "relevance": 1}', "end", "not_a_number"),
        ('{"sentiment": 1e400, "relevance": 1}', "end", "not_a_number"),
        ('{"sentiment": 1' + "0" * 400 + ', "relevance": 1}', "end", "not_a_number"),
        ('{"sentiment": 1.0000001, "relevance": 1}', "end", "out_of_bounds"),
        ('{"sentiment": 0.5, "relevance": ' + "[" * 100_000 + "]" * 100_000 + "}", "end", "not_json"),
        ('{"sentiment": -2, "relevance": 0.5}', "end", "out_of_bounds"),
    ],
)
def test_an_answer_is_refused_unless_it_is_exactly_the_declared_numbers(text, finish, reason):
    assert providers.parse(text, finish, FIELDS) == (None, reason)


def test_only_a_normal_truncated_or_refused_finish_is_understood():
    with pytest.raises(ValueError, match="Unknown finish 'stop'"):
        providers.parse('{"sentiment": 0.5, "relevance": 1}', "stop", FIELDS)


def test_an_answer_on_its_bounds_is_valid_and_whitespace_around_it_is_allowed():
    assert providers.parse('\n {"relevance": 0, "sentiment": -1}\n', "end", FIELDS) == ({"sentiment": -1.0, "relevance": 0.0}, None)
    assert providers.parse('{"sentiment": 1, "relevance": 1}', "end", FIELDS) == ({"sentiment": 1.0, "relevance": 1.0}, None)


def test_output_fields_need_ordered_finite_bounds_and_lower_case_names():
    for bad in ({"minimum": 1, "maximum": 1}, {"minimum": float("nan")}, {"maximum": float("inf")}, {"name": "Sentiment"}):
        with pytest.raises(ValidationError):
            OutputField.model_validate({**FIELDS[0].model_dump(), **bad})


def test_the_token_bound_counts_every_utf8_byte_sent_plus_framing_and_the_output_limit():
    asked = request(prompt="円" * 10)  # 30 bytes in UTF-8
    sent = asked.instructions + asked.prompt + json.dumps(asked.answer_schema)
    assert providers.token_bound(asked) == (len(sent.encode()) + 1024, 200)
    assert len(sent.encode()) == len(sent) + 20


def test_the_fixtures_token_counts_stay_within_the_bound_for_any_text(tmp_path):
    asked = request(prompt="円相場 " * 500, max_output_tokens=5)
    answer = '{"sentiment": 0.5, "relevance": 1}'  # 34 bytes: 9 of the fixture's tokens, over the limit of 5
    reply = fixture.complete(asked, answers_file(tmp_path, {sha256_hex(asked.prompt.encode()): answer}))
    input_bound, output_bound = providers.token_bound(asked)
    assert reply.input_tokens <= input_bound and reply.output_tokens == output_bound == 5
    # Like a real provider at its limit, the fixture stops there: the answer is truncated.
    assert (reply.finish, reply.text) == ("truncated", answer[:20])


def test_cost_is_at_prices_per_million_tokens():
    # 1,500 input tokens at $4 per million and 300 output tokens at $20 per million.
    assert providers.cost(1500, 300, 4.0, 20.0) == pytest.approx(0.006 + 0.006)
    assert providers.cost(1500, 300, 0.0, 0.0) == 0.0


def test_a_record_is_stored_once_read_only_and_never_replaced(tmp_path):
    first = record()
    key = responses.save(tmp_path, first)
    assert key == responses.request_sha256(first.request)
    assert responses.load(tmp_path, key) == first
    assert responses.save(tmp_path, first) == key  # storing the identical record again changes nothing
    assert not responses.record_path(tmp_path, key).stat().st_mode & 0o222
    with pytest.raises(responses.ResponseError, match="never replaced"):
        responses.save(tmp_path, record(text='{"sentiment": -0.5, "relevance": 1}'))
    assert responses.load(tmp_path, key) == first
    assert responses.load(tmp_path, responses.request_sha256(request(capture="other"))) is None
    assert [p.name for p in (tmp_path / "responses").iterdir()] == [f"{key}.json"]  # no temporary files left


def test_a_record_stored_by_another_writer_first_is_never_replaced(tmp_path, monkeypatch):
    # Another writer's record lands between this writer's check and its link: the link refuses it.
    theirs, ours = record(response_id="theirs"), record(response_id="ours")
    link = responses.os.link

    def link_after_another_writer(source, target):
        monkeypatch.setattr(responses.os, "link", link)
        responses.save(tmp_path, theirs)
        link(source, target)

    monkeypatch.setattr(responses.os, "link", link_after_another_writer)
    with pytest.raises(responses.ResponseError, match="never replaced"):
        responses.save(tmp_path, ours)
    assert responses.load(tmp_path, responses.request_sha256(request())) == theirs
    assert len(list((tmp_path / "responses").iterdir())) == 1


def test_a_corrupted_record_or_one_answering_another_request_is_refused(tmp_path):
    key = responses.save(tmp_path, record())
    path = responses.record_path(tmp_path, key)
    path.chmod(0o644)
    path.write_bytes(path.read_bytes()[:-5])
    with pytest.raises(responses.ResponseError, match="corrupted"):
        responses.load(tmp_path, key)
    path.write_bytes(record(request(capture="other")).model_dump_json().encode())
    with pytest.raises(responses.ResponseError, match="answers a different request"):
        responses.load(tmp_path, key)


def test_records_and_requests_refuse_impossible_values():
    for bad in ({"max_output_tokens": 0}, {"max_output_tokens": -5}, {"max_output_tokens": True}, {"max_output_tokens": 10.0}):
        with pytest.raises(ValidationError):
            request(**bad)
    for bad in ({"captured_at": datetime(2026, 11, 1, 12, 0)}, {"cost": float("nan")}, {"cost": -1.0}, {"input_tokens": -1}, {"latency_seconds": -0.1}):
        with pytest.raises(ValidationError):
            record(**bad)


def answers_file(tmp_path, answers: dict) -> str:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers))
    return str(path)


def test_the_fixture_answers_by_the_rendered_prompt_and_deterministically(tmp_path):
    asked = request()
    key = sha256_hex(asked.prompt.encode())
    endpoint = answers_file(tmp_path, {key: '{"sentiment": 0.5, "relevance": 1}'})
    reply = fixture.complete(asked, endpoint)
    assert reply == fixture.complete(asked, endpoint)
    assert (reply.text, reply.finish, reply.response_id) == ('{"sentiment": 0.5, "relevance": 1}', "end", f"fixture-{key[:16]}")
    assert reply.models == [f"fixture:{sha256_hex(open(endpoint, 'rb').read())}"]
    assert providers.parse(reply.text, reply.finish, FIELDS) == ({"sentiment": 0.5, "relevance": 1.0}, None)
    assert json.loads(reply.raw)["text"] == reply.text
    input_bound, output_bound = providers.token_bound(asked)
    assert reply.input_tokens <= input_bound and reply.output_tokens <= output_bound


def test_the_fixture_gives_no_answer_to_an_unknown_prompt_and_can_stop_early(tmp_path):
    truncated = request(prompt="long story")
    endpoint = answers_file(tmp_path, {sha256_hex(b"long story"): {"text": '{"sentiment": 0.', "finish": "truncated"}})
    unknown = fixture.complete(request(prompt="never seen"), endpoint)
    assert (unknown.text, unknown.finish) == (None, "end")
    assert providers.parse(unknown.text, unknown.finish, FIELDS) == (None, "no_answer")
    stopped = fixture.complete(truncated, endpoint)
    assert providers.parse(stopped.text, stopped.finish, FIELDS) == (None, "truncated")
    with pytest.raises(providers.ProviderError, match="needs its answers file"):
        fixture.complete(truncated, None)
    key = sha256_hex(b"long story")
    for malformed in ({"sentiment": 0.5}, 0.5, ["text"], {"text": "{}", "finish": "stop"}, {"text": 1, "finish": "refused"}):
        with pytest.raises(providers.ProviderError, match="must be text, or text with a finish of truncated or refused"):
            fixture.complete(truncated, answers_file(tmp_path, {key: malformed}))
    with pytest.raises(providers.ProviderError, match="must map prompt hashes to answers"):
        fixture.complete(truncated, answers_file(tmp_path, ["not", "a", "map"]))
    with pytest.raises(providers.ProviderError, match="cannot be read"):
        fixture.complete(truncated, str(tmp_path / "missing.json"))


def test_importing_the_provider_interface_imports_no_adapter():
    script = "import sys, qrs.providers, qrs.responses; print(sorted(m for m in sys.modules if m.startswith('qrs.providers.')))"
    assert subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout.strip() == "[]"
    assert providers.adapter("fixture") is fixture
