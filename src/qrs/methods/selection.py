"""Selection bias (Stage 5 workstream 6): best-of-N results carry N. The probabilistic and deflated
Sharpe ratios (Bailey and López de Prado) and White's Reality Check, over an experiment's trials.

N is every trial of every experiment on the same holdout window (the same bars and holdout start),
failed ones too, except experiments whose axes vary only backtest costs, which choose no strategy."""

import math

import numpy as np
from pydantic import Field

from qrs import stats
from qrs.methods import EXPERIMENT, RETURNS, Result, method
from qrs.registry import Params

INDEPENDENT = "Returns are serially independent; a significant lag-1 autocorrelation is warned."


class SharpeParams(Params):
    benchmark: float = Field(0.0, description="The annualised Sharpe ratio to exceed.")
    periods_per_year: int = Field(252, ge=1, le=366, description="Sessions in a year, to annualise.")


def _moments(returns: np.ndarray, periods: int) -> tuple[dict, list[str]]:
    sharpe, skewness, kurtosis = stats.sharpe_moments(returns)
    autocorrelation = float(np.corrcoef(returns[1:], returns[:-1])[0, 1])
    warnings = []
    if abs(autocorrelation) > 2 / math.sqrt(len(returns)):
        warnings.append(f"The lag-1 autocorrelation is {autocorrelation:.3f}, significant at about 5%: the ratio assumes independent returns.")
    values = {"sharpe": sharpe * math.sqrt(periods), "skewness": skewness, "kurtosis": kurtosis, "autocorrelation_1": autocorrelation, "sessions": len(returns)}
    return values, warnings


@method(
    "qrs.probabilistic_sharpe",
    1,
    title="Probabilistic Sharpe ratio",
    description=(
        "The probability that a return series' true Sharpe ratio exceeds a benchmark, allowing for the sample's length, "
        "skewness and kurtosis (Bailey and López de Prado, 2012)."
    ),
    assumptions=(INDEPENDENT,),
    inputs={"returns": RETURNS},
    outputs={},
    params=SharpeParams,
)
def probabilistic_sharpe(ctx, params: SharpeParams) -> Result:
    returns = ctx.series("returns").dropna().to_numpy()
    if len(returns) < 10 or returns.std() == 0:
        return Result(values={"n": len(returns), "effective_n": len(returns)}, warnings=["Fewer than 10 returns, or no variation."])
    values, warnings = _moments(returns, params.periods_per_year)
    benchmark = params.benchmark / math.sqrt(params.periods_per_year)
    psr = stats.probabilistic_sharpe(values["sharpe"] / math.sqrt(params.periods_per_year), benchmark, len(returns), values["skewness"], values["kurtosis"])
    return Result(
        values={"n": len(returns), "effective_n": len(returns), **values, "benchmark": params.benchmark, "probabilistic_sharpe": psr}, warnings=warnings
    )


