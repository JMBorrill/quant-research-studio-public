"""Stage 5 workstream 5: tests, intervals and multiple-testing adjustment. qrs.stats is checked against
reference values from statsmodels and arch (tests/reference/inference.json, written by
tests/reference/inference.py) and published worked examples; interval coverage is measured on AR(1)
data; each method against hand calculations; and a family's conclusions are derived from its
adjusted p-values, withdrawn and failed members counting as p = 1."""

import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.signal import lfilter
from scipy.stats import f as f_distribution
from scipy.stats import t as t_distribution

from conftest import client_for
from qrs import methods, projects, stats
from qrs.contracts import FamilyDocument
from qrs.methods.describe import MONTHS, monthly_returns
from test_analysis import document, wait_analysis
from test_describe import Given, days, run
from test_experiments import holdout_start
from test_projects import family_doc, new_project, synthetic

REFERENCE = json.loads((Path(__file__).parent / "reference" / "inference.json").read_text())
SERIES = {name: np.array(REFERENCE[name]) for name in ("ar", "x", "y")}


def close(found, expected, rel=1e-9):
    assert np.allclose(np.asarray(found, dtype=float), np.asarray(expected, dtype=float), rtol=rel, atol=0), (found, expected)


def test_hac_regression_and_wald_match_statsmodels():
    ar, x, y = SERIES["ar"], SERIES["x"], SERIES["y"]
    n = len(ar)
    mean = stats.ols(ar, np.ones((n, 1)), 5)
    close(mean["beta"], REFERENCE["mean"]["beta"]), close(mean["se"], REFERENCE["mean"]["se"]), close(mean["p"], REFERENCE["mean"]["p"])
    fit = stats.ols(y, np.column_stack([np.ones(n), x]), 7)
    close(fit["beta"], REFERENCE["regression"]["beta"]), close(fit["se"], REFERENCE["regression"]["se"]), close(fit["p"], REFERENCE["regression"]["p"])
    close(fit["r_squared"], REFERENCE["regression"]["r2"])
    groups = stats.ols(ar, np.eye(4)[np.arange(n) % 4], 4)
    restrictions = np.hstack([np.eye(3), -np.ones((3, 1))])
    statistic, df, p = stats.wald(groups["beta"], groups["covariance"], restrictions)
    close([statistic, p], [REFERENCE["wald"]["statistic"], REFERENCE["wald"]["p"]])
    f, q, _ = stats.wald(groups["beta"], groups["covariance"], restrictions, df_denominator=74)
    close(f, REFERENCE["wald"]["f"])
    assert df == q == 3


def test_adjustments_match_statsmodels_and_the_benjamini_hochberg_worked_example():
    for correction in ("holm", "bh", "by"):
        close(stats.adjust(REFERENCE["p_values"], correction), REFERENCE[correction])
    # Benjamini and Hochberg (1995), section 4: of these 15 p-values at 0.05, Holm's procedure rejects 3
    # and theirs rejects 4.
    published = [0.0001, 0.0004, 0.0019, 0.0095, 0.0201, 0.0278, 0.0298, 0.0344, 0.0459, 0.3240, 0.4262, 0.5719, 0.6528, 0.7590, 1.0]
    assert sum(p < 0.05 for p in stats.adjust(published, "holm")) == 3
    assert sum(p < 0.05 for p in stats.adjust(published, "bh")) == 4


def test_block_length_matches_arch_and_the_newey_west_lag_follows_the_plan():
    close(stats.optimal_block_length(SERIES["ar"]), REFERENCE["block"]["ar"], rel=1e-12)
    close(stats.optimal_block_length(SERIES["x"]), REFERENCE["block"]["x"], rel=1e-12)
    # floor(4 (n/100)^(2/9)): 4 at n = 100, 6 at 1,000; at least h − 1 for overlapping h-session returns.
    assert (stats.newey_west_lag(100), stats.newey_west_lag(1000), stats.newey_west_lag(1000, horizon=21)) == (4, 6, 20)


