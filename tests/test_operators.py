"""Indicator, rule and position-policy conventions against hand-calculated values."""

import math

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from conftest import INSTRUMENT, ohlc
from qrs import registry
from qrs.datasets import synthetic_bars
from qrs.operators.indicators import ema_values
from qrs.ports import Bars

INDEX = pd.bdate_range("2026-01-05", periods=7, name="session")


def run(op_id, inputs, **params):
    op = registry.get(op_id, 1)
    return op.run(None, inputs, op.params.model_validate(params))


def bars_with_closes(closes):
    index = INDEX[: len(closes)]
    return Bars(ohlc(closes, closes, index), INSTRUMENT, "sha256:" + "0" * 64)


def test_ema_is_seeded_with_simple_average_and_warm_up_is_nan():
    # alpha = 2 / (3 + 1) = 0.5; seed = (10 + 11 + 12) / 3 = 11
    # then 0.5*13 + 0.5*11 = 12; 0.5*12 + 0.5*12 = 12; 0.5*11 + 0.5*12 = 11.5
    out = run("qrs.ema", {"bars": bars_with_closes([10, 11, 12, 13, 12, 11])}, period=3)["series"]
    assert math.isnan(out.iloc[0]) and math.isnan(out.iloc[1])
    assert out.iloc[2:].tolist() == [11.0, 12.0, 12.0, 11.5]


def test_ema_edge_periods():
    closes = [10, 11, 12.5]
    assert run("qrs.ema", {"bars": bars_with_closes(closes)}, period=1)["series"].tolist() == closes
    assert run("qrs.ema", {"bars": bars_with_closes(closes)}, period=4)["series"].isna().all()


def test_ema_reads_the_requested_column():
    bars = bars_with_closes([10, 11, 12])
    highs = run("qrs.ema", {"bars": bars}, period=1, column="high")["series"]
    assert highs.tolist() == [10.5, 11.5, 12.5]


def test_cross_needs_two_valid_sessions_and_counts_equality_before():
    # Session 0 has no A; session 1 has no previous A, so both are missing.
    a = pd.Series([np.nan, 1, 2, 3, 2, 2, 3], index=INDEX, dtype=float)
    b = pd.Series([1, 1, 2, 2, 2, 3, 2], index=INDEX, dtype=float)
    above = run("qrs.cross", {"a": a, "b": b}, direction="above")["signal"]
    below = run("qrs.cross", {"a": a, "b": b}, direction="below")["signal"]
    assert above.tolist() == [pd.NA, pd.NA, False, True, False, False, True]
    assert below.tolist() == [pd.NA, pd.NA, False, False, False, True, False]


def test_long_flat_exit_wins_and_does_not_pyramid():
    entry = pd.Series([False, True, True, False, True, True, False], index=INDEX, dtype="boolean")
    exit_ = pd.Series([False, False, False, True, True, False, True], index=INDEX, dtype="boolean")
    targets = run("qrs.long_flat", {"entry": entry, "exit": exit_}, quantity=2)["positions"]
    assert targets.tolist() == [0, 2, 2, 0, 0, 2, 0]
    assert targets.dtype == "int64"


def test_price_column_is_the_bars_column():
    bars = bars_with_closes([10, 11, 12])
    assert run("qrs.column", {"bars": bars}, column="high")["series"].tolist() == [10.5, 11.5, 12.5]


def test_sma_is_the_mean_of_the_last_period_sessions():
    # (10+11+12)/3 = 11, (11+12+13)/3 = 12, (12+13+12)/3 = 37/3, (13+12+11)/3 = 12
    out = run("qrs.sma", {"bars": bars_with_closes([10, 11, 12, 13, 12, 11])}, period=3)["series"]
    assert out.iloc[:2].isna().all() and out.iloc[2:].tolist() == [11.0, 12.0, 37 / 3, 12.0]


