"""Extraction (Phase 4B workstream 3): documents to an immutable feature dataset through stored
responses. Replay never touches a provider; recording is explicit and bounded by its budget; rows are
usable only after their document plus its latency; invalid responses leave missing values with their
reasons; and the dataset carries its provenance, evidence class and rights."""

import base64
import io
import json
import subprocess
import sys
import zipfile

import pandas as pd
import pytest
from pydantic import ValidationError

from qrs import cli, compiler, datasets, export, extraction, importer, providers, responses
from qrs.contracts import Extraction
from qrs.datasets import DatasetError
from qrs.extraction import ExtractionError
from qrs.hashing import sha256_hex
from qrs.operators.data import as_of_values
from qrs.runtime import execute
from test_documents import DOCUMENTS_CSV, declaration
from test_observations import import_fixture, node, threshold_strategy

# The fixture's documents (tests/test_documents.py): A in two versions, B, and C, published between
# 14:30 UTC on 26 November 2024 and midnight on the 28th.
DOCUMENT_TEXTS = {
    ("A", "first"): "The committee kept rates at 4.5%.",
    ("A", "corrected"): "The committee kept rates at 4.25%.",
    ("B", "first"): 'Line one: 円相場は安定。\nLine two, quoted "as is".',
    ("C", "first"): "  Markets closed.\n",
}
TITLES = {"A": "Rates hold", "B": 'Yen, "steady"', "C": "Holiday"}
PUBLISHED = {
    ("A", "first"): "2024-11-26T14:30Z",
    ("A", "corrected"): "2024-11-26T20:00Z",
    ("B", "first"): "2024-11-26T19:00Z",
    ("C", "first"): "2024-11-28T00:00Z",
}
ANSWERS = {
    ("A", "first"): '{"sentiment": 0.5}',
    ("A", "corrected"): '{"sentiment": 0.25}',
    ("B", "first"): '{"sentiment": -0.5}',
    ("C", "first"): '{"sentiment": 0}',
}


def documents(tmp_path, **changes) -> str:
    return importer.import_documents(tmp_path, DOCUMENTS_CSV, declaration(**changes))


def spec(documents_id: str, endpoint: str | None, **changes) -> Extraction:
    declared = {
        "name": "Fixture sentiment",
        "documents": documents_id,
        "provider": "fixture",
        "model": "fixture",
        "endpoint": endpoint,
        "instructions": "Rate the story's tone towards the market. Answer with JSON only.",
        "prompt": "Headline: {title}\n\n{text}",
        "fields": [{"name": "sentiment", "description": "Tone towards the market.", "unit": "score", "minimum": -1, "maximum": 1}],
        "max_output_tokens": 50,
        "latency_seconds": 300,
        "capture": "2026-11-01",
        "budget": {"max_requests": 10, "max_cost": 0, "input_price": 0, "output_price": 0},
        "redistributable": True,
    }
    return Extraction.model_validate({**declared, **changes})


def answers_file(tmp_path, answers: dict, prompt: str = "Headline: {title}\n\n{text}") -> str:
    """The fixture provider's answers for these document versions, keyed by their rendered prompts."""
    keyed = {sha256_hex(extraction.render(prompt, TITLES[d], DOCUMENT_TEXTS[d, r]).encode()): answer for (d, r), answer in answers.items()}
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(keyed))
    return str(path)


def recorded(tmp_path, answers=ANSWERS, **changes) -> tuple[Extraction, str]:
    """A recorded extraction, and its dataset ID."""
    extracted = spec(documents(tmp_path), answers_file(tmp_path, answers), **changes)
    return extracted, extraction.extract(tmp_path, extracted, record=True, report=lambda _: None)


@pytest.fixture
def no_provider(monkeypatch):
    """Fail the test if anything asks for a provider adapter."""

    def refuse(name):
        raise AssertionError(f"the {name} adapter was asked for")

    monkeypatch.setattr(providers, "adapter", refuse)


def test_replay_without_stored_responses_fails_naming_the_documents_and_publishes_nothing(tmp_path, no_provider):
    extracted = spec(documents(tmp_path), answers_file(tmp_path, ANSWERS))
    with pytest.raises(ExtractionError) as refused:
        extraction.extract(tmp_path, extracted)
    assert str(refused.value) == (
        "4 document version(s) have no stored response, so the dataset cannot be built: "
        "A (first), B (first), A (corrected), C (first). Record them with --record."
    )
    assert [m.kind for m in datasets.list_manifests(tmp_path).values()] == ["qrs/documents"]


def test_a_recording_replays_to_the_same_dataset_with_no_provider_imported_or_network(tmp_path):
    extracted, recorded_id = recorded(tmp_path)
    (tmp_path / "extraction.json").write_text(extracted.model_dump_json())
    # Replay in a fresh process in which importing any adapter, or opening a socket, fails.
    script = (
        "import sys, socket\n"
        "sys.modules['qrs.providers.fixture'] = None\n"
        "def refuse(*a, **k): raise OSError('network used')\n"
        "socket.socket.connect = refuse; socket.socket.connect_ex = refuse; socket.create_connection = refuse; socket.getaddrinfo = refuse\n"
        "from qrs import cli\n"
        f"sys.exit(cli.main(['extract', {str(tmp_path / 'extraction.json')!r}, '--home', {str(tmp_path)!r}]))\n"
    )
    replayed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert replayed.returncode == 0, replayed.stderr
    assert replayed.stdout.splitlines()[0] == f"dataset  {recorded_id}"