def test_the_reality_check_matches_arch_within_monte_carlo_error():
    """arch's Reality Check resamples with its own generator, so p-values agree only to Monte Carlo
    error: at 20,000 replicates each has a standard error of at most 0.0035, so 0.02 is over 4
    standard errors of their difference."""
    reference = REFERENCE["reality_check"]
    ar, x, y = SERIES["ar"], SERIES["x"], SERIES["y"]
    for shift, expected in zip(reference["shifts"], reference["p"], strict=True):
        found = stats.reality_check(np.column_stack([ar * 0.5 + shift, x, y - y.mean() + shift / 2]), reference["reps"], seed=1)
        close(found["block"], reference["block"], rel=1e-12)
        assert abs(found["p_value"] - expected) < 0.02, (shift, found["p_value"], expected)


def test_stationary_bootstrap_resamples_wrapping_blocks_of_geometric_length_reproducibly():
    rng = np.random.default_rng(3)
    indices = np.concatenate(list(stats.stationary_indices(1000, 20.0, 400, rng)))
    assert indices.shape == (400, 1000) and indices.min() == 0 and indices.max() == 999
    continued = (indices[:, 1:] == (indices[:, :-1] + 1) % 1000).mean()
    assert continued == pytest.approx(1 - 1 / 20, abs=0.005)  # a new block starts with probability 1 / block
    assert (np.diff(indices, axis=1) == -999).any()  # blocks wrap round the end
    draw = lambda: stats.bootstrap(SERIES["ar"], lambda s: s[:, :, 0].mean(axis=1), 300, seed=11)  # noqa: E731
    assert np.array_equal(draw(), draw())


def ar1(rng: np.random.Generator, n: int, phi: float = 0.5) -> np.ndarray:
    return lfilter([1], [1, -phi], rng.normal(size=n + 200))[200:]


def test_interval_coverage_on_ar1_data_meets_the_plans_bounds():
    """AR(1), φ = 0.5, n = 1,000, true mean 0: the HAC and stationary-bootstrap 95% intervals cover at
    least 88% of the time, and more often than the naive i.i.d. interval (measured: 92.8%, 93.5% and
    73.8%)."""
    rng, n = np.random.default_rng(7), 1000
    hac = naive = 0
    for _ in range(400):
        x = ar1(rng, n)
        low, high = stats.t_interval(x.mean(), stats.ols(x, np.ones((n, 1)), stats.newey_west_lag(n))["se"][0], n - 1, 0.95)
        hac += low <= 0 <= high
        naive += abs(x.mean()) <= 1.96 * x.std(ddof=1) / math.sqrt(n)
    covered = 0
    for seed in range(200):
        low, high = stats.percentile_interval(stats.bootstrap(ar1(rng, n), lambda s: s[:, :, 0].mean(axis=1), 499, seed), 0.95)
        covered += low <= 0 <= high
    assert hac / 400 >= 0.88 and covered / 200 >= 0.88 and hac / 400 > naive / 400 and covered / 200 > naive / 400


def test_tests_hold_their_size_when_nothing_differs():
    """Rejections at 5% with no effect: the seasonal F test on 20 years of months (400 samples), and
    the mean test on 250 sessions of AR(1) returns, φ = 0.3 (1,000 samples). Against χ² the seasonal
    test rejected 19%."""
    rng = np.random.default_rng(1)
    rejected = 0
    for _ in range(400):
        x = (np.arange(240)[:, None] % 12 == np.arange(12)).astype(float)
        fit = stats.ols(rng.normal(size=240), x, stats.newey_west_lag(240))
        rejected += stats.wald(fit["beta"], fit["covariance"], np.hstack([np.eye(11), -np.ones((11, 1))]), df_denominator=19)[2] < 0.05
    assert 0.02 <= rejected / 400 <= 0.09
    rejected = sum(stats.ols(ar1(rng, 250, 0.3), np.ones((250, 1)), stats.newey_west_lag(250))["p"][0] < 0.05 for _ in range(1000))
    assert 0.02 <= rejected / 1000 <= 0.095  # measured 7.7% over 2,000 samples, as the methods state


