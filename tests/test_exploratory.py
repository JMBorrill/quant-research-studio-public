"""Verified and exploratory analysis (Phase 4B workstream 6): a verified experiment that would read
exploratory data is refused before anything is queued; an exploratory one runs, labelled, and its
holdout evaluations are kept apart from verified ones; and holdout evaluations are tracked across
experiments by their window, the bars traded and the holdout start."""

import json
import sqlite3
from pathlib import Path

import pytest

from conftest import client_for, wait
from qrs import datasets, extraction, graph, importer, registry, responses, templates
from qrs.contracts import Edge, Node, PortRef, ResponseRecord, StrategyDocument
from qrs.operators.data import ObservationsId
from qrs.store import Store

HOLDOUT = "2024-07-01"


def news_with_retrospective_data(home: Path) -> dict[str, str]:
    """The news template's fixture with its bars and stories declared retrospective, as real data
    imported after the fact would be, so the features extracted from them are exploratory. The
    template's recorded responses answer the same requests."""
    fixture = templates.load("news_trend").fixture
    bars = importer.import_csv(
        home,
        (templates.TEMPLATE_DIR / "news_trend.csv").read_bytes(),
        fixture.declaration.model_copy(update={"evidence_class": "retrospective", "name": "News bars (retrospective)"}),
    )
    documents = importer.import_documents(
        home,
        (templates.TEMPLATE_DIR / "news_trend.documents.csv").read_bytes(),
        fixture.documents.model_copy(update={"evidence_class": "retrospective", "name": "News stories (retrospective)"}),
    )
    for line in (templates.TEMPLATE_DIR / "news_trend.responses.jsonl").read_text().splitlines():
        responses.save(home, ResponseRecord.model_validate_json(line))
    spec = fixture.extraction.model_copy(update={"documents": documents, "name": "News sentiment (retrospective)"})
    return {datasets.BARS: bars, datasets.OBSERVATIONS: extraction.extract(home, spec, report=lambda _: None)}


def news_strategy(client, chosen: dict[str, str]) -> dict:
    created = client.post("/api/strategies", json={"template_id": "news_trend", "datasets": chosen})
    assert created.status_code == 200, created.text
    return created.json()


def experiment(strategy: dict, windows: list[int], **changes) -> dict:
    return {
        "name": f"News windows {windows}",
        "strategy_id": strategy["id"],
        "revision": strategy["revision"],
        "search": "grid",
        "axes": [{"node": "sentiment", "param": "window_days", "values": windows}],
        "objective": "net_pnl",
        "direction": "maximize",
        "holdout_start": HOLDOUT,
        **changes,
    }


def run_and_evaluate(client, body: dict) -> dict:
    """Create the experiment, wait for its trials, evaluate trial 1's holdout; its summary once done."""
    response = client.post("/api/experiments", json=body)
    assert response.status_code == 200, response.text
    created = response.json()
    for trial in created["trials"]:
        wait(client, trial["run"]["id"], timeout=120)
    assert client.post(f"/api/experiments/{created['id']}/holdout", json={"trial": 1}).status_code == 200
    summary = client.get(f"/api/experiments/{created['id']}").json()
    wait(client, summary["holdout"][-1]["run"]["id"], timeout=120)
    return client.get(f"/api/experiments/{created['id']}").json()


def test_a_verified_experiment_reading_exploratory_data_is_refused_before_anything_is_queued(tmp_path):
    chosen = news_with_retrospective_data(tmp_path)
    assert datasets.read_manifest(tmp_path, chosen[datasets.OBSERVATIONS]).evidence_class == "exploratory"
    with client_for(tmp_path) as client:
        strategy = news_strategy(client, chosen)
        refused = client.post("/api/experiments", json=experiment(strategy, [2, 3]))
        assert refused.status_code == 422
        assert "reads exploratory data (News sentiment (retrospective)), so its results cannot be verified" in refused.text
        assert client.get("/api/experiments", params={"strategy_id": strategy["id"]}).json() == []
    assert Store(tmp_path / "qrs.sqlite").list_experiments() == []