def test_stored_responses_are_reused_so_recording_again_asks_nothing(tmp_path, monkeypatch):
    extracted, first = recorded(tmp_path)
    calls = []
    monkeypatch.setattr(providers, "adapter", lambda name: calls.append(name))
    reports = []
    assert extraction.extract(tmp_path, extracted, record=True, report=reports.append) == first
    assert calls == [] and reports == ["Every response is stored: nothing to record."]
    assert extraction.extract(tmp_path, extracted) == first


def test_feature_rows_hold_their_values_availability_and_document(tmp_path):
    extracted, dataset_id = recorded(tmp_path)
    manifest = datasets.read_manifest(tmp_path, dataset_id)
    rows = datasets.load_observations(tmp_path, dataset_id)
    assert list(rows.columns) == ["event", "published", "revision", "estimated", "document", "sentiment"]
    assert datasets.value_columns(rows) == ["sentiment"]
    by_version = {(d, r): row for d, r, row in zip(rows["document"], rows["revision"], rows.itertuples(), strict=True)}
    for version, published in PUBLISHED.items():
        # The fixture answers in well under the declared 300 seconds, so the declared latency applies.
        assert by_version[version].published == pd.Timestamp(published) + pd.Timedelta(seconds=300)
        assert by_version[version].sentiment == json.loads(ANSWERS[version])["sentiment"]
    assert list(rows["event"]) == list(pd.to_datetime(["2024-11-26", "2024-11-26", "2024-11-26", "2024-11-28"]))
    # Captured in 2026, long after the documents could be used: retrospective, so exploratory.
    assert rows["estimated"].all()
    assert (manifest.evidence_class, manifest.values, manifest.rows, manifest.estimated_rows) == ("exploratory", {"sentiment": "score"}, 4, 4)
    assert manifest.estimates.startswith("4 row(s) are usable from their document's publication plus the larger of the declared (300 s) and measured latency")


def test_a_feature_dataset_carries_its_extraction_responses_and_provenance(tmp_path):
    extracted, dataset_id = recorded(tmp_path)
    manifest = datasets.read_manifest(tmp_path, dataset_id)
    d = manifest.derivation
    folder = datasets.directory(tmp_path, dataset_id)
    assert json.loads((folder / "extraction.json").read_bytes()) == json.loads(extracted.model_dump_json())
    lines = [json.loads(line) for line in (folder / "responses.jsonl").read_text().splitlines()]
    assert [(line["document"], line["revision"], line["status"]) for line in lines] == [
        ("A", "first", "valid"),
        ("B", "first", "valid"),
        ("A", "corrected", "valid"),
        ("C", "first", "valid"),
    ]
    # Each document's own publication time and URL, and the documents' manifest (source, file hash,
    # columns; no text), whose hash is the documents ID.
    assert [pd.Timestamp(line["published"]) for line in lines] == [pd.Timestamp(PUBLISHED[d, r]) for d, r in ORDER]
    assert [line["url"] for line in lines] == ["https://example.com/a", "https://example.com/b", "https://example.com/a", ""]
    documents_manifest = (folder / "documents.json").read_bytes()
    assert "sha256:" + sha256_hex(documents_manifest) == extracted.documents
    assert json.loads(documents_manifest)["source_sha256"] == sha256_hex(DOCUMENTS_CSV)
    b = lines[1]["record"]
    assert b["request"]["prompt"] == f"Headline: {TITLES['B']}\n\n{DOCUMENT_TEXTS['B', 'first']}"
    assert lines[1]["request_sha256"] == responses.request_sha256(extraction.request_for(extracted, TITLES["B"], DOCUMENT_TEXTS["B", "first"]))
    assert base64.b64decode(b["raw_base64"]) and b["text"] == ANSWERS["B", "first"]
    assert (d.documents, d.provider, d.model, d.hosted, d.capture, d.requests) == (extracted.documents, "fixture", "fixture", False, "2026-11-01", 4)
    assert d.reported_models == [f"fixture:{sha256_hex(open(extracted.endpoint, 'rb').read())}"]
    assert (d.extraction_sha256, d.responses_sha256) == (
        sha256_hex((folder / "extraction.json").read_bytes()),
        sha256_hex((folder / "responses.jsonl").read_bytes()),
    )
    assert (d.retrospective_rows, d.duplicates, d.invalid_rows, d.cost) == (4, 0, {}, 0.0)
    assert d.input_tokens == sum(line["record"]["input_tokens"] for line in lines) > 0
    assert manifest.source == "Fixture news, extracted by fixture fixture"
    for kept in (manifest.source_sha256, manifest.columns, manifest.event_format):
        assert kept is None


