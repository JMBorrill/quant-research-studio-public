"""Run comparison over the service, with real workers: metrics over the common window equal those of
the sliced ledgers, cost mismatches are flagged, runs on different datasets are refused, and a
re-run links to its predecessor."""

import json
import sqlite3

import pandas as pd
import pytest

from conftest import client_for, new_strategy, save, start, wait
from qrs import datasets
from qrs.analytics import metrics
from qrs.comparison import ComparisonError, Ledger, effective_params, from_close_before, traded_dataset
from qrs.contracts import Edge, Node, PortRef, RunInfo, StrategyDocument
from qrs.store import Store


def set_param(node: str, name: str, value):
    def edit(doc: dict) -> None:
        next(n for n in doc["nodes"] if n["id"] == node)["params"][name] = value

    return edit


def run_template(client, *edits, dataset: str | None = None, template_id: str = "ema_crossover") -> dict:
    """Run a template, edited, to completion."""
    strategy = new_strategy(client, dataset, template_id)
    revision = strategy["revision"]
    if edits:
        for edit in edits:
            edit(strategy["document"])
        revision = save(client, strategy, strategy["document"])
    finished = wait(client, start(client, strategy, revision)["id"])
    assert finished["status"] == "succeeded", finished["error"]
    return finished


def compare(client, *runs):
    return client.post("/api/compare", json={"run_ids": [r["id"] for r in runs]})


