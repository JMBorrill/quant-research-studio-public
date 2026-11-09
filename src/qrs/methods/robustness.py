"""Robustness, sensitivity and regimes (Stage 5 workstream 7). Cost sensitivity needs no method of its
own: an experiment over commission_per_contract and slippage_ticks, read by the sensitivity method."""

import math

import numpy as np
import pandas as pd
from pydantic import Field, field_validator

from qrs import figures, stats
from qrs.methods import EXPERIMENT, INPUT_KINDS, RETURNS, Result, method, total
from qrs.methods.inference import CONSTANT, HAC, LevelParams, effective_n
from qrs.operators.indicators import expanding_ranks
from qrs.registry import Params

INDEPENDENT_REGIME = 30  # regimes with fewer effectively independent returns are warned


class SensitivityParams(Params):
    axes: list[str] = Field(
        default_factory=list, max_length=2, description="One or two axes, as node.param; by default the experiment's own, if it has at most two."
    )

    @field_validator("axes")
    @classmethod
    def _distinct(cls, axes: list[str]) -> list[str]:
        if len(set(axes)) < len(axes):
            raise ValueError("axes must be distinct")
        return axes


@method(
    "qrs.sensitivity",
    1,
    title="Parameter sensitivity",
    description=(
        "An experiment's objective over one axis (a line) or two (a heatmap), each point the trial's own metric over the "
        "trials' common window; other axes are held at the best trial's values. The best trial's neighbours, one step "
        "along each axis, are listed beside it. A search over costs shows cost sensitivity."
    ),
    assumptions=("Shows the surface the search measured; it tests nothing.",),
    inputs={"trials": EXPERIMENT},
    outputs={"surface": "aggregates", "figure": "aggregates"},
    params=SensitivityParams,
)
def sensitivity(ctx, params: SensitivityParams) -> Result:
    trials = ctx.experiment("trials")
    shown = params.axes or list(trials.axes)
    unknown = sorted(set(shown) - set(trials.axes))
    if unknown or not 1 <= len(shown) <= 2:
        return Result(values={"n": 0, "effective_n": 0}, warnings=[f"Choose one or two of the axes {sorted(trials.axes)}; not {shown}."])
    points = {t["trial"]: t["point"] for t in trials.trials}
    best = int(trials.returns.columns[0])
    held = {axis: value for axis, value in points[best].items() if axis not in shown}
    rows = [
        {**points[number], "trial": number, trials.objective: trials.objectives.get(number)}
        for number in sorted(points)
        if all(points[number][axis] == value for axis, value in held.items())
    ]
    surface = pd.DataFrame(rows)[["trial", *shown, trials.objective]]
    warnings = [] if len(surface) > 1 else ["Only the best trial has these values of the other axes (a random search?): there is no surface to show."]
    neighbours = []
    for axis in shown:
        values, at = trials.axes[axis], trials.axes[axis].index(points[best][axis])
        for step in (-1, 1):
            if 0 <= at + step < len(values):
                match = surface[
                    (surface[axis] == values[at + step]) & np.logical_and.reduce([surface[a] == points[best][a] for a in shown if a != axis] or [True])
                ]
                neighbours += [
                    {"axis": axis, "value": values[at + step], "trial": int(r.trial), "objective": getattr(r, trials.objective)} for r in match.itertuples()
                ]
    title = f"{trials.objective} by {' and '.join(shown)}"
    if len(shown) == 1:
        ordered = surface.set_index(shown[0]).reindex(trials.axes[shown[0]])
        figure = figures.line(title, [str(v) for v in ordered.index], {trials.objective: ordered[trials.objective].astype(float).tolist()}, trials.objective)
    else:
        grid = surface.pivot_table(index=shown[0], columns=shown[1], values=trials.objective, aggfunc="first").reindex(
            index=trials.axes[shown[0]], columns=trials.axes[shown[1]]
        )
        figure = figures.heatmap(title, [str(v) for v in grid.index], [str(v) for v in grid.columns], grid.astype(float).values.tolist(), shown[1], shown[0])
    return Result(
        values={
            "n": len(surface),
            "effective_n": len(surface),
            "objective": trials.objective,
            "best_trial": best,
            "best_objective": trials.objectives[best],
            "held": held,
            "neighbours": neighbours,
            "trials": trials.n,
        },
        warnings=warnings,
        tables={"surface": surface},
        figures={"figure": figure},
    )