def test_a_changed_or_missing_provenance_file_is_refused(tmp_path):
    _, dataset_id = recorded(tmp_path)
    folder = datasets.directory(tmp_path, dataset_id)
    for name in ("responses.jsonl", "extraction.json", "documents.json"):
        path = folder / name
        original = path.read_bytes()
        path.chmod(0o644)
        path.write_bytes(original + b" ")
        with pytest.raises(DatasetError, match=f"{name} is missing or was changed after publication"):
            datasets.load_observations(tmp_path, dataset_id)
        path.unlink()
        with pytest.raises(DatasetError, match=f"{name} is missing"):
            datasets.load_observations(tmp_path, dataset_id)
        path.write_bytes(original)


def test_a_measured_latency_longer_than_the_declared_one_delays_each_row(tmp_path, monkeypatch):
    clock = iter(range(0, 10_000, 1000))  # each call to the provider takes 1,000 seconds
    monkeypatch.setattr(extraction.time, "monotonic", lambda: next(clock))
    _, dataset_id = recorded(tmp_path)
    rows = datasets.load_observations(tmp_path, dataset_id)
    assert list(rows["published"]) == sorted(pd.Timestamp(p) + pd.Timedelta(seconds=1000) for p in PUBLISHED.values())


# Rows in publication order: A (first), B (first), A (corrected), C (first).
ORDER = [("A", "first"), ("B", "first"), ("A", "corrected"), ("C", "first")]


def capture_after_publication(monkeypatch, seconds: list[float]) -> None:
    """Record each row's response `seconds` after its document's publication, in row order."""
    times = iter(pd.Timestamp(PUBLISHED[version]) + pd.Timedelta(seconds=s) for version, s in zip(ORDER, seconds, strict=True))
    monkeypatch.setattr(extraction, "now", lambda: next(times).to_pydatetime())


@pytest.mark.parametrize(
    ("documents_class", "seconds", "expected", "retrospective"),
    [
        # Captured within the declared 300 seconds, or exactly at its end: in time to be used.
        ("point_in_time", [100, 100, 100, 100], "point_in_time", 0),
        ("point_in_time", [300, 300, 300, 300], "point_in_time", 0),
        ("retrospective", [100, 100, 100, 100], "retrospective", 0),
        ("synthetic", [100, 100, 100, 100], "synthetic", 0),
        ("exploratory", [100, 100, 100, 100], "exploratory", 0),
        # One response a second too late makes the whole dataset exploratory.
        ("point_in_time", [100, 301, 100, 100], "exploratory", 1),
        ("retrospective", [100, 100, 100, 301], "exploratory", 1),
        # Captured years later: retrospective AI outputs, exploratory on real documents only.
        ("point_in_time", [6e7] * 4, "exploratory", 4),
        ("synthetic", [6e7] * 4, "synthetic", 4),
    ],
)
def test_only_responses_captured_by_the_time_they_could_be_used_lift_the_exploratory_class(
    tmp_path, monkeypatch, documents_class, seconds, expected, retrospective
):
    capture_after_publication(monkeypatch, seconds)
    extracted = spec(documents(tmp_path, evidence_class=documents_class), answers_file(tmp_path, ANSWERS))
    dataset_id = extraction.extract(tmp_path, extracted, record=True, report=lambda _: None)
    manifest = datasets.read_manifest(tmp_path, dataset_id)
    assert (manifest.evidence_class, manifest.derivation.retrospective_rows, manifest.estimated_rows) == (expected, retrospective, retrospective)
    assert (manifest.estimates is None) == (retrospective == 0)
    late = {version for version, s in zip(ORDER, seconds, strict=True) if s > 300}
    rows = datasets.load_observations(tmp_path, dataset_id)
    assert {(d, r) for d, r, e in zip(rows["document"], rows["revision"], rows["estimated"], strict=True) if e} == late


def test_a_document_with_an_estimated_timestamp_gives_an_estimated_row(tmp_path, monkeypatch):
    capture_after_publication(monkeypatch, [100, 100, 100, 100])
    csv = DOCUMENTS_CSV.replace(b",Day\n", b",Day,Estimated\n").replace(b"2024-11-26\n", b"2024-11-26,false\n").replace(b"2024-11-28\n", b"2024-11-28,true\n")
    columns = {**declaration().columns.model_dump(), "estimated": "Estimated"}
    documents_id = importer.import_documents(tmp_path, csv, declaration(columns=columns, estimates="first seen by a crawler"))
    manifest = datasets.read_manifest(
        tmp_path, extraction.extract(tmp_path, spec(documents_id, answers_file(tmp_path, ANSWERS)), record=True, report=lambda _: None)
    )
    assert (manifest.evidence_class, manifest.estimated_rows, manifest.derivation.retrospective_rows) == ("retrospective", 1, 0)
    assert manifest.estimates == "the documents' own timestamps were estimated: first seen by a crawler."