def test_mean_test_and_paired_difference_by_hand():
    values = days([0.01, -0.02, 0.03, 0.0, 0.02, -0.01, 0.04, 0.01, -0.03, 0.02, 0.01, 0.0])
    found = run("qrs.mean_test", Given({"values": values})).values
    fit = stats.ols(values.to_numpy(), np.ones((12, 1)), 2)  # floor(4 (12 / 100)^(2/9)) = 2
    assert found["estimate"] == pytest.approx(values.mean()) and found["se"] == pytest.approx(fit["se"][0]) and found["lag"] == 2
    assert found["p_value"] == pytest.approx(fit["p"][0]) and found["interval"][0] < found["estimate"] < found["interval"][1]
    paired = run("qrs.paired_difference", Given({"a": values, "b": values - 0.005}), replicates=200).values
    assert paired["estimate"] == pytest.approx(0.005) and paired["interval"] == pytest.approx((0.005, 0.005))


def test_regression_recovers_a_known_slope_and_samples_publications():
    rng = np.random.default_rng(4)
    feature = days(rng.normal(size=600))
    returns = (0.5 * feature.shift(1) + rng.normal(0, 0.1, 600)).fillna(0.0)  # next session's return follows today's feature
    found = run("qrs.regression", Given({"feature": feature, "returns": returns}, additive=True), horizon=1)
    assert found.values["estimate"] == pytest.approx(0.5, abs=0.02) and found.values["p_value"] < 1e-10
    weekly = days(np.repeat(rng.normal(size=120).cumsum(), 5))  # changes every 5 sessions
    sampled = run("qrs.regression", Given({"feature": weekly, "returns": returns}, additive=True), horizon=5, sample="changes", difference=True)
    assert sampled.values["n"] == 118 and len(sampled.tables["pairs"]) == 118  # 120 changes, less the first (no change) and the last (no return)


def test_event_test_and_seasonal_test_detect_planted_effects_and_not_absent_ones():
    rng = np.random.default_rng(5)
    returns = days(rng.normal(0, 0.01, 2000), start="2000-01-03")
    events = returns.index[::20]
    loud = returns.copy()
    loud[events] *= 4
    assert run("qrs.event_test", Given({"returns": loud}, events=events)).values["p_value"] < 1e-6
    assert run("qrs.event_test", Given({"returns": returns}, events=events)).values["p_value"] > 0.01
    planted = returns + np.where(returns.index.month == 7, 0.01, 0.0)  # July gains about 23% more, in each of 8 years
    assert run("qrs.seasonal_test", Given({"returns": planted})).values["p_value"] < 1e-3
    flat = run("qrs.seasonal_test", Given({"returns": returns}))
    assert flat.values["p_value"] > 0.01 and flat.values["df"] == [11, 6] and len(flat.tables["means"]) == 12
    # The p-value is F's on those degrees of freedom, not χ²'s, which over-rejects with few years.
    assert flat.values["p_value"] == pytest.approx(f_distribution.sf(flat.values["statistic"], 11, 6), rel=1e-12)
    # With few years it still over-rejects, and says by how much.
    assert any("7 years" in w and "8% with 5 to 10" in w for w in flat.warnings)


def test_return_metrics_and_rank_correlation_by_hand_and_within_budget():
    returns = days(np.tile([0.01, -0.005, 0.002, -0.001], 50))
    found = run("qrs.return_metrics", Given({"returns": returns}), replicates=200).values
    mean, std = returns.mean(), returns.std(ddof=1)
    assert found["mean"] == pytest.approx(mean * 252) and found["volatility"] == pytest.approx(std * math.sqrt(252))
    assert found["sharpe"] == pytest.approx(mean / std * math.sqrt(252)) and found["sharpe_interval"][0] <= found["sharpe"] <= found["sharpe_interval"][1]
    # The deepest fall from a peak of wealth: each cycle gains, so the worst is the first −0.5%, then −0.1% after 0.2%.
    assert found["max_drawdown"] == pytest.approx(1 - 0.995)
    ic = run("qrs.rank_correlation", Given({"feature": returns.shift(-1).fillna(0.0), "returns": returns}, additive=True), replicates=200).values
    assert ic["spearman"] > 0.9 and ic["interval"][0] > 0.5
    long = days(np.random.default_rng(6).normal(0, 0.01, 12_500), start="1975-01-01")
    start = time.perf_counter()
    run("qrs.return_metrics", Given({"returns": long}), replicates=2_000)
    assert time.perf_counter() - start < 15  # the plan's budget for 2,000 replicates on 12,500 sessions


