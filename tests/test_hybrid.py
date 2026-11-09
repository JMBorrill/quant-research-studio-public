"""Hybrid operators and research templates (Phase 4B workstream 5): the as-of aggregate worked by
hand; the templates' features replayed from their recorded responses, offline, to the same datasets
on every platform; market, Python, ML and LLM nodes in one strategy with no change to the graph
schema; and a holdout that no holdout-period document can reach."""

import csv
import io
import json
import shutil
import subprocess
import sys

import pandas as pd
import pytest

from conftest import EXTENSION_DIR, run_bundle, run_recorded
from qrs import compiler, datasets, export, extraction, graph, importer, templates
from qrs.contracts import Edge, Node, PortRef, StrategyDocument
from qrs.hashing import canonical_json, sha256_hex
from qrs.operators.data import aggregate_values
from qrs.runtime import execute
from test_extraction import ANSWERS, recorded
from test_observations import import_fixture, threshold_strategy

# The features of tests/test_extraction.py, with a 300-second latency: A (0.5) usable at 14:35 UTC on
# 26 November 2024, B (-0.5) at 19:05, A's correction (0.25) at 20:05, C (0.0) at 00:05 on the 28th.
# NYSE closes at 21:00 UTC, and at 18:00 on the 29th (an early close); the 28th is a holiday.


def closes_and_features(tmp_path, **changes):
    bars_id, observations_id = import_fixture(tmp_path)
    _, features_id = recorded(tmp_path, **changes)
    closes = datasets.session_closes(datasets.load(tmp_path, bars_id))
    return closes, datasets.load_observations(tmp_path, features_id), datasets.load_observations(tmp_path, observations_id)


def aggregate(rows, closes, days=3, statistic="mean", min_count=1, column="sentiment"):
    return list(aggregate_values(rows, column, closes, pd.Timedelta(days=days), statistic, min_count).round(12))


NAN = float("nan")


def same(a, b) -> bool:
    return len(a) == len(b) and all((pd.isna(x) and pd.isna(y)) or x == y for x, y in zip(a, b, strict=True))


def test_the_aggregate_counts_each_documents_latest_version_within_the_window_worked_by_hand(tmp_path):
    closes, features, _ = closes_and_features(tmp_path)
    # 26th: A in its correction (0.25) and B (-0.5). 27th: the same. 29th: C (0.0) too. 2 December
    # onwards: every story is more than 3 days old.
    assert same(aggregate(features, closes), [NAN, -0.125, -0.125, round(-0.25 / 3, 12), NAN, NAN, NAN, NAN, NAN])
    assert same(aggregate(features, closes, statistic="sum"), [NAN, -0.25, -0.25, -0.25, NAN, NAN, NAN, NAN, NAN])
    assert same(aggregate(features, closes, statistic="count"), [NAN, 2, 2, 3, NAN, NAN, NAN, NAN, NAN])
    assert same(aggregate(features, closes, min_count=3), [NAN, NAN, NAN, round(-0.25 / 3, 12), NAN, NAN, NAN, NAN, NAN])


def test_the_window_includes_a_version_usable_exactly_its_length_before_the_close(tmp_path):
    # With a window of 1 day: at the 27th's close, A's correction (usable 24h55m before) and B (25h55m)
    # are out. A latency of 3,600 seconds instead makes A's correction usable at 21:00 on the 26th:
    # exactly one day before the 27th's close, and so still in.
    closes, features, _ = closes_and_features(tmp_path)
    assert same(aggregate(features, closes, days=1)[1:3], [-0.125, NAN])
    closes, features, _ = closes_and_features(tmp_path, latency_seconds=3600, capture="an hour")
    assert same(aggregate(features, closes, days=1)[1:3], [-0.125, 0.25])


def test_a_correction_never_changes_an_earlier_close_and_replaces_its_original(tmp_path):
    # With a 90-minute latency, A's correction is usable at 21:30 on the 26th, after that close: there
    # the original (0.5) counts with B (-0.5), and from the 27th the correction replaces it.
    closes, features, _ = closes_and_features(tmp_path, latency_seconds=5400, capture="ninety minutes")
    assert same(aggregate(features, closes)[1:3], [0.0, -0.125])


def test_a_latest_version_with_no_value_counts_as_none_not_as_its_earlier_version(tmp_path):
    # A's correction answered out of bounds (7) has no value: from its use on the 26th only B (-0.5)
    # counts there, never A's original (0.5); on the 29th, B and C (0.0).
    answers = {**ANSWERS, ("A", "corrected"): '{"sentiment": 7}'}
    closes, features, _ = closes_and_features(tmp_path, answers=answers)
    assert same(aggregate(features, closes)[:4], [NAN, -0.5, -0.5, -0.25])
    assert same(aggregate(features, closes, statistic="sum")[:4], [NAN, -0.5, -0.5, -0.5])
    assert same(aggregate(features, closes, statistic="count")[:4], [NAN, 1, 1, 2])  # its correction is not counted
    assert same(aggregate(features, closes, min_count=2)[:4], [NAN, NAN, NAN, -0.25])