def test_a_stored_response_captured_before_its_document_was_published_is_refused(tmp_path, monkeypatch):
    extracted, _ = recorded(tmp_path)
    key = responses.request_sha256(extraction.request_for(extracted, TITLES["C"], DOCUMENT_TEXTS["C", "first"]))
    path = responses.record_path(tmp_path, key)
    altered = json.loads(path.read_text())
    altered["captured_at"] = "2024-11-27T00:00:00Z"  # a day before C was published
    path.chmod(0o644)
    path.write_text(json.dumps(altered))
    with pytest.raises(ExtractionError, match=r"Stored responses for C \(first\) were captured before their documents were published"):
        extraction.extract(tmp_path, extracted.model_copy(update={"name": "again"}))


def test_a_response_shared_with_a_version_dropped_as_a_copy_is_not_taken_for_an_altered_store(tmp_path, monkeypatch):
    # Y's first story was recorded 10 seconds after it came out, while it stood alone. X, which had
    # published the same text earlier under another headline, came in later: deduplication now drops
    # Y's first version, but Y's third reverts to it and shares its response, recorded before the
    # third was published.
    header = DOCUMENTS_CSV.splitlines(keepends=True)[0]
    y_first = b"Y,2024-11-27T09:30:00+00:00,first,Rates hold,Rates are unchanged.,,2024-11-27\n"
    later = (
        b"X,2024-11-27T09:00:00+00:00,first,Rates steady,Rates are unchanged.,,2024-11-27\n"
        + y_first
        + b"Y,2024-11-27T10:00:00+00:00,second,Rates hold,Rates are unchanged for now.,,2024-11-27\n"
        b"Y,2024-11-27T11:00:00+00:00,third,Rates hold,Rates are unchanged.,,2024-11-27\n"
    )
    stories = [("Rates hold", "Rates are unchanged."), ("Rates steady", "Rates are unchanged."), ("Rates hold", "Rates are unchanged for now.")]
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({sha256_hex(extraction.render("Headline: {title}\n\n{text}", t, x).encode()): '{"sentiment": 0.1}' for t, x in stories}))
    monkeypatch.setattr(extraction, "now", lambda: pd.Timestamp("2024-11-27T09:30:10Z").to_pydatetime())
    alone = importer.import_documents(tmp_path, header + y_first, declaration(evidence_class="point_in_time"))
    extraction.extract(tmp_path, spec(alone, str(answers), dedupe=True), record=True, report=lambda _: None)
    monkeypatch.setattr(extraction, "now", lambda: pd.Timestamp("2024-11-27T12:00:00Z").to_pydatetime())
    together = importer.import_documents(tmp_path, header + later, declaration(evidence_class="point_in_time"))
    dataset_id = extraction.extract(tmp_path, spec(together, str(answers), dedupe=True), record=True, report=lambda _: None)
    rows = datasets.load_observations(tmp_path, dataset_id).set_index(["document", "revision"])
    assert sorted(rows.index) == [("X", "first"), ("Y", "second"), ("Y", "third")]
    assert not rows.loc[("Y", "third"), "estimated"]  # its response came before it could be used


def test_capture_times_with_any_offset_are_read_as_utc(tmp_path):
    extracted, dataset_id = recorded(tmp_path)
    key = responses.request_sha256(extraction.request_for(extracted, TITLES["C"], DOCUMENT_TEXTS["C", "first"]))
    path = responses.record_path(tmp_path, key)
    record = json.loads(path.read_text())
    record["captured_at"] = pd.Timestamp(record["captured_at"]).tz_convert("Europe/Paris").isoformat()  # the same instant
    path.chmod(0o644)
    path.write_text(json.dumps(record))
    replayed = extraction.extract(tmp_path, extracted.model_copy(update={"name": "again"}))
    columns = ["published", "estimated"]
    assert datasets.load_observations(tmp_path, replayed)[columns].equals(datasets.load_observations(tmp_path, dataset_id)[columns])


@pytest.mark.parametrize("dedupe", [False, True])
def test_a_response_shared_with_a_later_identical_version_is_not_taken_for_an_altered_store(tmp_path, monkeypatch, dedupe):
    # D copies A's first story, and A later reverts to it: both render A's first request, answered
    # 10 seconds after A's publication, so before theirs. Each is usable from its own publication.
    copy = b"D,2024-11-27T09:00:00-05:00,first,Rates hold,The committee kept rates at 4.5%.,,2024-11-27\n"
    revert = b"A,2024-11-27T10:00:00-05:00,reverted,Rates hold,The committee kept rates at 4.5%.,,2024-11-27\n"
    documents_id = importer.import_documents(tmp_path, DOCUMENTS_CSV + copy + revert, declaration(evidence_class="point_in_time"))
    capture_after_publication(monkeypatch, [10, 10, 10, 10])  # the four distinct requests
    dataset_id = extraction.extract(tmp_path, spec(documents_id, answers_file(tmp_path, ANSWERS), dedupe=dedupe), record=True, report=lambda _: None)
    manifest = datasets.read_manifest(tmp_path, dataset_id)
    assert (manifest.evidence_class, manifest.derivation.retrospective_rows, manifest.rows) == ("point_in_time", 0, 5 if dedupe else 6)
    rows = datasets.load_observations(tmp_path, dataset_id).set_index(["document", "revision"])
    assert rows.loc[("A", "reverted"), "published"] == pd.Timestamp("2024-11-27T15:00Z") + pd.Timedelta(seconds=300)