def outcome(estimate, p_value, status="succeeded") -> dict:
    return {"analysis": {"status": status}, "result": None if status != "succeeded" else {"values": {"estimate": estimate, "p_value": p_value}}}


def test_a_familys_conclusions_follow_its_adjusted_p_values():
    hypotheses = [
        {"id": h, "statement": h, "direction": direction, "analysis": document({"values": {"kind": "bars", "dataset": "sha256:" + "0" * 64}}, "qrs.mean_test")}
        for h, direction in (("up", "greater"), ("gone", "greater"), ("broke", "greater"), ("wrong_way", "greater"), ("either", "two_sided"))
    ]
    doc = FamilyDocument(project="0" * 12, name="f", confirmatory=True, hypotheses=hypotheses, correction="holm", alpha=0.05)
    evaluations = [
        {"hypothesis": "up"} | outcome(0.2, 0.004),
        {"hypothesis": "broke"} | outcome(None, None, status="failed"),
        {"hypothesis": "wrong_way"} | outcome(-0.2, 0.004),
        {"hypothesis": "either"} | outcome(-0.1, 0.006),
    ]
    pending = projects.conclusions(doc, [], evaluations)
    assert not pending["complete"] and "adjusted_p_value" not in pending["outcomes"][0]
    found = projects.conclusions(doc, [{"hypothesis": "gone"}], evaluations)
    by_id = {o["hypothesis"]: o for o in found["outcomes"]}
    # One-sided p-values 0.002 (up), 1, 1, 1 − 0.002 (wrong way) and 0.006 (two-sided): Holm multiplies
    # the smallest by 5 and the next by 4.
    assert [by_id[h]["state"] for h in ("up", "gone", "broke", "wrong_way", "either")] == ["evaluated", "withdrawn", "failed", "evaluated", "evaluated"]
    assert by_id["up"]["adjusted_p_value"] == pytest.approx(0.01) and by_id["either"]["adjusted_p_value"] == pytest.approx(0.024)
    assert [h for h, o in by_id.items() if o["supported"]] == ["up", "either"] and found["complete"]
    assert projects.conclusions(doc.model_copy(update={"correction": "bh"}), [{"hypothesis": "gone"}], evaluations)["outcomes"][4][
        "adjusted_p_value"
    ] == pytest.approx(0.015)


def test_a_registered_family_is_evaluated_end_to_end_and_concludes(home):
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        project_id = new_project(client, [dataset], start_date)
        spec = document({"values": {"kind": "bars", "dataset": dataset}}, "qrs.mean_test").model_dump(mode="json")
        joint = document({"returns": {"kind": "bars", "dataset": dataset}}, "qrs.seasonal_test").model_dump(mode="json")
        refused = client.post("/api/families", json=family_doc(project_id, [{"id": "joint", "statement": "s", "analysis": joint, "direction": "greater"}]))
        assert refused.status_code == 422 and "a joint test has no direction" in refused.json()["detail"]
        summary = document({"values": {"kind": "bars", "dataset": dataset}}).model_dump(mode="json")
        refused = client.post("/api/families", json=family_doc(project_id, [{"id": "d", "statement": "s", "analysis": summary, "direction": "greater"}]))
        assert "qrs.summary@1 tests nothing" in refused.json()["detail"]
        hypotheses = [{"id": "up", "statement": "Returns rise.", "analysis": spec, "direction": "greater"}]
        hypotheses += [{"id": "down", "statement": "Returns fall.", "analysis": spec, "direction": "less"}]
        family = client.post("/api/families", json=family_doc(project_id, hypotheses)).json()
        for h in ("up", "down"):
            evaluation = client.post(f"/api/families/{family['id']}/evaluate", json={"hypothesis": h}).json()["evaluations"][-1]
            wait_analysis(client, evaluation["analysis"]["id"])
        shown = client.get(f"/api/families/{family['id']}").json()
        assert shown["complete"] and shown["confirmatory"] and not shown["compromised"]
        up, down = shown["outcomes"]
        assert up["p_value"] + down["p_value"] == pytest.approx(1.0)  # the same test, in opposite directions
        assert up["adjusted_p_value"] == pytest.approx(min(1.0, 2 * min(up["p_value"], down["p_value"])) if up["p_value"] < down["p_value"] else 1.0)
        assert [e["result"]["values"]["n"] for e in shown["evaluations"]] == [100, 100]  # the test period: the sessions from the confirmation start