def ledger(home, run: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    folder = home / "runs" / run["id"] / "results" / "backtest"
    return pd.read_csv(folder / "fills.csv", parse_dates=["session"]), pd.read_csv(folder / "equity.csv", index_col="session", parse_dates=True)


def test_common_window_metrics_equal_those_of_the_sliced_ledgers(home):
    with client_for(home) as client:
        slow = run_template(client, set_param("slow", "period", 53))  # EMA 20/53: warm-up 53
        fast = run_template(client, set_param("fast", "period", 5), set_param("slow", "period", 10))  # warm-up 10
        result = compare(client, slow, fast).json()

    sessions = ledger(home, slow)[1].index
    assert result["window"] == {"start": sessions[53].date().isoformat(), "end": sessions[-1].date().isoformat(), "sessions": len(sessions) - 53}
    assert [(r["warm_up"], r["excluded_sessions"]) for r in result["runs"]] == [(53, 53), (10, 53)]
    for run, compared in zip((slow, fast), result["runs"], strict=True):
        fills, equity = ledger(home, run)
        window, first = equity.iloc[53:], equity.index[53]
        later = fills[fills["session"] > first]  # a fill on the first session is already in its equity
        assert compared["metrics"] == metrics(later, window, window["equity"].iloc[0])
        # The same figures, computed without qrs.analytics:
        m, curve = compared["metrics"], window["equity"]
        signed = later["quantity"].where(later["side"] == "BUY", -later["quantity"])
        assert m["net_pnl"] == pytest.approx(curve.iloc[-1] - curve.iloc[0])
        assert (m["commission"], m["slippage"]) == pytest.approx((later["commission"].sum(), later["slippage"].sum()))
        assert (m["sessions"], m["fills"]) == (len(window), len(later))
        assert m["exposure_pct"] == pytest.approx((window["position"] != 0).mean() * 100)
        assert m["max_drawdown"] == pytest.approx((curve.cummax() - curve).max())
        assert m["round_trips"] == ((window["position"].iloc[0] + signed.cumsum()) == 0).sum()
        assert compared["position_at_start"] == window["position"].iloc[0]
        assert compared["equity_change"] == pytest.approx((curve - curve.iloc[0]).tolist())

    # The boundary: the fast run fills on the window's first session. That fill was decided before the
    # window, so it is left out and its costs sit in the baseline; window and earlier parts add up.
    fast_fills, fast_equity = ledger(home, fast)
    before = fast_fills[fast_fills["session"] <= sessions[53]]
    assert (fast_fills["session"] == sessions[53]).any()
    whole = json.loads((home / "runs" / fast["id"] / "results" / "backtest" / "summary.json").read_text())["metrics"]
    window_metrics = result["runs"][1]["metrics"]
    assert window_metrics["commission"] + before["commission"].sum() == pytest.approx(whole["commission"])
    assert window_metrics["fills"] + len(before) == whole["fills"]
    assert window_metrics["net_pnl"] + (fast_equity["equity"].iloc[53] - whole["initial_cash"]) == pytest.approx(whole["net_pnl"])
    assert result["parameters"] == [{"node": "fast", "field": "period", "values": [20, 5]}, {"node": "slow", "field": "period", "values": [53, 10]}]
    assert result["mismatches"] == []


def test_a_cost_mismatch_is_flagged(home):
    with client_for(home) as client:
        usual = run_template(client)
        # Dropping a parameter that holds its default is not a difference.
        pricier = run_template(client, set_param("backtest", "commission_per_contract", 5.0), lambda doc: doc["nodes"][1]["params"].pop("column"))
        result = compare(client, usual, pricier).json()
    assert result["mismatches"] == [{"name": "commission_per_contract", "values": [2.5, 5.0]}]
    assert result["parameters"] == [{"node": "backtest", "field": "commission_per_contract", "values": [2.5, 5.0]}]
    commissions = [r["metrics"]["commission"] for r in result["runs"]]
    assert commissions[1] == 2 * commissions[0] > 0


def test_differing_runtime_or_operator_code_is_flagged(home):
    with client_for(home) as client:
        ema, again, sma = run_template(client), run_template(client), run_template(client, template_id="sma_crossover")
        # As for a run made with other code: edit what its record says it ran with.
        record_path = home / "runs" / again["id"] / "record.json"
        record = json.loads(record_path.read_text())
        record["runtime"] |= {"qrs_source_sha256": "older", "python": "3.12.0"}
        record["runtime"]["packages"]["pandas"] = "2.3.0"
        record["operators"]["qrs.ema@1"]["source_sha256"] = "older"
        record_path.write_text(json.dumps(record))
        flagged = {m["name"] for m in compare(client, ema, again).json()["mismatches"]}
        different_operators = compare(client, ema, sma).json()
    assert flagged == {"runtime: qrs_source_sha256", "runtime: python", "runtime: pandas", "operator code: qrs.ema@1"}
    # Operators that only one run used are a difference of strategy, not of code.
    assert different_operators["mismatches"] == []
    assert {"node": "fast", "field": "operator", "values": ["qrs.ema@1", "qrs.sma@1"]} in different_operators["parameters"]


def test_parameters_of_an_operator_no_longer_registered_are_compared_as_saved():
    assert effective_params(Node(id="gone", operator="ext.gone", version=1, params={"window": 3})) == {"window": 3}
    assert effective_params(Node(id="fast", operator="qrs.ema", version=1, params={})) == {"period": 20, "column": "close"}


def test_a_backtest_fed_by_an_operator_without_a_dataset_is_refused():
    source, backtest = Node(id="source", operator="ext.gone", version=1), Node(id="backtest", operator="qrs.backtest", version=1)
    bars = Edge(source=PortRef(node="source", port="bars"), target=PortRef(node="backtest", port="bars"))
    info = RunInfo(id="r1", strategy_id="s", revision=1, status="succeeded", code_sha256="c", created_at="t")
    run = Ledger(info, "backtest", StrategyDocument(name="s", nodes=[source, backtest], edges=[bars]), {}, 0, pd.DataFrame(), pd.DataFrame(), {})
    with pytest.raises(ComparisonError, match="Run r1: cannot tell which dataset its backtest traded."):
        traded_dataset(run)


def test_runs_on_different_datasets_are_refused(home):
    demo, other = datasets.create_synthetic(home), datasets.create_synthetic(home, seed=7)
    with client_for(home) as client:
        response = compare(client, run_template(client, dataset=demo), run_template(client, dataset=other))
    assert response.status_code == 422
    assert "Only runs on the same dataset can be compared" in response.json()["detail"]
    assert "seed 42" in response.json()["detail"] and "seed 7" in response.json()["detail"]


def test_a_rerun_links_to_its_predecessor_and_reproduces_it(home):
    with client_for(home) as client:
        first = run_template(client)
        queued = client.post(f"/api/runs/{first['id']}/rerun").json()
        assert (queued["predecessor"], queued["strategy_id"], queued["revision"]) == (first["id"], first["strategy_id"], first["revision"])
        again = wait(client, queued["id"])
        assert again["status"] == "succeeded" and again["predecessor"] == first["id"]
        assert again["result_sha256"] == first["result_sha256"] and again["code_sha256"] == first["code_sha256"]
        assert next(r for r in client.get("/api/runs").json() if r["id"] == again["id"])["predecessor"] == first["id"]
        result = compare(client, first, again).json()
        assert client.post("/api/runs/missing/rerun").status_code == 404
    assert result["runs"][0]["metrics"] == result["runs"][1]["metrics"]
    assert (result["parameters"], result["mismatches"]) == ([], [])


def test_runs_that_read_the_same_datasets_but_trade_different_ones_are_refused(home):
    # Both datasets have the same sessions and instrument; each strategy reads both and trades one.
    demo, other = datasets.create_synthetic(home), datasets.create_synthetic(home, seed=7)

    def also_read(dataset_id):
        def edit(doc: dict) -> None:
            doc["nodes"].append({"id": "other", "operator": "qrs.dataset", "version": 1, "params": {"dataset_id": dataset_id}})

        return edit

    with client_for(home) as client:
        on_demo = run_template(client, also_read(other), dataset=demo)
        on_other = run_template(client, also_read(demo), dataset=other)
        also_on_demo = run_template(client, also_read(other), dataset=demo)
        refused = compare(client, on_demo, on_other)
        allowed = compare(client, on_demo, also_on_demo)
    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        f"Only runs on the same dataset can be compared ({on_demo['id']}: Synthetic SYN daily bars (seed 42) ({demo[7:19]}); "
        f"{on_other['id']}: Synthetic SYN daily bars (seed 7) ({other[7:19]}))."
    )
    assert allowed.status_code == 200