def test_a_correction_is_usable_after_the_version_it_corrects_however_slowly_that_was_answered(tmp_path, monkeypatch):
    # A's first version takes six hours to answer; everything else one second. Without carrying the
    # slower latency forward, the correction would be usable first and the original would mask it.
    clock = iter([0, 21600, 21600, 21601, 21601, 21602, 21602, 21603])
    monkeypatch.setattr(extraction.time, "monotonic", lambda: next(clock))
    _, dataset_id = recorded(tmp_path)
    rows = datasets.load_observations(tmp_path, dataset_id).set_index(["document", "revision"])
    assert rows.loc[("A", "first"), "published"] == pd.Timestamp("2024-11-26T14:30Z") + pd.Timedelta(hours=6)
    assert rows.loc[("A", "corrected"), "published"] == pd.Timestamp("2024-11-26T20:00Z") + pd.Timedelta(hours=6)
    assert rows.loc[("B", "first"), "published"] == pd.Timestamp("2024-11-26T19:00Z") + pd.Timedelta(seconds=300)


def test_at_each_close_a_strategy_sees_only_rows_usable_by_then(tmp_path):
    # NYSE closes at 21:00 UTC. With a latency of an hour, A's correction (published at 20:00) is
    # usable exactly at the close of the 26th; a second more, and B (usable at 20:00:01) is the
    # latest by then instead. Either way nothing is known at the close of the 25th.
    bars_id, _ = import_fixture(tmp_path)
    closes = datasets.session_closes(datasets.load(tmp_path, bars_id))
    for latency, at_26th in ((3600, 0.25), (3601, -0.5)):
        _, features_id = recorded(tmp_path, latency_seconds=latency, capture=f"latency {latency}")
        rows = datasets.load_observations(tmp_path, features_id)
        values = as_of_values(rows, "sentiment", closes, pd.Timedelta(days=7))
        assert pd.isna(values.iloc[0]) and values.iloc[1] == at_26th
        # The 27th and later: A's correction stays the latest of the 26th's events until C, on the 28th.
        assert list(values.iloc[2:4]) == [0.25, 0.0]


def test_invalid_responses_leave_missing_values_with_their_reasons(tmp_path):
    answers = {
        ("A", "first"): "Positive, about 0.5.",
        ("A", "corrected"): '{"sentiment": 2}',
        ("B", "first"): {"text": '{"sentiment": 0.', "finish": "truncated"},
        # C has no answer at all.
    }
    _, dataset_id = recorded(tmp_path, answers)
    rows = datasets.load_observations(tmp_path, dataset_id)
    assert rows["sentiment"].isna().all()
    manifest = datasets.read_manifest(tmp_path, dataset_id)
    assert manifest.derivation.invalid_rows == {"no_answer": 1, "not_json": 1, "out_of_bounds": 1, "truncated": 1}
    lines = [json.loads(line) for line in (datasets.directory(tmp_path, dataset_id) / "responses.jsonl").read_text().splitlines()]
    assert [line["status"] for line in lines] == ["not_json", "truncated", "out_of_bounds", "no_answer"]
    others = {
        ("A", "first"): '{"sentiment": 0.5, "relevance": 1}',
        ("A", "corrected"): '{"sentiment": "0.25"}',
        ("B", "first"): {"text": None, "finish": "refused"},
        ("C", "first"): '{"sentiment": 0}',
    }
    _, dataset_id = recorded(tmp_path, others, capture="others")
    assert datasets.read_manifest(tmp_path, dataset_id).derivation.invalid_rows == {"not_a_number": 1, "refused": 1, "wrong_fields": 1}
    assert datasets.load_observations(tmp_path, dataset_id)["sentiment"].isna().tolist() == [True, True, True, False]  # C's 0 is valid


def test_deduplication_drops_later_copies_from_other_documents_but_never_a_documents_own_revision(tmp_path):
    copy = b'D,2024-11-27T09:00:00-05:00,first,Rates hold,"The committee  kept rates\nat 4.5%.",,2024-11-27\n'
    revert = b'A,2024-11-27T10:00:00-05:00,reverted,Rates hold,"The committee kept rates at 4.5%.",,2024-11-27\n'
    documents_id = importer.import_documents(tmp_path, DOCUMENTS_CSV + copy + revert, declaration())
    extracted = spec(documents_id, answers_file(tmp_path, ANSWERS), dedupe=True)
    dataset_id = extraction.extract(tmp_path, extracted, record=True, report=lambda _: None)
    manifest = datasets.read_manifest(tmp_path, dataset_id)
    assert (manifest.rows, manifest.derivation.duplicates) == (5, 1)
    kept = datasets.load_observations(tmp_path, dataset_id)
    # D, a copy of A's first text from another document, is dropped; A's own reverted version is kept.
    assert sorted(zip(kept["document"], kept["revision"], strict=True)) == [
        ("A", "corrected"),
        ("A", "first"),
        ("A", "reverted"),
        ("B", "first"),
        ("C", "first"),
    ]
    without = extraction.extract(tmp_path, extracted.model_copy(update={"dedupe": False}), record=True, report=lambda _: None)
    assert datasets.read_manifest(tmp_path, without).derivation.invalid_rows == {"no_answer": 1}  # D, now extracted, has no answer