# Regressions from the workstream's review.


def test_a_persistent_feature_with_overlapping_horizons_is_warned_with_its_measured_size():
    rng = np.random.default_rng(12)
    persistent = days(lfilter([1], [1, -0.9], rng.normal(size=600)))
    found = run("qrs.regression", Given({"feature": persistent, "returns": days(rng.normal(0, 0.01, 600))}, additive=True), horizon=5)
    assert found.values["lag"] == stats.newey_west_lag(found.values["n"], 5) and found.values["feature_autocorrelation_1"] > 0.8
    assert "horizons overlap" in found.warnings[-1] and "25% at n = 120" in found.warnings[-1]
    # Its change is barely persistent, but its horizons overlap all the same: still warned.
    changes = run("qrs.regression", Given({"feature": persistent, "returns": days(rng.normal(0, 0.01, 600))}, additive=True), horizon=5, difference=True)
    assert any("horizons overlap" in w for w in changes.warnings)


def test_changes_at_least_a_horizon_apart_take_the_plans_lag_and_never_span_a_gap():
    """Sampled where the feature changes, h or more sessions apart, the horizons do not overlap: the lag
    is the plan's, not h − 1, which would shrink the errors. A change is against the previous session,
    so the first value after a gap in the feature is no change at all."""
    rng = np.random.default_rng(13)
    monthly = pd.Series(np.repeat(rng.normal(size=30), 21), dtype=float)
    monthly[210:252] = np.nan  # two months without a value
    feature, returns = days(monthly.to_numpy()), days(rng.normal(0, 0.01, len(monthly)))
    found = run("qrs.regression", Given({"feature": feature, "returns": returns}, additive=True), horizon=21, sample="changes", difference=True)
    n = found.values["n"]
    assert found.values["lag"] == stats.newey_west_lag(n) and found.values["effective_n"] == n
    assert not any("horizons overlap" in w for w in found.warnings)
    after_gap = feature.index[252]
    assert after_gap not in found.tables["pairs"].index and n == 30 - 1 - 2 - 1 - 1  # less the first, the gap, the change after it, the last
    # A feature missing between its values has no changes, and says so rather than spanning the gaps.
    sparse = feature.where(np.arange(len(feature)) % 21 == 0)
    empty = run("qrs.regression", Given({"feature": sparse, "returns": returns}, additive=True), horizon=21, sample="changes", difference=True)
    assert empty.values["p_value"] is None and any("has no changes" in w for w in empty.warnings)
    # Overlapping sessions count as their span over h (609 sessions, the gap included), never more than n.
    dense = run("qrs.regression", Given({"feature": feature, "returns": returns}, additive=True), horizon=21)
    pairs = dense.tables["pairs"].index
    assert dense.values["effective_n"] == pytest.approx((returns.index.get_loc(pairs[-1]) - returns.index.get_loc(pairs[0]) + 1) / 21) == pytest.approx(29)
    # A constant return, as from a run that never traded, gives no test rather than failing.
    flat = run("qrs.regression", Given({"feature": feature, "returns": returns * 0}, additive=True), horizon=21, sample="changes")
    assert flat.values["p_value"] is None


def test_the_event_test_is_the_difference_in_means_by_hand():
    returns = days([0.01, -0.03, 0.02, -0.01, 0.04, 0.0, -0.02, 0.01, 0.03, -0.01] * 3)
    events = returns.index[::5]
    found = run("qrs.event_test", Given({"returns": returns}, events=events)).values
    on = returns.index.isin(events)
    assert found["estimate"] == pytest.approx(returns.abs()[on].mean() - returns.abs()[~on].mean())
    assert found["mean_event"] == pytest.approx(returns.abs()[on].mean()) and found["events"] == 6


