"""Tests and intervals (Stage 5 workstream 5). Returns are autocorrelated and overlapping horizons
inflate significance, so tests use Newey–West (HAC) errors and intervals the stationary bootstrap;
naive i.i.d. tests are not offered. Each method reports n and the effective n: the sample variance
over the HAC variance of the mean, the number of independent values that would give the same
precision."""

import math

import numpy as np
import pandas as pd
from pydantic import Field
from scipy import stats as distributions

from qrs import figures, stats
from qrs.methods import INPUT_KINDS, RETURNS, Result, forward, method
from qrs.methods.describe import MONTHS, monthly_returns
from qrs.registry import Params

HAC = (
    "Newey–West (HAC) standard errors with Bartlett weights, scaled by n / (n − k); Student t on n − k degrees of freedom. "
    "In autocorrelated samples these tests reject more often than their level: at 5%, for AR(1) data with φ = 0.3, 13% at "
    "n = 20, 10% at n = 100, 8% at n = 250 and 6% at n = 1,000; with φ = 0.5, about 10% at n = 100 to 250."
)
OVERLAP = (
    "With overlapping forward returns the regression rejects more often still, most with few pairs and a persistent "
    "feature: at 5%, for h = 21 and a feature with lag-1 autocorrelation 0.9, about 25% at n = 120, 19% at 250, 12% at 500 "
    "and 10% at 1,000; 12% for h = 21 with an independent feature at n = 100; 14% for h = 5 and a persistent feature at "
    "n = 250. Regressing on the feature's change, or sampling it where it changes at least h sessions apart, avoids it."
)
CONSTANT = "The values do not vary: there is nothing to test."
SEASONAL_SIZE = (
    "As F it still rejects more often than its level with few years, as HAC errors do: at 5% on independent returns, "
    "about 10–12% with 2 to 4 years for each month, 8% with 5 to 10, 7% with 12 and 6% with 20 or more."
)
SMALL = 30  # fewer values than this are warned


class LevelParams(Params):
    level: float = Field(0.95, gt=0.5, lt=1, description="The confidence level of intervals.")


class BootstrapParams(LevelParams):
    replicates: int = Field(2_000, ge=100, le=10_000, description="Stationary-bootstrap resamples.")
    seed: int = Field(0, ge=0, description="The resampling seed, recorded with the result.")


def effective_n(values: np.ndarray, se: float) -> float:
    """The sample variance over the HAC variance of the mean."""
    return float(values.var(ddof=1) / se**2) if se > 0 else float(len(values))


def _constant(n: int, extra: dict | None = None) -> Result:
    return Result(values={"n": n, "effective_n": n, "estimate": None, "p_value": None, **(extra or {})}, warnings=[CONSTANT])


def _small(n: int) -> list[str]:
    return [f"Only {n} observations: HAC errors and intervals are unreliable in small samples."] if n < SMALL else []


@method(
    "qrs.mean_test",
    1,
    title="Mean test",
    description="Whether a series' mean differs from zero: its mean, HAC standard error, interval and two-sided p-value.",
    assumptions=(HAC, "The series is stationary."),
    inputs={"values": INPUT_KINDS},
    outputs={},
    params=LevelParams,
    tests="directional",
)
def mean_test(ctx, params: LevelParams) -> Result:
    values = ctx.series("values").dropna().to_numpy()
    n = len(values)
    if n < 3:
        return Result(values={"n": n, "effective_n": n, "estimate": None, "p_value": None}, warnings=["Fewer than 3 values: nothing to test."])
    if np.ptp(values) == 0:
        return _constant(n)
    lag = stats.newey_west_lag(n)
    fit = stats.ols(values, np.ones((n, 1)), lag)
    estimate, se = math.fsum(values) / n, float(fit["se"][0])
    return Result(
        values={
            "n": n,
            "effective_n": effective_n(values, se),
            "estimate": estimate,
            "se": se,
            "t": estimate / se,
            "p_value": float(fit["p"][0]),
            "interval": stats.t_interval(estimate, se, fit["df"], params.level),
            "level": params.level,
            "lag": lag,
        },
        warnings=_small(n),
    )