class SubperiodParams(Params):
    split: str = Field("years", pattern="^(years|equal)$", description="Calendar years, or equal numbers of sessions.")
    parts: int = Field(4, ge=2, le=20, description="With equal splits: how many.")
    periods_per_year: int = Field(252, ge=1, le=366, description="Sessions in a year, to annualise the ratios.")


@method(
    "qrs.subperiods",
    1,
    title="Sub-period results",
    description=(
        "Returns by calendar year or by equal splits: each period's sessions, total return (summed price changes or "
        "compounded fractions), annualised mean and volatility, and Sharpe ratio. Sessions and totals reconcile with the "
        "whole (totals by sum, or by product for fractions); the ratios do not, and are not reconciled."
    ),
    assumptions=("Describes how results vary over time; it tests nothing.",),
    inputs={"returns": RETURNS},
    outputs={"periods": "aggregates", "totals": "aggregates"},
    params=SubperiodParams,
)
def subperiods(ctx, params: SubperiodParams) -> Result:
    returns = ctx.series("returns").dropna()
    additive = ctx.additive("returns")
    if len(returns) < 2:
        return Result(values={"n": len(returns), "effective_n": len(returns)}, warnings=["Fewer than 2 returns."])
    if params.split == "years":
        groups = [(str(year), part) for year, part in returns.groupby(returns.index.year)]
    else:
        parts = min(params.parts, len(returns))
        groups = [(f"part {i + 1}", returns.iloc[rows]) for i, rows in enumerate(np.array_split(np.arange(len(returns)), parts))]
    rows = []
    for name, part in groups:
        std = part.std(ddof=1) if len(part) > 1 else float("nan")
        mean = part.mean()
        rows.append(
            {
                "period": name,
                "start": part.index[0].date().isoformat(),
                "end": part.index[-1].date().isoformat(),
                "sessions": len(part),
                "total": total(part, additive),
                "mean": mean * params.periods_per_year,
                "volatility": std * math.sqrt(params.periods_per_year),
                "sharpe": mean / std * math.sqrt(params.periods_per_year) if std > 0 else float("nan"),
            }
        )
    table = pd.DataFrame(rows)
    whole = total(returns, additive)
    combined = float(table["total"].sum()) if additive else float(np.prod(1 + table["total"].to_numpy()) - 1)
    figure = figures.bar("Total return by period", table["period"].tolist(), table["total"].tolist(), "total return")
    return Result(
        values={
            "n": len(returns),
            "effective_n": len(returns),
            "periods": len(table),
            "total": whole,
            "reconciled_total": combined,
            "sessions_reconcile": int(table["sessions"].sum()) == len(returns),
            "totals_reconcile": math.isclose(combined, whole, rel_tol=1e-9, abs_tol=1e-12),
        },
        tables={"periods": table},
        figures={"totals": figure},
    )


class RegimeParams(LevelParams):
    threshold: float = Field(0.5, description="A session is in the regime when the label at the close before it is above this.")
    rank: bool = Field(False, description="Compare the label's expanding percentile rank (0 to 1) with the threshold, rather than the label itself.")
    min_periods: int = Field(20, ge=1, le=10_000, description="With rank: values needed before the first rank, as the Expanding rank operator.")