def test_without_documents_each_event_counts_once_in_its_latest_revision(tmp_path):
    # The weekly rates of tests/test_observations.py. At the 29th's early close (18:00 UTC): A (1.0)
    # and B (2.0); C comes at 19:00. At the 3 December close: A, B revised (2.5) and C revised (3.5).
    closes, _, observations = closes_and_features(tmp_path)
    values = aggregate(observations, closes, days=31, column="Rate")
    assert (values[3], values[6]) == (1.5, round(7 / 3, 12))


def test_the_aggregate_runs_as_a_node_and_a_cutoff_changes_no_earlier_value(tmp_path):
    bars_id, _ = import_fixture(tmp_path)
    _, features_id = recorded(tmp_path)
    sentiment = Node(id="rate", operator="qrs.as_of_aggregate", version=1, params={"dataset_id": features_id, "column": "sentiment", "window_days": 3})
    doc = threshold_strategy(bars_id, sentiment, "series", -1.0)
    strategy = tmp_path / "strategy.py"
    strategy.write_text(compiler.compile_for_data(doc, tmp_path))
    full = run_recorded(strategy, tmp_path).outputs["rate"]["series"]
    assert same(list(full.round(12)), [NAN, -0.125, -0.125, round(-0.25 / 3, 12), NAN, NAN, NAN, NAN, NAN])
    until = pd.Timestamp("2024-11-29").date()
    strategy.write_text(compiler.compile_for_data(doc, tmp_path, until))
    cut = run_recorded(strategy, tmp_path, until).outputs["rate"]["series"]  # sessions up to the 27th, which reads nothing usable later
    assert same(list(cut.round(12)), [NAN, -0.125, -0.125])


# Each template's features, replayed from its committed responses: the same datasets everywhere.
FEATURES = {
    "news_trend": "sha256:1eb321082ccc33334b7713ee34a47a4f8d27210f5a25b66f9be44c604b1262f6",
    "macro_ridge": "sha256:0d401edcbc00e83905edbca6783400dfe798c8954fca820400f862293c7570c0",
}


@pytest.mark.parametrize("template_id", FEATURES)
def test_each_templates_features_replay_offline_with_no_provider_to_the_same_dataset(template_id, tmp_path):
    script = (
        "import sys, socket\n"
        "for name in ('qrs.providers.fixture', 'qrs.providers.ollama', 'qrs.providers.anthropic_messages', 'anthropic'):\n"
        "    sys.modules[name] = None\n"
        "def refuse(*a, **k): raise OSError('network used')\n"
        "socket.socket.connect = refuse; socket.create_connection = refuse; socket.getaddrinfo = refuse\n"
        "from pathlib import Path\n"
        "from qrs import templates\n"
        f"print(templates.prepare_features({template_id!r}, Path({str(tmp_path)!r})))\n"
    )
    replayed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert replayed.returncode == 0, replayed.stderr
    assert replayed.stdout.strip() == FEATURES[template_id]
    assert datasets.read_manifest(tmp_path, FEATURES[template_id]).evidence_class == "synthetic"


@pytest.mark.parametrize("template_id", FEATURES)
def test_recording_a_templates_extraction_again_gives_the_same_rows(template_id, tmp_path):
    """The committed responses are what the fixture provider answers: recording afresh, into an
    empty store, gives a different dataset (new capture times) with the same rows."""
    replayed = datasets.load_observations(tmp_path / "replayed", templates.prepare_features(template_id, tmp_path / "replayed"))
    fixture = templates.load(template_id).fixture
    fresh = tmp_path / "fresh"
    importer.import_documents(fresh, (templates.TEMPLATE_DIR / f"{template_id}.documents.csv").read_bytes(), fixture.documents)
    recording = fixture.extraction.model_copy(update={"endpoint": str(templates.TEMPLATE_DIR / fixture.extraction.endpoint)})
    again = extraction.extract(fresh, recording, record=True, report=lambda _: None)
    assert again != FEATURES[template_id]
    pd.testing.assert_frame_equal(datasets.load_observations(fresh, again), replayed)


# The strategy document's schema, hashed as Phase 4A (5624dda) left it.
STRATEGY_SCHEMA_SHA256 = "8f169197e1f0e2ffe9388e6a5b03b8ff9f7c7a9c27447c91283fd7c9e4ee99c7"