def test_a_verified_experiment_on_exploratory_bars_is_refused_too(tmp_path):
    fixture = templates.load("ema_crossover").fixture
    declaration = fixture.declaration.model_copy(update={"evidence_class": "exploratory", "name": "Exploratory bars"})
    bars = importer.import_csv(tmp_path, (templates.TEMPLATE_DIR / "ema_crossover.csv").read_bytes(), declaration)
    with client_for(tmp_path) as client:
        created = client.post("/api/strategies", json={"template_id": "ema_crossover", "dataset_id": bars}).json()
        body = experiment(created, [8, 9]) | {"axes": [{"node": "fast", "param": "period", "values": [8, 9]}], "holdout_start": "2024-06-03"}
        refused = client.post("/api/experiments", json=body)
        assert refused.status_code == 422 and "reads exploratory data (Exploratory bars)" in refused.text
    assert Store(tmp_path / "qrs.sqlite").list_experiments() == []


def test_an_exploratory_experiment_is_labelled_and_its_holdout_kept_apart_from_verified_ones(tmp_path):
    exploratory = news_with_retrospective_data(tmp_path)
    with client_for(tmp_path) as client:
        summary = run_and_evaluate(client, experiment(news_strategy(client, exploratory), [2, 3], exploratory=True))
        assert summary["exploratory"] and summary["experiment"]["exploratory"]
        assert {row["evidence_class"] for row in summary["ranking"]["runs"]} == {"exploratory"}
        compared = client.post("/api/compare", json={"run_ids": [t["run"]["id"] for t in summary["trials"]]}).json()
        assert {row["evidence_class"] for row in compared["runs"]} == {"exploratory"}  # comparisons say so too
        (evaluation,) = summary["holdout"]
        assert (evaluation["exploratory"], evaluation["repeat"], evaluation["earlier"]) == (True, False, {"verified": 0, "exploratory": 0})
        assert evaluation["result"]["metrics"]
        assert summary["window"]["verified"] == [] and [e["run"]["id"] for e in summary["window"]["exploratory"]] == [evaluation["run"]["id"]]


def test_holdout_evaluations_are_tracked_across_experiments_by_their_window(tmp_path):
    exploratory = news_with_retrospective_data(tmp_path)
    synthetic = templates.prepare("news_trend", tmp_path)  # the template's own fixture: synthetic, so verified
    with client_for(tmp_path) as client:
        first = run_and_evaluate(client, experiment(news_strategy(client, exploratory), [2, 3], exploratory=True))
        # Another experiment on the same bars and holdout start: a repeat, whatever its kind.
        second = run_and_evaluate(client, experiment(news_strategy(client, exploratory), [3, 4], exploratory=True))
        (again,) = second["holdout"]
        assert (again["repeat"], again["earlier"]) == (True, {"verified": 0, "exploratory": 1})
        assert [e["experiment_id"] for e in second["window"]["exploratory"]] == [first["id"], second["id"]]
        # Other bars, or another holdout start, are another window.
        verified = run_and_evaluate(client, experiment(news_strategy(client, synthetic), [2, 3]))
        assert not verified["exploratory"] and (verified["holdout"][0]["repeat"], verified["holdout"][0]["exploratory"]) == (False, False)
        assert [e["experiment_id"] for e in verified["window"]["verified"]] == [verified["id"]] and verified["window"]["exploratory"] == []
        later = run_and_evaluate(client, experiment(news_strategy(client, exploratory), [2, 3], exploratory=True, holdout_start="2024-08-01"))
        assert (later["holdout"][0]["repeat"], later["holdout"][0]["earlier"]) == (False, {"verified": 0, "exploratory": 0})
        assert later["window"]["verified"] == [] and [e["experiment_id"] for e in later["window"]["exploratory"]] == [later["id"]]
        # An exploratory experiment may read verified data: on the verified one's window, it is a
        # repeat of it, and each experiment lists both, apart.
        mixed = run_and_evaluate(client, experiment(news_strategy(client, synthetic), [3, 4], exploratory=True))
        (look,) = mixed["holdout"]
        assert (look["exploratory"], look["repeat"], look["earlier"]) == (True, True, {"verified": 1, "exploratory": 0})
        for summary in (mixed, client.get(f"/api/experiments/{verified['id']}").json()):
            assert [e["experiment_id"] for e in summary["window"]["verified"]] == [verified["id"]]
            assert [e["experiment_id"] for e in summary["window"]["exploratory"]] == [mixed["id"]]