def test_bollinger_bands_use_the_population_standard_deviation():
    # Each window of [1, 3, 1, 3, ...] has mean 2 and population deviation 1 (sample deviation
    # would be sqrt(4/3)), so the bands are 2 ± k.
    bars = bars_with_closes([1, 3, 1, 3, 1, 3])
    out = run("qrs.bollinger", {"bars": bars}, period=4, k=2)
    assert all(out[name].iloc[:3].isna().all() for name in ("upper", "middle", "lower"))
    assert (out["upper"].iloc[3:].tolist(), out["middle"].iloc[3:].tolist(), out["lower"].iloc[3:].tolist()) == ([4.0] * 3, [2.0] * 3, [0.0] * 3)
    narrow = run("qrs.bollinger", {"bars": bars}, period=4, k=1.5)
    assert (narrow["upper"].iloc[3], narrow["lower"].iloc[3]) == (3.5, 0.5)


def test_rsi_uses_wilder_smoothing():
    # Changes into sessions 1-6: +1, +1, -1, +2, 0, -1.
    # Session 3: average gain (1+1+0)/3 = 2/3, average loss (0+0+1)/3 = 1/3, RS 2 -> 100 - 100/3 = 200/3.
    # Session 4: gain (2/3*2 + 2)/3 = 10/9, loss (1/3*2 + 0)/3 = 2/9, RS 5 -> 250/3.
    # Session 5: gain 20/27, loss 4/27, RS 5 -> 250/3.
    # Session 6: gain 40/81, loss (4/27*2 + 1)/3 = 35/81, RS 8/7 -> 160/3.
    # Thirds are not exact in binary, so these values compare within 1e-12.
    out = run("qrs.rsi", {"bars": bars_with_closes([10, 11, 12, 11, 13, 13, 12])}, period=3)["series"]
    assert out.iloc[:3].isna().all()
    assert out.iloc[3:].tolist() == pytest.approx([200 / 3, 250 / 3, 250 / 3, 160 / 3], rel=1e-12)


def test_moving_average_and_bands_are_exact_on_a_flat_cent_priced_stretch():
    # 101.13 is not exact in binary; a naive window mean gives 101.13000000000002.
    closes = [101.13] * 5
    assert run("qrs.sma", {"bars": bars_with_closes(closes)}, period=3)["series"].iloc[2:].tolist() == [101.13] * 3
    bands = run("qrs.bollinger", {"bars": bars_with_closes(closes)}, period=3, k=2)
    assert bands["upper"].iloc[2:].tolist() == bands["lower"].iloc[2:].tolist() == [101.13] * 3


def test_rsi_holds_its_value_through_a_flat_stretch_after_moves():
    # Zero changes scale both Wilder averages by (n - 1)/n, leaving their ratio unchanged.
    out = run("qrs.rsi", {"bars": bars_with_closes([10, 11, 10, 11, 12, 12, 12])}, period=3)["series"]
    assert out.iloc[4] == out.iloc[5] == out.iloc[6] != 50


def test_rsi_is_100_without_losses_and_50_without_movement():
    rising = run("qrs.rsi", {"bars": bars_with_closes([10, 11, 12, 13, 14])}, period=3)["series"]
    flat = run("qrs.rsi", {"bars": bars_with_closes([10] * 5)}, period=3)["series"]
    assert rising.iloc[3:].tolist() == [100.0, 100.0] and flat.iloc[3:].tolist() == [50.0, 50.0]