class RegressionParams(LevelParams):
    horizon: int = Field(1, ge=1, le=250, description="Sessions ahead: the forward return from the close of t to the close of t + horizon.")
    sample: str = Field("sessions", pattern="^(sessions|changes)$", description="Every session, or only sessions where the feature changes (its publications).")
    difference: bool = Field(False, description="Regress on the feature's change since the previous session rather than its level.")


@method(
    "qrs.regression",
    1,
    title="Predictive regression",
    description=(
        "Regresses the return over the next `horizon` sessions on a feature at t, on every session or only where the feature "
        "changes, on its level or its change since the previous session. The estimate is the slope, with its HAC standard "
        "error, interval and two-sided p-value. Where the sampled horizons overlap, the lag is at least horizon − 1. A change "
        "is taken against the previous session's value, so none spans a gap in the feature."
    ),
    assumptions=(HAC, OVERLAP, "The relation is linear and stable over the sample."),
    inputs={"feature": INPUT_KINDS, "returns": RETURNS},
    outputs={"pairs": "rows", "scatter": "rows"},
    params=RegressionParams,
    tests="directional",
)
def regression(ctx, params: RegressionParams) -> Result:
    feature = ctx.series("feature")
    future = forward(ctx.series("returns"), params.horizon, ctx.additive("returns"))
    changed = feature.notna() & feature.ne(feature.shift(1))
    if params.difference:
        feature = feature - feature.shift(1)  # missing after a gap in the feature, not a change across it
    if params.sample == "changes":
        feature = feature[changed]
    pairs = pd.DataFrame({"feature": feature, "forward": future}).dropna()
    n = len(pairs)
    if n < 4 or pairs["feature"].nunique() < 2 or pairs["forward"].nunique() < 2:
        return Result(
            values={"n": n, "effective_n": n, "estimate": None, "p_value": None},
            warnings=["Too few pairs, or a constant feature or return: nothing to test."]
            + (
                ["The feature is missing before each of its values, so it has no changes: join it with a longer maximum age, or use its level."]
                if params.difference and feature.notna().sum() == 0
                else []
            ),
        )
    sessions = future.index.get_indexer(pairs.index)
    overlapping = params.horizon > 1 and int(np.diff(sessions).min()) < params.horizon
    lag = stats.newey_west_lag(n, params.horizon if overlapping else 1)
    x = np.column_stack([np.ones(n), pairs["feature"].to_numpy()])
    fit = stats.ols(pairs["forward"].to_numpy(), x, lag)
    slope, se = float(fit["beta"][1]), float(fit["se"][1])
    figure = figures.scatter(
        "Feature against forward return", pairs["feature"].tolist(), pairs["forward"].tolist(), "feature", f"{params.horizon}-session forward return"
    )
    persistence = float(np.corrcoef(pairs["feature"].to_numpy()[1:], pairs["feature"].to_numpy()[:-1])[0, 1])
    warnings = _small(n)
    if overlapping:
        warnings.append(f"The horizons overlap (the feature's lag-1 autocorrelation is {persistence:.2f}). {OVERLAP}")
    return Result(
        values={
            "n": n,
            # Horizons that do not overlap over the sampled span: n / h for every session, never more than n.
            "effective_n": min(n, (int(sessions[-1] - sessions[0]) + 1) / params.horizon) if overlapping else n,
            "feature_autocorrelation_1": persistence,
            "estimate": slope,
            "se": se,
            "t": slope / se,
            "p_value": float(fit["p"][1]),
            "interval": stats.t_interval(slope, se, fit["df"], params.level),
            "intercept": float(fit["beta"][0]),
            "r_squared": fit["r_squared"],
            "level": params.level,
            "lag": lag,
            "horizon": params.horizon,
        },
        warnings=warnings,
        tables={"pairs": pairs},
        figures={"scatter": figure},
    )


class EventTestParams(LevelParams):
    absolute: bool = Field(True, description="Compare absolute returns (how large) rather than returns (which way).")