@method(
    "qrs.deflated_sharpe",
    1,
    title="Deflated Sharpe ratio",
    description=(
        "For the trial an experiment ranked first by its objective: its Sharpe ratio over the trials' common window, and "
        "the probability that it exceeds the largest Sharpe ratio expected by chance from N trials (Bailey and López de "
        "Prado, 2014). N counts every trial on the holdout window, failed ones too, except searches over costs alone; the "
        "variance of Sharpe ratios is taken across this experiment's succeeded trials."
    ),
    assumptions=(
        INDEPENDENT,
        "It applies to the selection the experiment made, by its objective, which is not the Sharpe ratio.",
        "The variance of Sharpe ratios comes from this experiment's trials, which, being alike, may understate it.",
    ),
    inputs={"trials": EXPERIMENT},
    outputs={},
    params=SharpeParams,
)
def deflated_sharpe(ctx, params: SharpeParams) -> Result:
    trials = ctx.experiment("trials")
    selected = int(trials.returns.columns[0])
    returns = trials.returns[selected].to_numpy()
    own = next(w for w in trials.window if w["experiment"] == ctx.doc.inputs["trials"].experiment)
    base = {"n": len(returns), "effective_n": len(returns), "selected_trial": selected, "trials": trials.n, "window": trials.window}
    if own["costs_only"] or np.ptp(returns) == 0:
        why = "The experiment varies only costs, so it chooses no strategy" if own["costs_only"] else "The chosen trial never traded"
        return Result(values=base | {"deflated_sharpe": None}, warnings=[f"{why}: there is no selection to deflate."])
    sharpes = [stats.sharpe_moments(trials.returns[c].to_numpy())[0] for c in trials.returns.columns]
    if len(sharpes) < 2:
        return Result(
            values=base | {"deflated_sharpe": None}, warnings=["Only one trial succeeded: the variance of Sharpe ratios, which deflation needs, is unknown."]
        )
    values, warnings = _moments(returns, params.periods_per_year)
    variance = float(np.var(sharpes, ddof=1))
    deflated = stats.expected_maximum_sharpe(variance, trials.n)
    idle = sum(np.ptp(trials.returns[c].to_numpy()) == 0 for c in trials.returns.columns)
    if idle:
        warnings.append(f"{idle} trials never traded; their Sharpe ratio is taken as 0.")
    if len(sharpes) < 10:
        warnings.append(f"The variance of Sharpe ratios rests on {len(sharpes)} trials, and neighbouring trials are alike, so it may understate the deflation.")
    dsr = stats.probabilistic_sharpe(values["sharpe"] / math.sqrt(params.periods_per_year), deflated, len(returns), values["skewness"], values["kurtosis"])
    warnings.append(f"The experiment ranked its trials by {trials.objective} ({trials.direction}), not by Sharpe ratio; this is the trial it chose.")
    return Result(
        values=base
        | {
            "succeeded": len(sharpes),
            **values,
            "sharpe_variance": variance * params.periods_per_year,
            "expected_maximum_sharpe": deflated * math.sqrt(params.periods_per_year),
            "deflated_sharpe": dsr,
            "probabilistic_sharpe": stats.probabilistic_sharpe(
                values["sharpe"] / math.sqrt(params.periods_per_year), 0.0, len(returns), values["skewness"], values["kurtosis"]
            ),
        },
        warnings=warnings,
    )


class RealityParams(Params):
    replicates: int = Field(1_000, ge=100, le=10_000, description="Stationary-bootstrap resamples.")
    seed: int = Field(0, ge=0, description="The resampling seed, recorded with the result.")


@method(
    "qrs.reality_check",
    1,
    title="Reality Check",
    description=(
        "White's (2000) Reality Check: whether the best of an experiment's succeeded trials beats a zero return over their "
        "common window by more than the best of them would by chance, with one stationary-bootstrap resampling shared by "
        "every trial. Failed trials have no returns to resample, but are counted and listed."
    ),
    assumptions=(
        "The trials' returns are stationary over the window.",
        "The benchmark is a zero return.",
        "In finite samples it rejects somewhat more often than its level: at 5%, measured 6% for independent returns "
        "(250 sessions) and 9% for AR(1) returns with φ = 0.2 (250 sessions; 7% at 1,000).",
    ),
    inputs={"trials": EXPERIMENT},
    outputs={},
    params=RealityParams,
)
def reality_check(ctx, params: RealityParams) -> Result:
    trials = ctx.experiment("trials")
    found = stats.reality_check(trials.returns.to_numpy(), params.replicates, params.seed)
    unsucceeded = [{"trial": t["trial"], "status": t["status"]} for t in trials.trials if t["status"] != "succeeded"]
    return Result(
        values={
            "n": len(trials.returns),
            "effective_n": len(trials.returns),
            "statistic": found["statistic"],
            "p_value": found["p_value"],
            "best_trial": int(trials.returns.columns[found["best"]]),
            "succeeded": trials.returns.shape[1],
            "trials": trials.n,
            "window": trials.window,
            "not_succeeded": unsucceeded,
            "block": found["block"],
            "replicates": params.replicates,
            "seed": params.seed,
        },
        warnings=([f"{len(unsucceeded)} trials did not succeed: counted in N, with no returns to resample."] if unsucceeded else [])
        + (
            [f"The p-value covers this experiment's {len(trials.trials)} trials; {trials.n} were tried on its window in all, which it does not cover."]
            if trials.n > len(trials.trials) and not next(w for w in trials.window if w["experiment"] == ctx.doc.inputs["trials"].experiment)["costs_only"]
            else []
        ),
    )