def test_a_kept_documents_later_version_is_kept_even_when_it_copies_another(tmp_path):
    # W reports A's story in its own words, then corrects itself to A's exact text: dropping the
    # correction would leave W's superseded version counting.
    w = (
        b'W,2024-11-27T09:00:00-05:00,first,Rates hold,"Rates were held, at 4.5%.",,2024-11-27\n'
        b"W,2024-11-27T10:00:00-05:00,corrected,Rates hold,The committee kept rates at 4.5%.,,2024-11-27\n"
    )
    documents_id = importer.import_documents(tmp_path, DOCUMENTS_CSV + w, declaration())
    dataset_id = extraction.extract(tmp_path, spec(documents_id, answers_file(tmp_path, ANSWERS), dedupe=True), record=True, report=lambda _: None)
    rows = datasets.load_observations(tmp_path, dataset_id)
    assert datasets.read_manifest(tmp_path, dataset_id).derivation.duplicates == 0
    assert ("W", "corrected") in set(zip(rows["document"], rows["revision"], strict=True))


def test_the_prompt_is_rendered_once_so_text_that_looks_like_a_placeholder_stays_as_it_is():
    assert extraction.render("{title}: {text}", "{text}", "a {title} b") == "{text}: a {title} b"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"prompt": "{title} on {date}: {text}"}, r"may hold only \{title\} and \{text\}, not \{date\}"),
        ({"prompt": "Headline: {title}"}, r"must hold \{text\}"),
        ({"fields": [{"name": "document", "description": "d", "unit": "u", "minimum": 0, "maximum": 1}]}, "cannot be document"),
        ({"fields": [{"name": "x", "description": "d", "unit": "u", "minimum": 0, "maximum": 1}] * 2}, "must be unique"),
        ({"fields": [{"name": "x", "description": "d", "unit": "u", "minimum": 1, "maximum": 1}]}, "must be below maximum"),
        ({"provider": "openai"}, "Input should be 'fixture', 'ollama' or 'anthropic'"),
        ({"budget": {"max_requests": 1, "max_cost": -1, "input_price": 0, "output_price": 0}}, "greater than or equal to 0"),
        ({"latency_seconds": -1}, "greater than or equal to 0"),
        ({"options": {"temperature": float("nan")}}, "options must be finite: temperature"),
        ({"unknown": 1}, "Extra inputs are not permitted"),
    ],
)
def test_an_extraction_that_breaks_a_rule_is_refused_as_it_is_read(tmp_path, change, message):
    with pytest.raises(ValidationError, match=message):
        spec("sha256:" + "0" * 64, "answers.json", **change)


@pytest.mark.parametrize(
    ("registered", "change", "message"),
    [
        ({}, {"options": {"seed": 1}}, "fixture accepts no option 'seed'"),
        ({"options": {"seed": int, "temperature": float}}, {"options": {"seed": 1.5}}, "Option 'seed' must be a int"),
        ({"options": {"seed": int, "temperature": float}}, {"options": {"seed": True}}, "Option 'seed' must be a int"),
        ({"options": {"stream": bool}}, {"options": {"stream": 1}}, "Option 'stream' must be a bool"),
        ({"options": {"temperature": float}}, {"options": {"temperature": True}}, "Option 'temperature' must be a float"),
        ({"max_output_tokens": 49}, {}, "max_output_tokens 50 is over fixture's limit of 49"),
        ({}, {"endpoint": None}, "fixture needs an endpoint"),
        ({"hosted": True}, {"endpoint": None}, "may not be sent to a hosted provider"),
        ({"hosted": True}, {}, "its endpoint is fixed"),
    ],
)
def test_provider_rules_are_refused_before_any_call_in_replay_and_recording(tmp_path, monkeypatch, no_provider, registered, change, message):
    described = providers.PROVIDERS["fixture"]
    monkeypatch.setitem(providers.PROVIDERS, "fixture", providers.Provider(**{**described.__dict__, **registered}))
    change = {"endpoint": "answers.json", **change}
    extracted = spec(documents(tmp_path), change.pop("endpoint"), **change)
    for record in (False, True):
        with pytest.raises(ExtractionError, match=message):
            extraction.extract(tmp_path, extracted, record=record)


def test_an_integer_is_a_float_option(tmp_path, monkeypatch, no_provider):
    described = providers.PROVIDERS["fixture"]
    monkeypatch.setitem(providers.PROVIDERS, "fixture", providers.Provider(**{**described.__dict__, "options": {"temperature": float}}))
    with pytest.raises(ExtractionError, match="have no stored response"):  # past every rule, to replay
        extraction.extract(tmp_path, spec(documents(tmp_path), "answers.json", options={"temperature": 1}))