@method(
    "qrs.event_test",
    1,
    title="Event test",
    description=(
        "Whether returns, or absolute returns, on event sessions (at whose close an observation was first known) differ "
        "from those on all other sessions: the difference in means, from a regression on an event indicator, with its HAC "
        "standard error, interval and two-sided p-value."
    ),
    assumptions=(HAC, "Event sessions are as the event study places them: the first close at or after each publication."),
    inputs={"returns": RETURNS, "events": ("observations",)},
    outputs={},
    params=EventTestParams,
    tests="directional",
)
def event_test(ctx, params: EventTestParams) -> Result:
    returns = ctx.series("returns").dropna()
    on_event = returns.index.isin(ctx.events("events")).astype(float)
    y = returns.abs().to_numpy() if params.absolute else returns.to_numpy()
    n, events = len(y), int(on_event.sum())
    if events < 2 or events > n - 2:
        return Result(values={"n": n, "effective_n": n, "events": events, "estimate": None, "p_value": None}, warnings=["Too few event or other sessions."])
    if np.ptp(y) == 0:
        return _constant(n, {"events": events})
    lag = stats.newey_west_lag(n)
    fit = stats.ols(y, np.column_stack([np.ones(n), on_event]), lag)
    difference, se = float(fit["beta"][1]), float(fit["se"][1])
    return Result(
        values={
            "n": n,
            "effective_n": n,
            "events": events,
            "estimate": difference,
            "se": se,
            "t": difference / se,
            "p_value": float(fit["p"][1]),
            "interval": stats.t_interval(difference, se, fit["df"], params.level),
            "mean_other": float(fit["beta"][0]),
            "mean_event": float(fit["beta"][0] + difference),
            "level": params.level,
            "lag": lag,
        },
        warnings=_small(events),
    )


@method(
    "qrs.seasonal_test",
    1,
    title="Seasonal test",
    description=(
        "Whether mean returns differ across calendar months: each month's return in each year (as the seasonal profile "
        "computes them) regressed on calendar-month indicators, and the Wald test that every month's mean is equal, with "
        "HAC covariance over the months in time order, as an F statistic on (months − 1, years − 1) degrees of freedom: each "
        "month's mean is estimated from its years, and against χ² the test rejected up to 38% of the time at 5% when nothing "
        f"differed. {SEASONAL_SIZE} Each month's mean has its own interval, on its years less one degrees of freedom."
    ),
    assumptions=(HAC, "Each calendar month's years are its observations; a partial month at either end of the sample is left out."),
    inputs={"returns": RETURNS},
    outputs={"means": "aggregates"},
    params=LevelParams,
    tests="joint",
)
def seasonal_test(ctx, params: LevelParams) -> Result:
    table = monthly_returns(ctx.series("returns").dropna(), ctx.additive("returns"))
    totals = table.stack().dropna().sort_index()  # months in time order, without the left-out ones
    months = sorted(set(totals.index.get_level_values(1)))
    n = len(totals)
    counts = totals.groupby(level=1).size()
    if len(months) < 2 or counts.min() < 2:
        return Result(values={"n": n, "effective_n": n, "p_value": None}, warnings=["Every calendar month needs at least 2 years."])
    x = (totals.index.get_level_values(1).to_numpy()[:, None] == np.array(months)).astype(float)
    lag = stats.newey_west_lag(n)
    fit = stats.ols(totals.to_numpy(), x, lag)
    restrictions = np.hstack([np.eye(len(months) - 1), -np.ones((len(months) - 1, 1))])
    statistic, df, p_value = stats.wald(fit["beta"], fit["covariance"], restrictions, df_denominator=int(counts.min()) - 1)
    # Each month's interval on its own years less one, as the F test counts them.
    intervals = [stats.t_interval(b, s, int(counts[m]) - 1, params.level) for b, s, m in zip(fit["beta"], fit["se"], months, strict=True)]
    means = pd.DataFrame(
        {"month": [MONTHS[m - 1] for m in months], "mean": fit["beta"], "se": fit["se"], "low": [i[0] for i in intervals], "high": [i[1] for i in intervals]}
    )
    figure = figures.intervals(
        "Mean return by calendar month", means["month"].tolist(), means["mean"].tolist(), means["low"].tolist(), means["high"].tolist(), "mean return"
    )
    return Result(
        values={
            "n": n,
            "effective_n": n,
            "statistic": statistic,
            "df": [df, int(counts.min()) - 1],
            "p_value": p_value,
            "years": int(counts.min()),
            "level": params.level,
            "lag": lag,
        },
        warnings=_small(int(counts.min()) * len(months))
        + ([f"Only {int(counts.min())} years for some months: expect wide intervals. {SEASONAL_SIZE}"] if counts.min() < 20 else []),
        tables={"means": means},
        figures={"means": figure},
    )


