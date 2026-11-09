"""Stage 5 workstream 6: selection bias. The deflated Sharpe ratio reproduces Bailey and López de
Prado's published example; N counts every trial on the holdout window, failed ones too, except
searches over costs alone; the Reality Check uses the trials' common window, lists failed trials,
holds its size and meets its budget; and best-of-N results always show N."""

import math
import time

import numpy as np
import pytest
from scipy.signal import lfilter

from conftest import client_for, new_strategy
from qrs import stats
from qrs.store import Store
from test_analysis import document, wait_analysis
from test_experiments import create, experiment, finished, holdout_start


def test_the_deflated_sharpe_ratio_reproduces_the_published_example():
    """Bailey and López de Prado (2014), section 4: an annualised Sharpe ratio of 2.5 over 1,250 daily
    returns (250 a year) with skewness −3 and kurtosis 10, the best of 100 trials whose annualised
    Sharpe ratios vary by 0.5: the expected maximum is 0.1132 a session, and the DSR 0.9004."""
    sharpe, variance = 2.5 / math.sqrt(250), 0.5 / 250
    deflated = stats.expected_maximum_sharpe(variance, 100)
    assert deflated == pytest.approx(0.1132, abs=5e-5)
    assert stats.probabilistic_sharpe(sharpe, deflated, 1250, -3, 10) == pytest.approx(0.9004, abs=5e-5)
    # With no deflation, the probabilistic Sharpe ratio against zero: Φ(SR √(T − 1) / √(1 − γ3 SR + (γ4 − 1) / 4 SR²)).
    expected = 0.5 * (1 + math.erf(sharpe * math.sqrt(1249) / math.sqrt(1 + 3 * sharpe + 9 / 4 * sharpe**2) / math.sqrt(2)))
    assert stats.probabilistic_sharpe(sharpe, 0.0, 1250, -3, 10) == pytest.approx(expected, rel=1e-12)


def test_the_reality_check_finds_a_real_edge_and_holds_its_size_without_one():
    rng = np.random.default_rng(8)
    noise = rng.normal(0, 0.01, (500, 20))
    edge = noise.copy()
    edge[:, 7] += 0.003
    found = stats.reality_check(edge, 500, seed=1)
    assert found["p_value"] < 0.01 and found["best"] == 7
    assert stats.reality_check(noise, 500, seed=1)["p_value"] > 0.05
    # Its size at 5% on ten AR(1) trials, φ = 0.2, n = 250, whose true means are zero: measured 9%,
    # as the method states (6% for independent returns, 7% at n = 1,000).
    rejected = 0
    for seed in range(200):
        null = lfilter([1], [1, -0.2], rng.normal(0, 0.01, (250 + 50, 10)), axis=0)[50:]
        rejected += stats.reality_check(null, 199, seed)["p_value"] < 0.05
    assert rejected / 200 <= 0.12


def test_the_reality_check_meets_its_budget():
    """200 trials by 2,500 sessions by 1,000 replicates in under 60 s."""
    data = np.random.default_rng(9).normal(0, 0.01, (2500, 200))
    start = time.perf_counter()
    stats.reality_check(data, 1000, seed=0)
    assert time.perf_counter() - start < 60


