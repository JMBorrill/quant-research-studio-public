"""Technical indicators. Each convention is fixed by the operator version (docs/decisions.md §9) and
checked against hand-calculated values in tests/test_operators.py. Outputs are NaN during warm-up."""

from typing import Literal

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from pydantic import Field, model_validator

from qrs.registry import Params, Temporal, operator

PriceColumn = Literal["open", "high", "low", "close"]


def window_stats(values: np.ndarray, period: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean and population standard deviation of the last `period` values; NaN for the first
    period - 1. Each window is measured from its first value, so a constant window gives exactly
    that value and a deviation of 0 even for prices that are not exact in binary, such as cents."""
    mean, deviation = np.full(len(values), np.nan), np.full(len(values), np.nan)
    if len(values) >= period:
        windows = sliding_window_view(values, period)
        offsets = windows - windows[:, :1]
        mean[period - 1 :] = windows[:, 0] + offsets.mean(axis=1)
        deviation[period - 1 :] = offsets.std(axis=1)
    return mean, deviation


def ema_values(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average with smoothing 2 / (period + 1), seeded with the simple average of
    the first `period` valid values and recursive after that, with no bias adjustment. Leading NaNs
    (the input's own warm-up) are skipped, so the output's warm-up adds period - 1 to the input's."""
    out = np.full(len(values), np.nan)
    valid = ~np.isnan(values)
    start = int(valid.argmax()) if valid.any() else len(values)
    if not valid[start:].all():
        raise ValueError("Values must not be missing after their warm-up.")
    if len(values) - start >= period:
        alpha = 2 / (period + 1)
        seed = start + period - 1
        out[seed] = values[start : seed + 1].mean()
        for i in range(seed + 1, len(values)):
            out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def prices(inputs: dict, column: str) -> np.ndarray:
    return inputs["bars"].frame[column].to_numpy(dtype=float)


def series(inputs: dict, values: np.ndarray) -> pd.Series:
    return pd.Series(values, index=inputs["bars"].frame.index)


class ColumnParams(Params):
    column: PriceColumn = "close"


@operator(
    "qrs.column",
    1,
    title="Price",
    description="One price column of the bars as a series, e.g. to compare the close with a band. No warm-up.",
    family="indicators",
    inputs={"bars": "bars"},
    outputs={"series": "series"},
    params=ColumnParams,
    temporal=Temporal(warm_up=lambda p: 0),
)
def column(ctx, inputs, params: ColumnParams):
    return {"series": series(inputs, prices(inputs, params.column))}


class PeriodParams(Params):
    period: int = Field(20, ge=1, le=1000, description="Number of sessions.")
    column: PriceColumn = "close"


@operator(
    "qrs.sma",
    1,
    title="SMA",
    description="Simple moving average: the mean of the last `period` sessions. Warm-up: period - 1 sessions.",
    family="indicators",
    inputs={"bars": "bars"},
    outputs={"series": "series"},
    params=PeriodParams,
    temporal=Temporal(warm_up=lambda p: p.period - 1),
)
def sma(ctx, inputs, params: PeriodParams):
    return {"series": series(inputs, window_stats(prices(inputs, params.column), params.period)[0])}


class EmaParams(Params):
    period: int = Field(20, ge=1, le=1000, description="Number of sessions; smoothing factor 2 / (period + 1).")
    column: PriceColumn = "close"


@operator(
    "qrs.ema",
    1,
    title="EMA",
    description=(
        "Exponential moving average. Seeded with the simple average of the first `period` values; "
        "the first period - 1 sessions are warm-up (NaN). Recursive, no bias adjustment."
    ),
    family="indicators",
    inputs={"bars": "bars"},
    outputs={"series": "series"},
    params=EmaParams,
    temporal=Temporal(warm_up=lambda p: p.period - 1),
)
def ema(ctx, inputs, params: EmaParams):
    return {"series": series(inputs, ema_values(prices(inputs, params.column), params.period))}


class BollingerParams(Params):
    period: int = Field(20, ge=2, le=1000, description="Sessions in the moving average and the deviation.")
    k: float = Field(2.0, gt=0, le=10, description="Band width in population standard deviations.")
    column: PriceColumn = "close"


@operator(
    "qrs.bollinger",
    1,
    title="Bollinger Bands",
    description=(
        "Middle band: SMA of the last `period` sessions. Upper and lower bands: middle ± k × the population "
        "standard deviation (divisor `period`) of the same sessions. Warm-up: period - 1 sessions."
    ),
    family="indicators",
    inputs={"bars": "bars"},
    outputs={"upper": "series", "middle": "series", "lower": "series"},
    params=BollingerParams,
    temporal=Temporal(warm_up=lambda p: p.period - 1),
)
def bollinger(ctx, inputs, params: BollingerParams):
    middle, deviation = window_stats(prices(inputs, params.column), params.period)
    return {
        "upper": series(inputs, middle + params.k * deviation),
        "middle": series(inputs, middle),
        "lower": series(inputs, middle - params.k * deviation),
    }


class RsiParams(Params):
    period: int = Field(14, ge=1, le=1000, description="Number of price changes averaged.")
    column: PriceColumn = "close"


@operator(
    "qrs.rsi",
    1,
    title="RSI",
    description=(
        "Relative strength index with Wilder smoothing. The first value, at session `period`, uses the simple "
        "averages of the first `period` gains and losses; later averages are (previous × (period - 1) + latest) / "
        "period. RSI = 100 - 100 / (1 + average gain / average loss); 100 when the average loss is 0, 50 when "
        "both averages are 0. The averages never reset, so after any move a flat market holds the RSI at its "
        "last value. Warm-up: period sessions."
    ),
    family="indicators",
    inputs={"bars": "bars"},
    outputs={"series": "series"},
    params=RsiParams,
    temporal=Temporal(warm_up=lambda p: p.period),
)
def rsi(ctx, inputs, params: RsiParams):
    values, n = prices(inputs, params.column), params.period
    out = np.full(len(values), np.nan)
    changes = np.diff(values)  # changes[i - 1] is the change into session i
    gains, losses = np.maximum(changes, 0), np.maximum(-changes, 0)
    for i in range(n, len(values)):
        if i == n:
            average_gain, average_loss = gains[:n].mean(), losses[:n].mean()
        else:
            average_gain = (average_gain * (n - 1) + gains[i - 1]) / n
            average_loss = (average_loss * (n - 1) + losses[i - 1]) / n
        if average_loss == 0:
            out[i] = 50.0 if average_gain == 0 else 100.0
        else:
            out[i] = 100 - 100 / (1 + average_gain / average_loss)
    return {"series": series(inputs, out)}


class MacdParams(Params):
    fast: int = Field(12, ge=1, le=1000, description="Sessions in the fast EMA.")
    slow: int = Field(26, ge=2, le=1000, description="Sessions in the slow EMA; must exceed fast.")
    signal: int = Field(9, ge=1, le=1000, description="Sessions in the EMA of the MACD line.")
    column: PriceColumn = "close"

    @model_validator(mode="after")
    def _fast_shorter_than_slow(self):
        if self.fast >= self.slow:
            raise ValueError("fast must be shorter than slow")
        return self


@operator(
    "qrs.macd",
    1,
    title="MACD",
    description=(
        "MACD line: EMA(fast) - EMA(slow), using the EMA convention; valid from session slow - 1. Signal: EMA of "
        "the MACD line over `signal` sessions, seeded from its first `signal` valid values. The signal and the "
        "histogram (line - signal) are valid from session slow + signal - 2."
    ),
    family="indicators",
    inputs={"bars": "bars"},
    outputs={"macd": "series", "signal": "series", "histogram": "series"},
    params=MacdParams,
    temporal=Temporal(warm_up=lambda p: {"macd": p.slow - 1, "signal": p.slow + p.signal - 2, "histogram": p.slow + p.signal - 2}),
)
def macd(ctx, inputs, params: MacdParams):
    values = prices(inputs, params.column)
    line = ema_values(values, params.fast) - ema_values(values, params.slow)
    signal = ema_values(line, params.signal)
    return {"macd": series(inputs, line), "signal": series(inputs, signal), "histogram": series(inputs, line - signal)}


def simple_returns(values: pd.Series, sessions: int) -> pd.Series:
    """The return over the last `sessions` sessions: price / price `sessions` earlier - 1. NaN where
    there is no earlier price. Shared by the past-return feature and the forward-return label."""
    return values / values.shift(sessions) - 1


class ReturnsParams(Params):
    period: int = Field(1, ge=1, le=1000, description="Sessions back.")
    column: PriceColumn = "close"


@operator(
    "qrs.returns",
    1,
    title="Return",
    description="The return over the last `period` sessions, as a fraction: price / price `period` sessions earlier - 1. Warm-up: period.",
    family="indicators",
    inputs={"bars": "bars"},
    outputs={"series": "series"},
    params=ReturnsParams,
    temporal=Temporal(warm_up=lambda p: p.period),
)
def returns(ctx, inputs, params: ReturnsParams):
    refuse_continuous(inputs["bars"])
    return {"series": simple_returns(series(inputs, prices(inputs, params.column)), params.period)}


def refuse_continuous(bars) -> None:
    """A continuous series' adjusted levels make percentages meaningless (Stage 5 amendment A3)."""
    if bars.instrument.continuous is not None:
        raise ValueError("A continuous series' adjusted prices give no meaningful percentage returns; use its price changes.")


class RollingParams(Params):
    period: int = Field(20, ge=2, le=1000, description="Number of sessions.")


@operator(
    "qrs.rolling_std",
    1,
    title="Rolling deviation",
    description=(
        "The population standard deviation of a series over the last `period` sessions, as Bollinger bands measure it; "
        "missing while any value in the window is. Of returns, it measures volatility. Warm-up: period - 1."
    ),
    family="indicators",
    inputs={"series": "series"},
    outputs={"series": "series"},
    params=RollingParams,
    temporal=Temporal(warm_up=lambda p: p.period - 1),
)
def rolling_std(ctx, inputs, params: RollingParams):
    values = inputs["series"]
    return {"series": pd.Series(window_stats(values.to_numpy(dtype=float), params.period)[1], index=values.index)}


class RankParams(Params):
    min_periods: int = Field(20, ge=1, le=10_000, description="Values needed before the first rank.")


@operator(
    "qrs.expanding_rank",
    1,
    title="Expanding rank",
    description=(
        "Each value's percentile rank among all the series' values so far, itself included: from just above 0 (the lowest "
        "yet) to 1 (the highest), ties sharing their average rank. Missing values are left out. Above 0.5 means above "
        "the median so far. Warm-up: min_periods - 1."
    ),
    family="indicators",
    inputs={"series": "series"},
    outputs={"series": "series"},
    params=RankParams,
    temporal=Temporal(warm_up=lambda p: p.min_periods - 1),
)
def expanding_rank(ctx, inputs, params: RankParams):
    return {"series": expanding_ranks(inputs["series"], params.min_periods)}


def expanding_ranks(values: pd.Series, min_periods: int) -> pd.Series:
    """Each value's percentile rank among the values up to it; shared by the operator and the regime method."""
    return values.expanding(min_periods=min_periods).rank(pct=True).astype(float)