def test_a_store_from_before_phase_4b_fills_each_experiments_window_and_keeps_its_repeats(tmp_path):
    synthetic = templates.prepare("news_trend", tmp_path)
    with client_for(tmp_path) as client:
        first = run_and_evaluate(client, experiment(news_strategy(client, synthetic), [2, 3]))
        assert client.post(f"/api/experiments/{first['id']}/holdout", json={"trial": 2}).status_code == 200
        second = run_and_evaluate(client, experiment(news_strategy(client, synthetic), [3, 4]))
        untraced = news_strategy(client, synthetic)  # its bars will pass through an operator not loaded
    store = Store(tmp_path / "qrs.sqlite")
    doc = store.get_strategy(untraced["id"])[1]
    doc.nodes.append(Node(id="resample", operator="myteam.resample", version=1, params={}))
    doc.edges = [
        e if e.target.port != "bars" or e.target.node != "backtest" else e.model_copy(update={"source": PortRef(node="resample", port="bars")})
        for e in doc.edges
    ]
    store.save_strategy(untraced["id"], untraced["revision"], doc)
    # Back to the Phase 4A store: experiments without their window or kind, repeats counted within
    # one experiment, one whose bars cannot be traced now, and one whose strategy is missing.
    with sqlite3.connect(tmp_path / "qrs.sqlite") as db:
        for column in ("bars", "holdout_start", "exploratory"):
            db.execute(f"ALTER TABLE experiments DROP COLUMN {column}")
        db.execute("UPDATE holdout_evaluations SET repeat = 0 WHERE experiment_id = ?", (second["id"],))
        for experiment_id, strategy_id, revision in (("untraced", untraced["id"], untraced["revision"] + 1), ("lost", "missing", 1)):
            document = json.dumps(experiment({"id": strategy_id, "revision": revision}, [2, 3]))
            db.execute("INSERT INTO experiments (id, document, created_at) VALUES (?, ?, ?)", (experiment_id, document, "2026-01-01T00:00:00+00:00"))
    store = Store(tmp_path / "qrs.sqlite")
    for experiment_id in (first["id"], second["id"]):
        row = store.get_experiment(experiment_id)
        assert (row["bars"], row["holdout_start"], row["exploratory"]) == (synthetic[datasets.BARS], HOLDOUT, 0)
    window = store.window_evaluations(first["id"])
    assert [(e["experiment_id"], e["repeat"], e["exploratory"]) for e in window] == [(first["id"], 0, 0), (first["id"], 1, 0), (second["id"], 0, 0)]
    # Left for an open that can fill them; their own evaluations are still repeats of each other.
    assert [store.get_experiment(i)["holdout_start"] for i in ("untraced", "lost")] == [None, None]
    assert [store.record_holdout_evaluation(f"run-{n}", "untraced", 1) for n in (1, 2)] == [False, True]


@pytest.fixture
def bars_passed_on():
    """An extension operator that passes bars on, as one filtering them by events it reads from an
    observations dataset would."""

    class ThroughParams(registry.Params):
        dataset_id: ObservationsId

    @registry.operator(
        "test.bars_through",
        1,
        title="Bars through",
        description="Its bars, unchanged.",
        family="sources",
        inputs={"bars": "bars"},
        outputs={"bars": "bars"},
        params=ThroughParams,
        temporal=registry.Temporal(warm_up=lambda p: 0),
    )
    def through(ctx, inputs, params):
        return {"bars": inputs["bars"]}

    yield
    del registry._operators[("test.bars_through", 1)]


def test_the_bars_traded_are_traced_back_through_operators_that_pass_bars_on(bars_passed_on):
    def wire(source: str, target: str, port: str) -> Edge:
        return Edge(source=PortRef(node=source, port="bars"), target=PortRef(node=target, port=port))

    doc = StrategyDocument(
        name="through",
        nodes=[
            Node(id="data", operator="qrs.dataset", version=1, params={"dataset_id": "sha256:traded"}),
            Node(id="through", operator="test.bars_through", version=1, params={"dataset_id": "sha256:events"}),
            Node(id="test", operator="qrs.backtest", version=1, params={}),
        ],
        edges=[wire("data", "through", "bars"), wire("through", "test", "bars")],
    )
    assert graph.traded_bars(doc) == "sha256:traded"  # not the observations it reads on the way
    assert graph.traded_bars(doc.model_copy(update={"edges": doc.edges[1:]})) is None  # bars from no dataset