def test_selection_methods_count_every_trial_on_the_window_and_list_failed_ones(home):
    with client_for(home) as client:
        holdout = holdout_start(home)
        strategy = new_strategy(client)
        searched = finished(client, create(client, experiment(strategy, holdout, [("fast", "period", [5, 10, 15])])))
        costs = finished(client, create(client, experiment(strategy, holdout, [("backtest", "commission_per_contract", [1.0, 2.0])])))
        another = finished(client, create(client, experiment(strategy, holdout, [("slow", "period", [30, 40])])))
        elsewhere = finished(client, create(client, experiment(strategy, holdout.replace(day=1), [("slow", "period", [30, 60])])))
        assert all(e["counts"] == {"succeeded": len(e["trials"])} for e in (searched, costs, another, elsewhere))
        Store(home / "qrs.sqlite").update_run(searched["trials"][2]["run"]["id"], status="failed")  # as if trial 3 had failed
        results = {}
        for method_id in ("qrs.deflated_sharpe", "qrs.reality_check"):
            queued = client.post(
                "/api/analyses", json=document({"trials": {"kind": "experiment", "experiment": searched["id"]}}, method_id).model_dump(mode="json")
            )
            assert queued.status_code == 200, queued.text
            done = wait_analysis(client, queued.json()["id"])
            assert done["status"] == "succeeded", done
            results[method_id] = client.get(f"/api/analyses/{done['id']}/result").json()["result"]
        dsr, rc = results["qrs.deflated_sharpe"]["values"], results["qrs.reality_check"]["values"]
        # N: this search's 3 trials (one failed) and the other search's 2; not the cost sweep's 2, nor
        # the experiment on another window.
        assert dsr["trials"] == rc["trials"] == 5 and dsr["succeeded"] == rc["succeeded"] == 2
        assert {(w["experiment"], w["costs_only"]) for w in dsr["window"]} == {(searched["id"], False), (costs["id"], True), (another["id"], False)}
        assert rc["not_succeeded"] == [{"trial": 3, "status": "failed"}] and "1 trials did not succeed" in results["qrs.reality_check"]["warnings"][0]
        # Its p-value covers only this experiment's trials, while N counts the window's: it says so.
        assert any("covers this experiment's 3 trials; 5 were tried" in w for w in results["qrs.reality_check"]["warnings"])
        ranking = client.get(f"/api/experiments/{searched['id']}").json()["ranking"]
        assert dsr["selected_trial"] == ranking["runs"][0]["trial"] and "ranked its trials by net_pnl" in results["qrs.deflated_sharpe"]["warnings"][-1]
        # The common window: from the latest warm-up among the trials, to the holdout start.
        assert dsr["n"] == ranking["window"]["sessions"] - 1 and 0 <= dsr["deflated_sharpe"] <= 1 and 0 <= rc["p_value"] <= 1
        unfinished = create(client, experiment(strategy, holdout, [("fast", "period", [6, 7])]))
        refused = client.post(
            "/api/analyses", json=document({"trials": {"kind": "experiment", "experiment": unfinished["id"]}}, "qrs.reality_check").model_dump(mode="json")
        )
        assert refused.status_code == 422 and "still queued or running" in refused.json()["detail"]
        finished(client, unfinished)


def test_the_probabilistic_sharpe_ratio_of_a_series_warns_of_autocorrelation(tmp_path):
    from test_describe import Given, days, run

    rng = np.random.default_rng(10)
    correlated = days(lfilter([1], [1, -0.5], rng.normal(0.001, 0.01, 600)))
    found = run("qrs.probabilistic_sharpe", Given({"returns": correlated}))
    sharpe, skewness, kurtosis = stats.sharpe_moments(correlated.to_numpy())
    assert found.values["probabilistic_sharpe"] == pytest.approx(stats.probabilistic_sharpe(sharpe, 0.0, 600, skewness, kurtosis))
    assert found.values["sharpe"] == pytest.approx(sharpe * math.sqrt(252)) and "assumes independent returns" in found.warnings[0]


# Regressions from the workstream's review.


def analysed(client, inputs: dict, method_id: str, **changes) -> dict:
    queued = client.post("/api/analyses", json=document(inputs, method_id, **changes).model_dump(mode="json"))
    assert queued.status_code == 200, queued.text
    done = wait_analysis(client, queued.json()["id"])
    assert done["status"] == "succeeded", done
    return client.get(f"/api/analyses/{done['id']}/result").json()["result"]


def test_a_trial_that_never_trades_counts_as_a_sharpe_ratio_of_zero(home):
    with client_for(home) as client:
        strategy = new_strategy(client)
        # fast = slow never crosses, so trial 2 never trades.
        body = experiment(strategy, holdout_start(home), [("fast", "period", [10, 50, 30])]) | {"objective": "fills"}  # the chosen trial trades most
        searched = finished(client, create(client, body))
        result = analysed(client, {"trials": {"kind": "experiment", "experiment": searched["id"]}}, "qrs.deflated_sharpe")
        assert result["values"]["deflated_sharpe"] is not None and any("1 trials never traded" in w for w in result["warnings"])