def test_comparisons_that_cannot_be_made_are_refused(home):
    with client_for(home) as client:
        good = run_template(client)
        other = run_template(client)

        def second_backtest(doc: dict) -> None:
            doc["nodes"].append({**next(n for n in doc["nodes"] if n["id"] == "backtest"), "id": "backtest_2"})
            doc["edges"] += [{**e, "target": {**e["target"], "node": "backtest_2"}} for e in doc["edges"] if e["target"]["node"] == "backtest"]

        two_backtests = run_template(client, second_backtest)
        strategy = new_strategy(client)
        set_param("backtest", "initial_cash", 100.0)(strategy["document"])  # too little margin: the run fails
        failed = wait(client, start(client, strategy, save(client, strategy, strategy["document"]))["id"])
        assert failed["status"] == "failed"
        (home / "runs" / other["id"] / "warm_up.json").unlink()  # as for a run from before warm-ups were recorded

        def refusal(*runs) -> str:
            response = compare(client, *runs)
            assert response.status_code == 422, response.text
            return response.json()["detail"]

        assert refusal(good) == "Choose at least two different runs to compare."
        assert refusal(good, good) == "Choose at least two different runs to compare."
        assert refusal(good, failed) == f"Run {failed['id']} is failed; only succeeded runs can be compared."
        assert refusal(good, other) == f"Run {other['id']} predates recorded warm-ups; re-run it to compare it."
        assert refusal(good, two_backtests) == f"Run {two_backtests['id']} has 2 backtests; only runs with one backtest can be compared."
        assert client.post("/api/compare", json={"run_ids": [good["id"], "missing"]}).status_code == 404


def test_a_store_from_before_reruns_gains_the_predecessor_column(tmp_path):
    path = tmp_path / "qrs.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE runs (id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,"
            " code_sha256 TEXT NOT NULL, result_sha256 TEXT, error TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT)"
        )
        db.execute("INSERT INTO runs (id, strategy_id, revision, status, code_sha256, created_at) VALUES ('old', 's', 1, 'succeeded', 'c', 't')")
    store = Store(path)
    store.create_run("new", "s", 1, "c", predecessor="old")
    assert {r["id"]: r["predecessor"] for r in store.list_runs()} == {"old": None, "new": "old"}
    Store(path)  # reopening does not add the column again


def test_round_trips_count_from_the_position_held_at_the_start():
    # A window that opens long one contract, sells it, buys one and sells it again: two round trips.
    sessions = pd.bdate_range("2026-01-05", periods=4)
    equity = pd.DataFrame({"position": [1, 0, 1, 0], "equity": [100.0, 101.0, 100.0, 102.0]}, index=sessions)
    fills = pd.DataFrame({"session": sessions[1:], "side": ["SELL", "BUY", "SELL"], "quantity": [1, 1, 1], "commission": [0.0] * 3, "slippage": [0.0] * 3})
    assert metrics(fills, equity, 100.0)["round_trips"] == 2


def test_a_slice_measured_from_the_close_before_it_counts_its_first_sessions_move_and_fills():
    # Worked by hand, as the holdout is measured. Long one contract at a close where equity is 100;
    # the next session sells it and closes at 97, below that baseline; then one is bought back (99),
    # and sold again (104). Each fill costs commission 1 and slippage 0.5.
    sessions = pd.bdate_range("2026-01-02", periods=4)
    equity = pd.DataFrame({"position": [1, 0, 1, 0], "equity": [100.0, 97.0, 99.0, 104.0]}, index=sessions)
    fills = pd.DataFrame({"session": sessions[1:], "side": ["SELL", "BUY", "SELL"], "quantity": [1, 1, 1], "commission": [1.0] * 3, "slippage": [0.5] * 3})
    info = RunInfo(id="r", strategy_id="s", revision=1, status="succeeded", code_sha256="c", created_at="t")
    ledger = Ledger(info, "backtest", StrategyDocument(name="hand"), {"datasets": {"d": {"evidence_class": "synthetic"}}}, 0, fills, equity, {})
    measured = from_close_before(ledger, 1)
    m = measured["metrics"]
    assert (measured["position_at_start"], m["initial_cash"], m["sessions"]) == (1, 100.0, 3)
    assert (m["net_pnl"], m["gross_pnl"], m["commission"], m["fills"]) == (4.0, 8.5, 3.0, 3)
    assert (m["max_drawdown"], m["max_drawdown_sessions"]) == (3.0, 2)  # 97 and 99 are below the baseline of 100
    assert m["round_trips"] == 2  # the contract held in, sold on the first session, then one more
    assert m["exposure_pct"] == pytest.approx(100 / 3)