def test_hosted_extraction_of_documents_that_allow_it_passes_the_consent_rule(tmp_path, monkeypatch, no_provider):
    described = providers.PROVIDERS["fixture"]
    monkeypatch.setitem(providers.PROVIDERS, "fixture", providers.Provider(**{**described.__dict__, "hosted": True}))
    extracted = spec(documents(tmp_path, hosted_processing=True), None)
    with pytest.raises(ExtractionError, match="have no stored response"):  # past every rule, to replay
        extraction.extract(tmp_path, extracted)


def test_the_documents_must_be_a_documents_dataset_unchanged_since_publication(tmp_path):
    bars_id, observations_id = import_fixture(tmp_path)
    with pytest.raises(DatasetError, match="holds observations, not documents"):
        extraction.extract(tmp_path, spec(observations_id, "answers.json"))
    documents_id = documents(tmp_path)
    data = datasets.directory(tmp_path, documents_id) / "documents.parquet"
    data.chmod(0o644)
    data.write_bytes(data.read_bytes() + b"\0")
    with pytest.raises(DatasetError, match="changed after publication"):
        extraction.extract(tmp_path, spec(documents_id, "answers.json"))


class Scripted:
    """A provider adapter that answers from a script of replies or errors, counting calls."""

    def __init__(self, script):
        self.script, self.calls = list(script), 0

    def check(self, endpoint, model):
        pass

    def complete(self, request, endpoint):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def reply(text='{"sentiment": 0.5}', input_tokens=100, output_tokens=10) -> providers.Reply:
    return providers.Reply(
        raw=text.encode(), text=text, response_id="r", models=["m"], finish="end", stop_reason="end", input_tokens=input_tokens, output_tokens=output_tokens
    )


PRICED = {"max_requests": 10, "max_cost": 1.0, "input_price": 4.0, "output_price": 20.0}


def test_a_recording_over_its_budget_is_refused_before_any_call(tmp_path, monkeypatch, no_provider):
    extracted = spec(documents(tmp_path), "answers.json", budget={**PRICED, "max_requests": 3})
    with pytest.raises(ExtractionError, match="The extraction needs 4 requests, over the budget's 3. Nothing was asked."):
        extraction.extract(tmp_path, extracted, record=True)
    # Each request's bound: its UTF-8 bytes plus 1,024 framing tokens in, 50 out.
    bound = sum(
        providers.cost(*providers.token_bound(extraction.request_for(extracted, TITLES[d], text)), 4.0, 20.0) for (d, _), text in DOCUMENT_TEXTS.items()
    )
    tight = spec(documents(tmp_path), "answers.json", budget={**PRICED, "max_cost": bound * 0.999})
    with pytest.raises(ExtractionError, match=rf"could cost up to {bound:.6g} \(0 already spent\), over the budget's"):
        extraction.extract(tmp_path, tight, record=True)
    assert not (tmp_path / "responses").exists()


def test_a_recording_stopped_by_an_error_publishes_nothing_and_resumes_where_it_stopped(tmp_path, monkeypatch):
    extracted = spec(documents(tmp_path), "answers.json", budget=PRICED)
    failing = Scripted([reply(), providers.ProviderError("connection reset")])
    monkeypatch.setattr(providers, "adapter", lambda name: failing)
    with pytest.raises(ExtractionError, match="Recording stopped: connection reset. Stored responses are kept"):
        extraction.extract(tmp_path, extracted, record=True, report=lambda _: None)
    assert len(list((tmp_path / "responses").glob("*.json"))) == 1
    assert [m.kind for m in datasets.list_manifests(tmp_path).values()] == ["qrs/documents"]
    resumed = Scripted([reply(), reply(), reply()])
    monkeypatch.setattr(providers, "adapter", lambda name: resumed)
    dataset_id = extraction.extract(tmp_path, extracted, record=True, report=lambda _: None)
    assert resumed.calls == 3
    # Each response cost 100 input tokens at $4 and 10 output tokens at $20 per million.
    assert datasets.read_manifest(tmp_path, dataset_id).derivation.cost == pytest.approx(4 * (100 * 4 + 10 * 20) / 1e6)


def test_the_request_budget_counts_the_responses_already_stored(tmp_path, monkeypatch):
    extracted = spec(documents(tmp_path), "answers.json", budget=PRICED)
    monkeypatch.setattr(providers, "adapter", lambda name: Scripted([reply(), providers.ProviderError("connection reset")]))
    with pytest.raises(ExtractionError, match="Recording stopped"):
        extraction.extract(tmp_path, extracted, record=True, report=lambda _: None)
    # One of the four is stored; the three missing fit a budget of 3 requests, but the extraction does not.
    tight = extracted.model_copy(update={"budget": extracted.budget.model_copy(update={"max_requests": 3})})
    with pytest.raises(ExtractionError, match="needs 4 requests, over the budget's 3"):
        extraction.extract(tmp_path, tight, record=True)


@pytest.mark.parametrize("tokens", [{"input_tokens": 100_000}, {"output_tokens": 51}])
def test_either_token_count_over_its_bound_stops_the_recording(tmp_path, monkeypatch, tokens):
    extracted = spec(documents(tmp_path), "answers.json", budget=PRICED)
    monkeypatch.setattr(providers, "adapter", lambda name: Scripted([reply(**tokens), reply(), reply(), reply()]))
    with pytest.raises(ExtractionError, match="reported more tokens than its bound"):
        extraction.extract(tmp_path, extracted, record=True, report=lambda _: None)


