"""Descriptive methods (Stage 5): they summarise data, and test nothing."""

import numpy as np
import pandas as pd
from pydantic import Field
from scipy import stats

from qrs import figures
from qrs.methods import INPUT_KINDS, RETURNS, Result, forward, method, total
from qrs.operators.indicators import window_stats
from qrs.registry import Params

QUANTILES = (0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DESCRIBES = ("Describes the sample only: no adjustment for dependence, so n and the effective n are the same.",)


@method(
    "qrs.summary",
    1,
    title="Summary",
    description=(
        "Count, missing values, mean, sample standard deviation, skewness and excess kurtosis (both bias-corrected, "
        "as Excel's SKEW and KURT), and quantiles (linear interpolation) of one series."
    ),
    assumptions=DESCRIBES,
    inputs={"values": INPUT_KINDS},
    outputs={},
    params=Params,
)
def summary(ctx, params: Params) -> Result:
    series = ctx.series("values")
    values = series.dropna().to_numpy()
    n = len(values)
    warnings = [] if n >= 4 else [f"Only {n} values: skewness needs 3 and kurtosis 4."]
    return Result(
        values={
            "n": n,
            "effective_n": n,
            "missing": int(series.isna().sum()),
            "mean": float(values.mean()) if n else None,
            "std": float(values.std(ddof=1)) if n >= 2 else None,
            "skewness": float(stats.skew(values, bias=False)) if n >= 3 else None,
            "excess_kurtosis": float(stats.kurtosis(values, bias=False)) if n >= 4 else None,
            "quantiles": {str(q): float(np.quantile(values, q)) for q in QUANTILES} if n else None,
        },
        warnings=warnings,
    )


@method(
    "qrs.coverage",
    1,
    title="Coverage",
    description=(
        "What a dataset holds up to the cutoff. Bars: sessions, first and last, and the expected sessions their import "
        "found missing. Observations: rows, events, revisions, rows with estimated publication times, and the "
        "publication lag from each event date to its publication, in days."
    ),
    assumptions=DESCRIBES,
    inputs={"data": ("bars", "observations")},
    outputs={"missing": "aggregates"},
    params=Params,
)
def coverage(ctx, params: Params) -> Result:
    manifest, frame = ctx.rows("data")
    if manifest.kind == "qrs/dataset":
        first, last = frame.index[0], frame.index[-1]
        report = manifest.import_report
        missing = [d for d in (report.missing_sessions if report else []) if first.date() <= d <= last.date()]
        values = {"sessions": len(frame), "first": first.date().isoformat(), "last": last.date().isoformat(), "missing_sessions": len(missing)}
        values["calendar"] = report.calendar if report else "not checked (no import report)"
        return Result(values=values | {"n": len(frame), "effective_n": len(frame)}, tables={"missing": pd.DataFrame({"session": missing})})
    lag = (frame["published"] - frame["event"].dt.tz_localize("UTC")).dt.total_seconds() / 86_400
    events = frame["event"].nunique()
    values = {
        "n": len(frame),
        "effective_n": len(frame),
        "rows": len(frame),
        "events": events,
        "revisions": len(frame) - events,
        "estimated_rows": int(frame["estimated"].sum()),
        "first_published": frame["published"].min().isoformat() if len(frame) else None,
        "last_published": frame["published"].max().isoformat() if len(frame) else None,
        "lag_days": {"median": float(lag.median()), "max": float(lag.max()), "min": float(lag.min())} if len(frame) else None,
    }
    return Result(values=values, tables={"missing": pd.DataFrame({"session": []})})


class DistributionParams(Params):
    bins: int = Field(30, ge=5, le=200, description="Equal-width bins between the smallest and largest value.")


@method(
    "qrs.distribution",
    1,
    title="Distribution",
    description="A histogram of one series: equal-width bins between its smallest and largest value, and the count in each.",
    assumptions=DESCRIBES,
    inputs={"values": INPUT_KINDS},
    outputs={"histogram": "aggregates"},
    params=DistributionParams,
)
def distribution(ctx, params: DistributionParams) -> Result:
    values = ctx.series("values").dropna().to_numpy()
    if not len(values):
        return Result(values={"n": 0, "effective_n": 0}, warnings=["No values to count."])
    counts, edges = np.histogram(values, bins=params.bins)
    table = pd.DataFrame({"low": edges[:-1], "high": edges[1:], "count": counts})
    figure = figures.bar("Distribution", [figures.label(e) for e in edges[:-1]], counts.astype(float).tolist(), "count")
    return Result(
        values={"n": len(values), "effective_n": len(values), "min": float(values.min()), "max": float(values.max()), "bins": params.bins},
        tables={"histogram": table},
        figures={"histogram": figure},
    )


class RollingParams(Params):
    period: int = Field(63, ge=2, le=1000, description="Sessions in each window.")


@method(
    "qrs.rolling",
    1,
    title="Rolling mean and volatility",
    description=(
        "The mean and population standard deviation of one series over each window of `period` sessions, as the Rolling "
        "deviation operator (qrs.rolling_std) computes it; missing while a window lacks a value."
    ),
    assumptions=DESCRIBES,
    inputs={"values": INPUT_KINDS},
    outputs={"rolling": "rows"},
    params=RollingParams,
)
def rolling(ctx, params: RollingParams) -> Result:
    series = ctx.series("values")
    mean, deviation = window_stats(series.to_numpy(dtype=float), params.period)
    table = pd.DataFrame({"mean": mean, "std": deviation}, index=series.index)
    labels = [d.date().isoformat() for d in series.index]
    figure = figures.line(f"Rolling {params.period}-session mean and deviation", labels, {"mean": mean.tolist(), "std": deviation.tolist()}, "value")
    windows = int(np.isfinite(deviation).sum())
    warnings = [] if windows else [f"No complete window of {params.period} sessions."]
    return Result(
        values={"n": int(series.notna().sum()), "effective_n": windows, "windows": windows},
        warnings=warnings,
        tables={"rolling": table},
        figures={"rolling": figure},
    )


@method(
    "qrs.seasonal",
    1,
    title="Seasonal profile",
    description=(
        "Each calendar month's return in each year (compounded fractions, or summed price changes), and the mean, sample "
        "deviation and number of years for each calendar month. The sample's first or last month is left out when it is "
        "partial (fewer than 80% of the median sessions of its months)."
    ),
    assumptions=("Months are summarised, not tested: each calendar month's years are its observations.",),
    inputs={"returns": RETURNS},
    outputs={"monthly": "aggregates", "profile": "aggregates"},
    params=Params,
)
def seasonal(ctx, params: Params) -> Result:
    table = monthly_returns(ctx.series("returns").dropna(), ctx.additive("returns"))
    profile = {MONTHS[m - 1]: {"mean": _mean(table[m]), "std": _std(table[m]), "years": int(table[m].notna().sum())} for m in table.columns}
    figure = figures.bar("Mean return by calendar month", list(profile), [p["mean"] for p in profile.values()], "mean return")
    years = min((p["years"] for p in profile.values()), default=0)
    warnings = [f"Some calendar months have only {years} years."] if years < 10 else []
    return Result(
        values={"n": int(table.notna().sum().sum()), "effective_n": int(table.notna().sum().sum()), "months": profile},
        warnings=warnings,
        tables={"monthly": table.rename(columns=lambda m: MONTHS[m - 1])},
        figures={"profile": figure},
    )


def monthly_returns(returns: pd.Series, additive: bool) -> pd.DataFrame:
    """Years by calendar month (1 to 12): each month's total return. The sample's first or last month
    is left out when it is partial: when it has fewer than 80% of the median sessions of its months."""
    grouped = returns.groupby([returns.index.year, returns.index.month])
    months, sessions = grouped.apply(lambda r: total(r, additive)), grouped.size()
    short = sessions < 0.8 * sessions.median()
    edges = [months.index[0]] * bool(short.iloc[0]) + [months.index[-1]] * bool(short.iloc[-1])
    months = months.drop(index=edges)
    table = months.unstack()
    table.index.name, table.columns.name = "year", None
    return table.reindex(columns=range(1, 13))


def _mean(values: pd.Series) -> float | None:
    values = values.dropna()
    return float(values.mean()) if len(values) else None


def _std(values: pd.Series) -> float | None:
    values = values.dropna()
    return float(values.std(ddof=1)) if len(values) >= 2 else None


class EventParams(Params):
    before: int = Field(5, ge=0, le=60, description="Sessions shown before each event session.")
    after: int = Field(5, ge=0, le=60, description="Sessions shown after it.")


@method(
    "qrs.event_study",
    1,
    title="Event study",
    description=(
        "Returns around the sessions at whose close each value of an observations column was first known (the event "
        "sessions): the mean and mean absolute return at each offset, and the mean absolute return on event sessions "
        "and on all others."
    ),
    assumptions=("Describes returns around events; it tests nothing. An event published after a close belongs to the next session.",),
    inputs={"returns": RETURNS, "events": ("observations",)},
    outputs={"profile": "aggregates"},
    params=EventParams,
)
def event_study(ctx, params: EventParams) -> Result:
    returns = ctx.series("returns").dropna()
    events = ctx.events("events")
    positions = returns.index.get_indexer(events)
    used = positions[positions >= 0]
    offsets = range(-params.before, params.after + 1)
    rows = []
    for offset in offsets:
        at = used + offset
        values = returns.to_numpy()[at[(at >= 0) & (at < len(returns))]]
        rows.append({"offset": offset, "mean": _mean(pd.Series(values)), "mean_abs": _mean(pd.Series(np.abs(values))), "events": len(values)})
    profile = pd.DataFrame(rows)
    on_event = np.zeros(len(returns), dtype=bool)
    on_event[used] = True
    absolute = returns.abs()
    figure = figures.bar("Mean absolute return around events", [str(o) for o in offsets], profile["mean_abs"].tolist(), "mean absolute return")
    warnings = [] if len(used) == len(events) else [f"{len(events) - len(used)} event sessions have no return, so are left out."]
    return Result(
        values={
            "n": len(returns),
            "effective_n": len(returns),
            "events": len(used),
            "mean_abs_event": _mean(absolute[on_event]),
            "mean_abs_other": _mean(absolute[~on_event]),
        },
        warnings=warnings,
        tables={"profile": profile},
        figures={"profile": figure},
    )


class HorizonParams(Params):
    horizon: int = Field(1, ge=1, le=250, description="Sessions ahead: the forward return runs from the close of t to the close of t + horizon.")


@method(
    "qrs.feature_return",
    1,
    title="Feature against forward return",
    description=(
        "Pairs a feature at each session t with the return over the next `horizon` sessions, where every one of them is "
        "read, and gives Spearman's rank correlation and a scatter plot."
    ),
    assumptions=("Describes the association; it tests nothing. Forward returns over more than one session overlap, so pairs are not independent.",),
    inputs={"feature": INPUT_KINDS, "returns": RETURNS},
    outputs={"pairs": "rows", "scatter": "rows"},
    params=HorizonParams,
)
def feature_return(ctx, params: HorizonParams) -> Result:
    pairs = pd.DataFrame({"feature": ctx.series("feature"), "forward": forward(ctx.series("returns"), params.horizon, ctx.additive("returns"))}).dropna()
    n = len(pairs)
    constant = n >= 3 and pairs.nunique().min() == 1
    rho = float(stats.spearmanr(pairs["feature"], pairs["forward"]).statistic) if n >= 3 and not constant else None
    warnings = [f"Overlapping {params.horizon}-session returns: about {n // params.horizon} of the {n} pairs are independent."] if params.horizon > 1 else []
    warnings += ["The feature or the forward return is constant, so they have no rank correlation."] if constant else []
    figure = figures.scatter(
        "Feature against forward return", pairs["feature"].tolist(), pairs["forward"].tolist(), "feature", f"{params.horizon}-session forward return"
    )
    return Result(
        values={"n": n, "effective_n": n // params.horizon, "horizon": params.horizon, "spearman": rho},
        warnings=warnings + ([] if n >= 3 else ["Fewer than 3 pairs."]),
        tables={"pairs": pairs},
        figures={"scatter": figure},
    )