def test_an_experiment_is_read_only_to_its_holdout_start_and_a_cost_sweep_deflates_nothing(home):
    with client_for(home) as client:
        strategy = new_strategy(client)
        holdout = holdout_start(home)
        costs = finished(client, create(client, experiment(strategy, holdout, [("backtest", "commission_per_contract", [1.0, 2.0])])))
        given = {"trials": {"kind": "experiment", "experiment": costs["id"]}}
        early = client.post("/api/analyses", json=document(given, "qrs.deflated_sharpe", until="2024-06-03").model_dump(mode="json"))
        assert early.status_code == 422 and "ranked its trials on sessions to" in early.json()["detail"]
        missing = client.post(
            "/api/analyses", json=document({"trials": {"kind": "experiment", "experiment": "0" * 12}}, "qrs.reality_check").model_dump(mode="json")
        )
        assert missing.status_code == 422 and "not found" in missing.json()["detail"]
        result = analysed(client, given, "qrs.deflated_sharpe")
        assert result["values"]["deflated_sharpe"] is None and result["values"]["trials"] == 0 and "chooses no strategy" in result["warnings"][0]


def test_a_minimizing_objective_selects_its_lowest_trial(home):
    with client_for(home) as client:
        body = experiment(new_strategy(client), holdout_start(home), [("fast", "period", [5, 10, 15])]) | {"objective": "max_drawdown", "direction": "minimize"}
        searched = finished(client, create(client, body))
        result = analysed(client, {"trials": {"kind": "experiment", "experiment": searched["id"]}}, "qrs.deflated_sharpe")
        drawdowns = {r["trial"]: r["metrics"]["max_drawdown"] for r in client.get(f"/api/experiments/{searched['id']}").json()["ranking"]["runs"]}
        assert result["values"]["selected_trial"] == min(drawdowns, key=lambda t: (drawdowns[t], t))


def test_any_analysis_of_a_trial_or_its_holdout_shows_n(home):
    with client_for(home) as client:
        searched = finished(client, create(client, experiment(new_strategy(client), holdout_start(home), [("fast", "period", [5, 10])])))
        trial_run = searched["trials"][0]["run"]["id"]
        result = analysed(client, {"values": {"kind": "equity", "run": trial_run}}, "qrs.summary")
        assert result["selections"][trial_run] == {"experiment": searched["id"], "trial": 1, "holdout_evaluation": False, "trials": 2}
        assert any("one of 2 trials on its window" in w for w in result["warnings"])
        holdout = client.post(f"/api/experiments/{searched['id']}/holdout", json={"trial": 2}).json()["holdout"][0]["run"]["id"]
        from conftest import wait

        wait(client, holdout)
        shown = analysed(client, {"values": {"kind": "equity", "run": holdout}}, "qrs.summary")["selections"][holdout]
        assert shown["holdout_evaluation"] and shown["trial"] == 2 and "repeat_look" not in shown
        # A second look at the same holdout window is disclosed as one.
        second = client.post(f"/api/experiments/{searched['id']}/holdout", json={"trial": 1}).json()["holdout"][-1]["run"]["id"]
        wait(client, second)
        looked = analysed(client, {"values": {"kind": "equity", "run": second}}, "qrs.summary")
        assert looked["selections"][second]["repeat_look"] and any("repeat look" in w for w in looked["warnings"])
        # A re-run is the same choice from N, however many times it is re-run.
        again = wait(client, client.post(f"/api/runs/{trial_run}/rerun", json={}).json()["id"])
        again = wait(client, client.post(f"/api/runs/{again['id']}/rerun", json={}).json()["id"])
        rerun = analysed(client, {"values": {"kind": "equity", "run": again["id"]}}, "qrs.summary")["selections"][again["id"]]
        assert rerun == {"experiment": searched["id"], "trial": 1, "holdout_evaluation": False, "trials": 2}


def test_the_reality_check_refuses_missing_returns_rather_than_reporting_its_least_p_value():
    returns = np.random.default_rng(15).normal(0, 0.01, (250, 5))
    returns[100, 2] = np.nan
    with pytest.raises(ValueError, match="finite returns"):
        stats.reality_check(returns, 100, seed=0)


def test_the_reality_check_never_gives_a_p_value_of_zero():
    edge = np.random.default_rng(14).normal(0.01, 0.001, (300, 3))
    assert stats.reality_check(edge, 100, seed=0)["p_value"] == pytest.approx(1 / 101)