class MetricsParams(BootstrapParams):
    periods_per_year: int = Field(252, ge=1, le=366, description="Sessions in a year, to annualise.")


def _annualised(sample: np.ndarray, periods: int) -> np.ndarray:
    """Annualised mean, volatility and Sharpe ratio of each resample (axis 1: sessions)."""
    mean, std = sample.mean(axis=1), sample.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.column_stack([mean * periods, std * math.sqrt(periods), mean / std * math.sqrt(periods)])


@method(
    "qrs.return_metrics",
    1,
    title="Return metrics",
    description=(
        "Annualised mean return, volatility and Sharpe ratio, each with a stationary-bootstrap percentile interval (block "
        "length by Politis and White, corrected), the maximum drawdown as a point value (its bootstrap is unreliable), and "
        "the lag-1 autocorrelation."
    ),
    assumptions=(
        "Annualised by multiplying by the sessions in a year (the mean) or its square root (the deviation).",
        "The series is stationary; the block length is the longer of those chosen for the returns and their squares.",
    ),
    inputs={"returns": RETURNS},
    outputs={},
    params=MetricsParams,
)
def return_metrics(ctx, params: MetricsParams) -> Result:
    returns = ctx.series("returns").dropna().to_numpy()
    n = len(returns)
    if n < 10:
        return Result(values={"n": n, "effective_n": n}, warnings=["Fewer than 10 returns."])
    if np.ptp(returns) == 0:
        return Result(
            values={"n": n, "effective_n": n, "mean": float(returns[0]) * params.periods_per_year, "volatility": 0.0, "sharpe": None}, warnings=[CONSTANT]
        )
    point = _annualised(returns[None, :], params.periods_per_year)[0]
    # Volatility clusters even where returns are uncorrelated, so the block follows the squares too.
    block = max(stats.optimal_block_length(returns), stats.optimal_block_length(returns**2))
    resampled = stats.bootstrap(returns, lambda s: _annualised(s[:, :, 0], params.periods_per_year), params.replicates, params.seed, block)
    names = ("mean", "volatility", "sharpe")
    wealth = np.cumsum(returns) if ctx.additive("returns") else np.cumprod(1 + returns)
    peak = np.maximum.accumulate(np.r_[0.0 if ctx.additive("returns") else 1.0, wealth])[1:]
    drawdown = float(np.max(peak - wealth)) if ctx.additive("returns") else float(np.max(1 - wealth / peak))
    autocorrelation = float(np.corrcoef(returns[1:], returns[:-1])[0, 1])
    values = {name: float(point[i]) for i, name in enumerate(names)}
    values |= {f"{name}_interval": stats.percentile_interval(resampled[:, i][np.isfinite(resampled[:, i])], params.level) for i, name in enumerate(names)}
    values |= {"n": n, "effective_n": n, "max_drawdown": drawdown, "autocorrelation_1": autocorrelation, "block": block, "replicates": params.replicates}
    flat = int((~np.isfinite(resampled[:, 2])).sum())  # resamples with no variation have no Sharpe ratio
    values |= {"seed": params.seed, "level": params.level, "replicates_without_sharpe": flat}
    warnings = _small(n) + ([f"{flat} resamples did not vary and are left out of the Sharpe interval."] if flat else [])
    warnings.append(
        "On GARCH(1,1) returns (α = 0.08, β = 0.9, n = 2,500) the 95% volatility interval covered about 77%: volatility clustering outlasts the blocks."
    )
    return Result(values=values, warnings=warnings)


