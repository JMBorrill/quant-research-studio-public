"""Stage 5 workstream 7: robustness, sensitivity and regimes. Sensitivity surfaces equal their trials'
metrics; additive sub-period quantities reconcile with the whole; regime labels are lagged and
causal, and small regimes are warned; and the expanding rank operator is causal (the contract
harness checks every operator) and matches hand values."""

import numpy as np
import pandas as pd
import pytest

from conftest import client_for, new_strategy
from qrs.operators.indicators import expanding_ranks
from qrs.runtime import Run
from test_describe import Given, days, run
from test_experiments import create, experiment, finished, holdout_start
from test_selection import analysed


def test_expanding_rank_by_hand():
    assert expanding_ranks(pd.Series([3.0, 1.0, 2.0, 2.0, 5.0]), 1).tolist() == pytest.approx([1.0, 0.5, 2 / 3, 0.625, 1.0])
    found = Run(None).op("rank", "qrs.expanding_rank", 1, {"series": days([3.0, 1.0, 2.0, 2.0, 5.0])}, {"min_periods": 3})["series"]
    assert np.isnan(found.iloc[:2]).all() and found.iloc[2:].tolist() == pytest.approx([2 / 3, 0.625, 1.0])


def test_sensitivity_surfaces_equal_their_trials_metrics(home):
    with client_for(home) as client:
        axes = [("fast", "period", [5, 10, 15]), ("slow", "period", [30, 40])]
        searched = finished(client, create(client, experiment(new_strategy(client), holdout_start(home), axes)))
        result = analysed(client, {"trials": {"kind": "experiment", "experiment": searched["id"]}}, "qrs.sensitivity")
        ranking = client.get(f"/api/experiments/{searched['id']}").json()["ranking"]
        metrics = {r["trial"]: r["metrics"]["net_pnl"] for r in ranking["runs"]}
        from qrs.store import Store

        analysis_id = Store(home / "qrs.sqlite").list_analyses()[0]["id"]
        surface = pd.read_csv(home / "analyses" / analysis_id / "tables" / "surface.csv", index_col=0)
        assert len(surface) == 6 and dict(zip(surface["trial"], surface["net_pnl"], strict=True)) == pytest.approx(metrics)
        best = ranking["runs"][0]["trial"]
        assert result["values"]["best_trial"] == best and result["values"]["best_objective"] == pytest.approx(metrics[best])
        neighbours = result["values"]["neighbours"]
        assert neighbours and all(n["objective"] == pytest.approx(metrics[n["trial"]]) for n in neighbours)
        assert "figures/figure.svg" in result["outputs"]
        one = analysed(client, {"trials": {"kind": "experiment", "experiment": searched["id"]}}, "qrs.sensitivity", params={"axes": ["fast.period"]})
        assert one["values"]["n"] == 3 and set(one["values"]["held"]) == {"slow.period"}


def test_sub_period_totals_and_sessions_reconcile_with_the_whole():
    rng = np.random.default_rng(15)
    fractions = days(rng.normal(0.0005, 0.01, 700), start="2020-01-01")
    found = run("qrs.subperiods", Given({"returns": fractions}))
    assert found.values["sessions_reconcile"] and found.values["totals_reconcile"]
    assert found.values["total"] == pytest.approx(np.prod(1 + fractions.to_numpy()) - 1)
    assert found.tables["periods"]["period"].tolist() == ["2020", "2021", "2022"]
    prices = run("qrs.subperiods", Given({"returns": fractions * 1000}, additive=True), split="equal", parts=4)
    assert prices.tables["periods"]["total"].sum() == pytest.approx((fractions * 1000).sum()) and prices.values["totals_reconcile"]
    assert prices.tables["periods"]["sessions"].sum() == 700