def test_macd_line_signal_and_histogram():
    # EMA(2), alpha 2/3, from session 1: 10.5, 11.5, 12.5, 73/6, 205/18.
    # EMA(3), alpha 1/2, from session 2: 11, 12, 12, 11.5.
    # Line from session 2 (slow - 1): 1/2, 1/2, 1/6, -1/9.
    # Signal, EMA(2) of the line seeded with (1/2 + 1/2)/2 at session 3 (slow + signal - 2): 1/2, 5/18, 1/54.
    # Histogram from session 3: 0, -1/9, -7/54.
    # Sixths, ninths and fifty-fourths are not exact in binary, so these compare within 1e-12.
    out = run("qrs.macd", {"bars": bars_with_closes([10, 11, 12, 13, 12, 11])}, fast=2, slow=3, signal=2)
    assert out["macd"].iloc[:2].isna().all() and out["signal"].iloc[:3].isna().all() and out["histogram"].iloc[:3].isna().all()
    assert out["macd"].iloc[2:].tolist() == pytest.approx([1 / 2, 1 / 2, 1 / 6, -1 / 9], rel=1e-12)
    assert out["signal"].iloc[3:].tolist() == pytest.approx([1 / 2, 5 / 18, 1 / 54], rel=1e-12)
    assert out["histogram"].iloc[3:].tolist() == pytest.approx([0, -1 / 9, -7 / 54], rel=1e-12, abs=1e-15)


def test_macd_requires_fast_shorter_than_slow():
    with pytest.raises(ValidationError, match="fast must be shorter than slow"):
        registry.get("qrs.macd", 1).params.model_validate({"fast": 26, "slow": 12})


def test_ema_values_reject_gaps_after_warm_up():
    with pytest.raises(ValueError, match="must not be missing after their warm-up"):
        ema_values(np.array([np.nan, 1.0, np.nan, 2.0]), 2)


def pandas_ema(values: pd.Series, n: int) -> pd.Series:
    """Reference EMA: seeded with the mean of the first n valid values, then pandas' ewm."""
    valid = values.dropna()
    seeded = pd.concat([pd.Series([valid.iloc[:n].mean()], index=[valid.index[n - 1]]), valid.iloc[n:]])
    return seeded.ewm(alpha=2 / (n + 1), adjust=False).mean().reindex(values.index)


def pandas_rsi(close: pd.Series, n: int) -> pd.Series:
    """Reference RSI: Wilder's averages are an ewm with alpha 1/n, seeded with the first n changes."""
    change = close.diff()

    def wilder(moves: pd.Series) -> pd.Series:
        seeded = pd.concat([pd.Series([moves.iloc[1 : n + 1].mean()], index=[moves.index[n]]), moves.iloc[n + 1 :]])
        return seeded.ewm(alpha=1 / n, adjust=False).mean()

    return (100 - 100 / (1 + wilder(change.clip(lower=0)) / wilder((-change).clip(lower=0)))).reindex(close.index)


def test_indicators_match_independent_pandas_references():
    bars = Bars(synthetic_bars(seed=11, sessions=400), INSTRUMENT, "sha256:" + "0" * 64)
    close = bars.frame["close"]

    def same(ours: pd.Series, reference: pd.Series):
        pd.testing.assert_series_equal(ours, reference, check_names=False, check_freq=False, rtol=1e-9, atol=1e-9)

    same(run("qrs.sma", {"bars": bars}, period=20)["series"], close.rolling(20).mean())
    same(run("qrs.returns", {"bars": bars}, period=5)["series"], close.pct_change(5))
    same(run("qrs.ema", {"bars": bars}, period=20)["series"], pandas_ema(close, 20))
    bands = run("qrs.bollinger", {"bars": bars}, period=20, k=2)
    deviation = close.rolling(20).std(ddof=0)
    same(bands["upper"], close.rolling(20).mean() + 2 * deviation)
    same(bands["lower"], close.rolling(20).mean() - 2 * deviation)
    same(run("qrs.rsi", {"bars": bars}, period=14)["series"], pandas_rsi(close, 14))
    lines = run("qrs.macd", {"bars": bars}, fast=12, slow=26, signal=9)
    line = pandas_ema(close, 12) - pandas_ema(close, 26)
    same(lines["macd"], line)
    same(lines["signal"], pandas_ema(line, 9))
    same(lines["histogram"], line - pandas_ema(line, 9))