class CorrelationParams(BootstrapParams):
    horizon: int = Field(1, ge=1, le=250, description="Sessions ahead of the forward return.")


def _spearman(sample: np.ndarray) -> np.ndarray:
    ranks = distributions.rankdata(sample, axis=1)
    centred = ranks - ranks.mean(axis=1, keepdims=True)
    return (centred[:, :, 0] * centred[:, :, 1]).sum(axis=1) / np.sqrt((centred[:, :, 0] ** 2).sum(axis=1) * (centred[:, :, 1] ** 2).sum(axis=1))


@method(
    "qrs.rank_correlation",
    1,
    title="Rank correlation (IC)",
    description=(
        "Spearman's rank correlation between a feature at t and the return over the next `horizon` sessions, with a "
        "stationary-bootstrap percentile interval over the pairs, resampled together."
    ),
    assumptions=("The pairs are stationary; overlapping forward returns are kept together within resampled blocks.",),
    inputs={"feature": INPUT_KINDS, "returns": RETURNS},
    outputs={},
    params=CorrelationParams,
)
def rank_correlation(ctx, params: CorrelationParams) -> Result:
    pairs = pd.DataFrame({"feature": ctx.series("feature"), "forward": forward(ctx.series("returns"), params.horizon, ctx.additive("returns"))}).dropna()
    n = len(pairs)
    if n < 10 or pairs.nunique().min() < 2:
        return Result(values={"n": n, "effective_n": n, "spearman": None}, warnings=["Fewer than 10 pairs, or a constant series."])
    data = pairs.to_numpy()
    block = max(stats.optimal_block_length(data[:, 0]), stats.optimal_block_length(data[:, 1]), float(params.horizon))
    resampled = stats.bootstrap(data, _spearman, params.replicates, params.seed, block)
    return Result(
        values={
            "n": n,
            "effective_n": n / params.horizon,
            "spearman": float(_spearman(data[None, :, :])[0]),
            "interval": stats.percentile_interval(resampled[np.isfinite(resampled)], params.level),
            "block": block,
            "replicates": params.replicates,
            "seed": params.seed,
            "level": params.level,
            "horizon": params.horizon,
        },
        warnings=_small(n),
    )


@method(
    "qrs.paired_difference",
    1,
    title="Paired difference",
    description=(
        "Whether two return series differ on their common sessions: the mean of a − b, with its HAC standard error and "
        "two-sided p-value, and a stationary-bootstrap percentile interval."
    ),
    assumptions=(HAC, "The difference is stationary."),
    inputs={"a": RETURNS, "b": RETURNS},
    outputs={},
    params=BootstrapParams,
    tests="directional",
)
def paired_difference(ctx, params: BootstrapParams) -> Result:
    both = pd.DataFrame({"a": ctx.series("a"), "b": ctx.series("b")}).dropna()
    difference = (both["a"] - both["b"]).to_numpy()
    n = len(difference)
    if n < 10:
        return Result(values={"n": n, "effective_n": n, "estimate": None, "p_value": None}, warnings=["Fewer than 10 common sessions."])
    if np.ptp(difference) == 0:
        return _constant(n, {"difference": float(difference[0])})
    lag = stats.newey_west_lag(n)
    fit = stats.ols(difference, np.ones((n, 1)), lag)
    estimate, se = math.fsum(difference) / n, float(fit["se"][0])
    block = stats.optimal_block_length(difference)
    resampled = stats.bootstrap(difference, lambda s: s[:, :, 0].mean(axis=1), params.replicates, params.seed, block)
    return Result(
        values={
            "n": n,
            "effective_n": effective_n(difference, se),
            "estimate": estimate,
            "se": se,
            "p_value": float(fit["p"][0]),
            "interval": stats.percentile_interval(resampled, params.level),
            "first": both.index[0].date().isoformat(),
            "last": both.index[-1].date().isoformat(),
            "block": block,
            "replicates": params.replicates,
            "seed": params.seed,
            "level": params.level,
            "lag": lag,
        },
        warnings=_small(n),
    )