def test_regimes_use_the_label_at_the_previous_close_and_warn_when_small():
    rng = np.random.default_rng(16)
    label = days((rng.random(400) > 0.5).astype(float))
    returns = days(rng.normal(0, 0.01, 400)) + 0.02 * label.shift(1).fillna(0.0)  # returns follow yesterday's label
    found = run("qrs.regimes", Given({"returns": returns, "label": label}))
    assert found.values["estimate"] == pytest.approx(0.02, abs=0.003) and found.values["p_value"] < 1e-10
    # A label known only at the close of the session it moves is not used for that session; and
    # changing the last label changes nothing.
    same_day = days(rng.normal(0, 0.01, 400)) + 0.02 * label
    assert abs(run("qrs.regimes", Given({"returns": same_day, "label": label})).values["estimate"]) < 0.005
    changed = label.copy()
    changed.iloc[-1] = 1 - changed.iloc[-1]
    assert run("qrs.regimes", Given({"returns": returns, "label": changed})).values == found.values
    rare = label * 0
    rare.iloc[[50, 51, 200, 201, 300]] = 1.0
    small = run("qrs.regimes", Given({"returns": returns, "label": rare}))
    assert any("The in regime has about" in w for w in small.warnings)
    ranked = run("qrs.regimes", Given({"returns": returns, "label": days(rng.normal(size=400).cumsum())}), rank=True, threshold=0.9)
    assert ranked.values["in_regime"] > 0 and ranked.values["n"] == 400 - 20  # ranks start at the 20th value; the next session is the first with a regime


# Regressions from the workstream's review.


def test_a_label_with_sessions_the_returns_lack_is_lagged_in_the_returns_sessions():
    """A label published in the gap between two return sessions must not set the regime of the
    return that spans it: the lag is one session of the returns, not of the label."""
    rng = np.random.default_rng(17)
    label = days((rng.random(1200) > 0.5).astype(float))
    kept = label.index[np.arange(1200) % 3 != 1]
    gap = label.shift(1).reindex(kept)  # the label at the session skipped just before each kept one
    returns = pd.Series(rng.normal(0, 0.01, len(kept)), index=kept) + 0.02 * gap.where(np.arange(len(kept)) % 2 == 1, 0.0).fillna(0.0)
    assert abs(run("qrs.regimes", Given({"returns": returns, "label": label})).values["estimate"]) < 0.005


def test_regimes_ranked_ignore_later_labels_and_edge_cases_give_results():
    rng = np.random.default_rng(18)
    label, returns = days(rng.normal(size=300).cumsum()), days(rng.normal(0, 0.01, 300))
    found = run("qrs.regimes", Given({"returns": returns, "label": label}), rank=True, threshold=0.7)
    later = label.copy()
    later.iloc[200:] = later.iloc[200:] * -3
    rewritten = run("qrs.regimes", Given({"returns": returns.iloc[:201], "label": later.iloc[:201]}), rank=True, threshold=0.7)
    same = run("qrs.regimes", Given({"returns": returns.iloc[:201], "label": label.iloc[:201]}), rank=True, threshold=0.7)
    assert rewritten.values["in_regime"] == same.values["in_regime"] and rewritten.values["estimate"] == pytest.approx(same.values["estimate"])
    assert found.values["p_value"] is not None
    flat = run("qrs.regimes", Given({"returns": days([0.0] * 300), "label": label}))
    assert flat.values["p_value"] is None and "do not vary" in flat.warnings[0]
    one = run("qrs.regimes", Given({"returns": returns, "label": days([0.0] * 100 + [1.0] * 150 + [0.0] * 50)}))
    assert any("only 1 episodes" in w for w in one.warnings)
    few = run("qrs.subperiods", Given({"returns": days([0.01, 0.02, 0.03])}), split="equal", parts=4)
    assert few.values["periods"] == 3 and few.values["totals_reconcile"]


def test_sub_periods_by_hand():
    returns = pd.Series([0.1, -0.1, 0.2, 0.05], index=pd.to_datetime(["2020-12-30", "2020-12-31", "2021-01-04", "2021-01-05"]))
    table = run("qrs.subperiods", Given({"returns": returns})).tables["periods"]
    assert table["total"].tolist() == pytest.approx([1.1 * 0.9 - 1, 1.2 * 1.05 - 1])
    assert table[["start", "end", "sessions"]].values.tolist() == [["2020-12-30", "2020-12-31", 2], ["2021-01-04", "2021-01-05", 2]]


def test_sensitivity_axes_must_be_distinct():
    from pydantic import ValidationError

    from qrs import methods

    with pytest.raises(ValidationError, match="axes must be distinct"):
        methods.get("qrs.sensitivity", 1).params.model_validate({"axes": ["fast.period", "fast.period"]})