@method(
    "qrs.regimes",
    1,
    title="Regime-conditional returns",
    description=(
        "Returns on sessions whose previous close had the label above a threshold (in the regime) and on the others: each "
        "regime's sessions, mean and HAC interval, and the difference in means with its HAC standard error and two-sided "
        "p-value. The label is lagged one session of the returns' own sessions, so a session's regime is known at the "
        "close before it; with rank, the label's expanding percentile rank over its whole history read, as the Expanding "
        "rank operator computes it, even when the sample starts later. A regime with fewer than 30 effectively independent returns, or "
        "fewer than 5 episodes, is warned."
    ),
    assumptions=(HAC, "The label is computed causally: a recorded signal or series, or an observations column joined as of each close."),
    inputs={"returns": RETURNS, "label": INPUT_KINDS},
    outputs={"regimes": "aggregates", "means": "aggregates"},
    params=RegimeParams,
    tests="directional",
)
def regimes(ctx, params: RegimeParams) -> Result:
    # Ranked over the label's whole history read, and lagged over the returns' whole sessions, before
    # the sample starts at `since`: both causal, so nothing before it is lost to warm-up.
    label = ctx.series("label", whole=True)
    if params.rank:
        label = expanding_ranks(label.dropna(), params.min_periods).reindex(label.index)
    returns = ctx.series("returns", whole=True)
    # Lagged in the returns' own sessions: the label at the close the return starts from.
    previous = ctx.from_since(label.reindex(returns.index).shift(1))
    returns = ctx.from_since(returns)
    sample = pd.DataFrame({"r": returns, "in": previous.gt(params.threshold).astype(float)})[previous.notna() & returns.notna()]
    n, inside = len(sample), int(sample["in"].sum())
    if inside < 2 or inside > n - 2:
        return Result(
            values={"n": n, "effective_n": n, "in_regime": inside, "estimate": None, "p_value": None}, warnings=["Too few sessions in or out of the regime."]
        )
    if np.ptp(sample["r"].to_numpy()) == 0:
        return Result(values={"n": n, "effective_n": n, "in_regime": inside, "estimate": None, "p_value": None}, warnings=[CONSTANT])
    lag = stats.newey_west_lag(n)
    fit = stats.ols(sample["r"].to_numpy(), np.column_stack([np.ones(n), sample["in"].to_numpy()]), lag)
    difference, se = float(fit["beta"][1]), float(fit["se"][1])
    rows, warnings = [], []
    for name, flag in (("in", 1.0), ("out", 0.0)):
        part = sample.loc[sample["in"] == flag, "r"].to_numpy()
        part_fit = stats.ols(part, np.ones((len(part), 1)), stats.newey_west_lag(len(part)))
        part_se = float(part_fit["se"][0])
        effective = effective_n(part, part_se)
        low, high = stats.t_interval(part.mean(), part_se, len(part) - 1, params.level)
        rows.append({"regime": name, "sessions": len(part), "effective_n": effective, "mean": part.mean(), "se": part_se, "low": low, "high": high})
        if effective < INDEPENDENT_REGIME:
            warnings.append(f"The {name} regime has about {effective:.0f} effectively independent returns, fewer than {INDEPENDENT_REGIME}.")
    table = pd.DataFrame(rows)
    figure = figures.intervals(
        "Mean return by regime", table["regime"].tolist(), table["mean"].tolist(), table["low"].tolist(), table["high"].tolist(), "mean return"
    )
    episodes = int((sample["in"].diff().fillna(sample["in"].iloc[0]) == 1).sum())
    if episodes < 5:
        warnings.append(f"The regime occurs in only {episodes} episodes: its returns are hardly independent of each other.")
    return Result(
        values={
            "n": n,
            "effective_n": n,
            "in_regime": inside,
            "episodes": episodes,
            "estimate": difference,
            "se": se,
            "t": difference / se,
            "p_value": float(fit["p"][1]),
            "interval": stats.t_interval(difference, se, fit["df"], params.level),
            "level": params.level,
            "lag": lag,
        },
        warnings=warnings,
        tables={"regimes": table},
        figures={"means": figure},
    )