def test_a_response_reporting_more_tokens_than_its_bound_stops_the_recording_and_counts_against_the_budget(tmp_path, monkeypatch):
    extracted = spec(documents(tmp_path), "answers.json", budget=PRICED)
    monkeypatch.setattr(providers, "adapter", lambda name: Scripted([reply(input_tokens=100_000, output_tokens=51)]))
    with pytest.raises(ExtractionError, match="reported more tokens than its bound"):
        extraction.extract(tmp_path, extracted, record=True, report=lambda _: None)
    assert len(list((tmp_path / "responses").glob("*.json"))) == 1  # it was paid for, so it is kept
    assert [m.kind for m in datasets.list_manifests(tmp_path).values()] == ["qrs/documents"]
    # It cost 0.40102 at these prices, so the rest of the extraction no longer fits a budget of 0.4.
    monkeypatch.setattr(providers, "adapter", lambda name: Scripted([reply(), reply(), reply()]))
    with pytest.raises(ExtractionError, match=r"\(0.40102 already spent\), over the budget's 0.4. Nothing was asked."):
        extraction.extract(tmp_path, extracted.model_copy(update={"budget": extracted.budget.model_copy(update={"max_cost": 0.4})}), record=True)


def feature_strategy(bars_id: str, features_id: str, column: str = "sentiment"):
    """Long while the latest story's sentiment, as of each close, is above zero."""
    sentiment = node("rate", "qrs.as_of", dataset_id=features_id, column=column, max_age_days=7)
    return threshold_strategy(bars_id, sentiment, "series", 0.0)


def test_a_strategy_reads_features_as_of_each_close_and_shows_their_evidence_class(tmp_path):
    bars_id, _ = import_fixture(tmp_path)
    _, features_id = recorded(tmp_path)
    strategy = tmp_path / "strategy.py"
    strategy.write_text(compiler.compile_for_data(feature_strategy(bars_id, features_id), tmp_path))
    record = execute(strategy, tmp_path, tmp_path / "out")
    assert record["datasets"][features_id]["evidence_class"] == "exploratory"
    strategy.write_text(compiler.compile_for_data(feature_strategy(bars_id, features_id, column="document"), tmp_path))
    with pytest.raises(Exception, match="no column 'document'; they have \\['sentiment'\\]"):
        execute(strategy, tmp_path, tmp_path / "again")


def test_exports_include_features_and_their_responses_only_when_both_extraction_and_documents_allow(tmp_path):
    bars_id, _ = import_fixture(tmp_path)
    allowed = recorded(tmp_path)[1]
    bundle = zipfile.ZipFile(io.BytesIO(export.build(feature_strategy(bars_id, allowed), tmp_path)))
    folder = allowed.removeprefix("sha256:")
    assert {f"data/datasets/{folder}/{name}" for name in ("manifest.json", "observations.parquet", "extraction.json", "responses.jsonl")} <= set(
        bundle.namelist()
    )
    for documents_allow, extraction_allows in ((False, True), (True, False)):
        documents_id = importer.import_documents(tmp_path, DOCUMENTS_CSV, declaration(redistributable=documents_allow))
        extracted = spec(documents_id, answers_file(tmp_path, ANSWERS), redistributable=extraction_allows)
        features_id = extraction.extract(tmp_path, extracted, record=True, report=lambda _: None)
        assert not datasets.read_manifest(tmp_path, features_id).redistributable
        names = zipfile.ZipFile(io.BytesIO(export.build(feature_strategy(bars_id, features_id), tmp_path))).namelist()
        assert not any(features_id.removeprefix("sha256:") in name for name in names)


def test_qrs_extract_replays_by_default_and_records_only_when_asked(tmp_path, capsys):
    extracted = spec(documents(tmp_path), answers_file(tmp_path, ANSWERS))
    path = tmp_path / "extraction.json"
    path.write_text(extracted.model_dump_json())
    assert cli.main(["extract", str(path), "--home", str(tmp_path)]) == 1
    assert "have no stored response" in capsys.readouterr().err
    assert cli.main(["extract", str(path), "--record", "--home", str(tmp_path)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("recorded 1/4 A (first) ") and out[4] == "recorded 4 responses; the extraction's responses have cost 0"
    assert out[6:] == ["rows: 4 (0 duplicates dropped); invalid: none", "evidence class: exploratory (4 rows captured after they could be used)"]
    assert cli.main(["extract", str(path), "--home", str(tmp_path)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == out[5]


def test_an_observations_value_column_cannot_be_named_document(tmp_path):
    from test_observations import OBSERVATIONS_CSV, observations_declaration

    csv = OBSERVATIONS_CSV.replace(b",Rate\n", b",document\n")
    with pytest.raises(DatasetError, match="A value column cannot be named event, published, revision, estimated, document."):
        importer.import_observations(tmp_path, csv, observations_declaration(values={"document": "percent"}))