def test_holm_with_ties_and_a_running_evaluation():
    assert stats.adjust([0.01, 0.01, 0.04], "holm") == pytest.approx([0.03, 0.03, 0.04])
    hypotheses = [
        {
            "id": "a",
            "statement": "a",
            "direction": "greater",
            "analysis": document({"values": {"kind": "bars", "dataset": "sha256:" + "0" * 64}}, "qrs.mean_test"),
        }
    ]
    doc = FamilyDocument(project="0" * 12, name="f", confirmatory=True, hypotheses=hypotheses)
    found = projects.conclusions(doc, [], [{"hypothesis": "a", "analysis": {"status": "running"}, "result": None}])
    assert found["outcomes"][0]["state"] == "running" and not found["complete"]


@pytest.mark.parametrize(
    "method_id", ["qrs.mean_test", "qrs.event_test", "qrs.regression", "qrs.return_metrics", "qrs.paired_difference", "qrs.rank_correlation"]
)
def test_constant_inputs_give_no_test_rather_than_failing(method_id):
    flat = days([0.0] * 60)
    quick = {"replicates": 100} if "replicates" in methods.get(method_id, 1).params.model_fields else {}
    found = run(method_id, Given({"values": flat, "returns": flat, "feature": flat, "a": flat, "b": flat}, events=flat.index[::7]), **quick)
    assert found.values.get("p_value") is None and found.warnings


def test_drawdown_in_price_units_and_a_paired_bootstrap_interval():
    additive = run("qrs.return_metrics", Given({"returns": days([5.0, -3.0, -4.0, 6.0, 1.0] * 4)}, additive=True), replicates=100).values
    assert additive["max_drawdown"] == pytest.approx(7.0)  # from 5 down to −2 in currency per contract
    rng = np.random.default_rng(13)
    a = days(rng.normal(0.001, 0.01, 300))
    paired = run("qrs.paired_difference", Given({"a": a, "b": days(rng.normal(0, 0.01, 300))}), replicates=500).values
    assert paired["interval"][0] < paired["estimate"] < paired["interval"][1] and paired["block"] >= 1


def test_block_lengths_follow_every_series_resampled_and_month_intervals_their_own_years():
    """The volatility interval's block follows the squared returns as well (GARCH-like clustering
    lives there); the information coefficient's follows both series; each month's interval is on
    its own years less one."""
    rng = np.random.default_rng(16)
    clustered = days(np.repeat(rng.normal(size=60), 10) * rng.normal(0, 0.01, 600))  # volatility in runs of 10
    metrics = run("qrs.return_metrics", Given({"returns": clustered}), replicates=100).values
    assert metrics["block"] == max(stats.optimal_block_length(clustered.to_numpy()), stats.optimal_block_length(clustered.to_numpy() ** 2))
    assert metrics["block"] > stats.optimal_block_length(clustered.to_numpy())
    feature = days(lfilter([1], [1, -0.9], rng.normal(size=600)))
    returns = days(rng.normal(0, 0.01, 600))
    ic = run("qrs.rank_correlation", Given({"feature": feature, "returns": returns}, additive=True), replicates=100, horizon=1)
    assert ic.values["block"] > stats.optimal_block_length(returns.to_numpy())  # the persistent feature's, not the returns'
    years = days(rng.normal(0, 0.01, 1300), start="2000-01-03")  # about five years: some months have one fewer
    seasonal = run("qrs.seasonal_test", Given({"returns": years}))
    means = seasonal.tables["means"]
    totals = monthly_returns(years, False).stack()
    for _, row in means.iterrows():
        count = int((totals.index.get_level_values(1) == MONTHS.index(row["month"]) + 1).sum())
        assert row["high"] - row["mean"] == pytest.approx(t_distribution.ppf(0.5 + seasonal.values["level"] / 2, count - 1) * row["se"], rel=1e-12)