def test_market_python_ml_and_llm_nodes_combine_with_no_change_to_the_graph_schema(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(EXTENSION_DIR))
    import qrs_roc  # noqa: F401  the example extension: trusted Python operators

    assert sha256_hex(canonical_json(StrategyDocument.model_json_schema())) == STRATEGY_SCHEMA_SHA256
    doc = templates.instantiate("macro_ridge", chosen=templates.prepare("macro_ridge", tmp_path))
    # The extension's candle table (Python), merged with the market and report features, feeds the ridge model.
    doc.nodes += [
        Node(id="candle", operator="ext.candle", version=1),
        Node(id="add_change", operator="qrs.merge", version=1, params={"name": "change"}),
        Node(id="add_report", operator="qrs.merge", version=1, params={"name": "growth"}),
    ]
    rewired = [e for e in doc.edges if (e.target.node, e.target.port) not in {("fit", "features"), ("predict", "features")}]

    def wire(a, b, c, d):
        return Edge(source=PortRef(node=a, port=b), target=PortRef(node=c, port=d))

    doc.edges = rewired + [
        wire("bars", "bars", "candle", "bars"),
        wire("candle", "table", "add_change", "table"),
        wire("change", "series", "add_change", "series"),
        wire("add_change", "table", "add_report", "table"),
        wire("growth", "series", "add_report", "series"),
        wire("add_report", "table", "fit", "features"),
        wire("add_report", "table", "predict", "features"),
    ]
    assert graph.validate(doc) == [] and graph.check_data(doc, tmp_path) == []
    strategy = tmp_path / "strategy.py"
    strategy.write_text(compiler.compile_for_data(doc, tmp_path))
    record = execute(strategy, tmp_path, tmp_path / "out")
    operators = set(record["operators"])
    assert {"ext.candle@1", "qrs.as_of@1", "qrs.fit_ridge@1", "qrs.rsi@1"} <= operators
    (tmp_path / "exported").mkdir()
    assert run_bundle(export.build(doc, tmp_path), tmp_path / "exported")["result_sha256"] == record["result_sha256"]


def test_no_holdout_period_document_reaches_a_trial(tmp_path):
    """A trial of the news template cut at 1 July 2024 gives the same result whatever the stories
    published from its last close (28 June) on say, and with whatever text; the full run does not."""
    holdout = pd.Timestamp("2024-07-01").date()
    template = templates.load("news_trend")
    answers = json.loads((templates.TEMPLATE_DIR / template.fixture.extraction.endpoint).read_text())
    results = {}
    for name, flip in (("original", False), ("rewritten", True)):
        home = tmp_path / name
        chosen = templates.prepare("news_trend", home)
        if flip:  # every story published on or after 1 July 2024 is reworded and now says the opposite
            with open(templates.TEMPLATE_DIR / "news_trend.documents.csv", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                stories = list(reader)
            late = [row for row in stories if row["Published"] >= "2024-07-01"]
            assert [row["Id"] for row in late] == ["I", "K", "J", "L", "M"]
            flipped = dict(answers)
            for row in late:
                answer = answers.get(sha256_hex(extraction.render(template.fixture.extraction.prompt, row["Headline"], row["Body"]).encode()))
                row["Body"] = f"Rewritten: {row['Body']}"
                key = sha256_hex(extraction.render(template.fixture.extraction.prompt, row["Headline"], row["Body"]).encode())
                opposite = isinstance(answer, str) and answer.startswith("{")
                flipped[key] = json.dumps({"sentiment": -json.loads(answer)["sentiment"]}) if opposite else answer
            rewritten = io.StringIO()
            writer = csv.DictWriter(rewritten, reader.fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(stories)
            documents = importer.import_documents(home, rewritten.getvalue().encode(), template.fixture.documents)
            (home / "answers.json").write_text(json.dumps(flipped))
            shutil.rmtree(home / "responses")
            update = {"documents": documents, "endpoint": str(home / "answers.json"), "capture": "rewritten"}
            spec = template.fixture.extraction.model_copy(update=update)
            chosen[datasets.OBSERVATIONS] = extraction.extract(home, spec, record=True, report=lambda _: None)
        doc = templates.instantiate("news_trend", chosen=chosen)
        strategy = home / "strategy.py"
        for cut in (holdout, None):
            strategy.write_text(compiler.compile_for_data(doc, home, cut))
            results[name, cut] = execute(strategy, home, home / f"out-{cut}", cut)["result_sha256"]
    assert results["original", holdout] == results["rewritten", holdout]
    assert results["original", None] != results["rewritten", None]
