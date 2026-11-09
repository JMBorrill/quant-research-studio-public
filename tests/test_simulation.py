"""NautilusTrader adapter reconciled against hand-calculated futures cases.

Shared assumptions: multiplier 50, tick 0.25, commission 2.50 per contract, slippage 1 tick (0.25),
initial cash 100,000 USD, initial margin 10% and maintenance margin 5% of notional.

  session      open    close
  0 Mon 01-05  100.00  100.50
  1 Tue 01-06  101.00  102.00
  2 Wed 01-07  102.00  104.00
  3 Thu 01-08  104.00  103.00
  4 Fri 01-09  103.00  105.00
  5 Mon 01-12  105.00  105.50
"""

import pandas as pd
import pytest

from conftest import CLOSES, INSTRUMENT, OPENS, SESSIONS, ohlc
from qrs.analytics import metrics
from qrs.ports import Bars
from qrs.simulation import SimulationError, simulate

COSTS = {"initial_cash": 100_000.0, "commission_per_contract": 2.5, "slippage_ticks": 1}


def targets(*values):
    return pd.Series(values, index=SESSIONS, dtype="int64")


def test_long_round_trip_fills_at_next_open_with_costs(golden_bars):
    # Target 2 decided at session 2's close -> BUY 2 at session 3's open: 104.00 + 0.25 = 104.25.
    # Target 0 decided at session 4's close -> SELL 2 at session 5's open: 105.00 - 0.25 = 104.75.
    fills, equity = simulate(golden_bars, targets(0, 0, 2, 2, 0, 0), **COSTS)

    assert fills.to_dict("records") == [
        {"session": SESSIONS[3], "side": "BUY", "quantity": 2, "price": 104.25, "commission": 5.0, "slippage": 25.0},
        {"session": SESSIONS[5], "side": "SELL", "quantity": 2, "price": 104.75, "commission": 5.0, "slippage": 25.0},
    ]
    # Session 3: cash 100,000 - 5 = 99,995; unrealised (103.00 - 104.25) * 2 * 50 = -125.
    # Session 4: unrealised (105.00 - 104.25) * 100 = +75.
    # Session 5: realised (104.75 - 104.25) * 100 = +50, commission 5 -> cash 100,040.
    assert equity["position"].tolist() == [0, 0, 0, 2, 2, 0]
    assert equity["cash"].tolist() == [100_000, 100_000, 100_000, 99_995, 99_995, 100_040]
    assert equity["unrealized_pnl"].tolist() == [0, 0, 0, -125, 75, 0]
    assert equity["equity"].tolist() == [100_000, 100_000, 100_000, 99_870, 100_070, 100_040]

    m = metrics(fills, equity, COSTS["initial_cash"])
    # Gross: bought at the 104.00 open, sold at the 105.00 open: 1.00 * 2 * 50 = 100.
    assert (m["net_pnl"], m["gross_pnl"], m["commission"], m["slippage"]) == (40, 100, 10, 50)
    # Peak 100,000 -> trough 99,870 at session 3.
    assert (m["max_drawdown"], m["max_drawdown_sessions"]) == (130, 1)
    assert m["max_drawdown_pct"] == pytest.approx(0.13)
    assert (m["fills"], m["round_trips"], m["contracts_traded"], m["final_position"]) == (2, 1, 4, 0)
    assert m["exposure_pct"] == pytest.approx(100 * 2 / 6)


def test_reversal_in_one_order_realises_the_long_and_opens_the_short(golden_bars):
    # Session 2 open: BUY 1 at 102.25. Session 3 open: SELL 2 at 103.75 closes the long
    # (+1.50 * 50 = +75) and opens a short at 103.75. Session 5 open: BUY 1 at 105.25 closes the
    # short (-1.50 * 50 = -75). Commission 2.5 + 5 + 2.5 = 10.
    fills, equity = simulate(golden_bars, targets(0, 1, -1, -1, 0, 0), **COSTS)

    assert fills[["side", "quantity", "price", "commission"]].values.tolist() == [["BUY", 1, 102.25, 2.5], ["SELL", 2, 103.75, 5.0], ["BUY", 1, 105.25, 2.5]]
    assert equity["position"].tolist() == [0, 0, 1, -1, -1, 0]
    # Session 2: 99,997.5 + (104.00 - 102.25) * 50 = 100,085
    # Session 3: cash 99,997.5 + 75 - 5 = 100,067.5; short marked (103.75 - 103.00) * 50 = +37.5
    # Session 4: (103.75 - 105.00) * 50 = -62.5. Session 5: 100,067.5 - 75 - 2.5 = 99,990.
    assert equity["equity"].tolist() == [100_000, 100_000, 100_085, 100_105, 100_005, 99_990]
    m = metrics(fills, equity, COSTS["initial_cash"])
    # Gross at the opens: long 102 -> 104 = +100, short 104 -> 105 = -50.
    assert (m["net_pnl"], m["gross_pnl"], m["slippage"], m["round_trips"]) == (-10, 50, 50, 1)


def test_a_whole_runs_drawdown_is_measured_from_its_first_close(golden_bars):
    # The simulator reads 485,000 back a hair below it. The equity never moves, so there is no
    # drawdown, and the run's summary (and result hash) is what it always was.
    fills, equity = simulate(golden_bars, targets(0, 0, 0, 0, 0, 0), **(COSTS | {"initial_cash": 485_000.0}))
    assert equity["equity"].iloc[0] < 485_000
    m = metrics(fills, equity, 485_000.0)
    assert (m["max_drawdown"], m["max_drawdown_sessions"]) == (0.0, 0)


def test_no_trades_is_an_explicit_flat_result(golden_bars):
    fills, equity = simulate(golden_bars, targets(0, 0, 0, 0, 0, 0), **COSTS)
    assert fills.empty and list(fills.columns) == ["session", "side", "quantity", "price", "commission", "slippage"]
    m = metrics(fills, equity, COSTS["initial_cash"])
    assert m["net_pnl"] == 0 and m["max_drawdown"] == 0 and m["max_drawdown_pct"] == 0
    assert (m["fills"], m["round_trips"], m["exposure_pct"]) == (0, 0, 0)


def test_target_from_the_last_close_is_not_executed(golden_bars):
    fills, equity = simulate(golden_bars, targets(0, 0, 0, 0, 0, 3), **COSTS)
    assert fills.empty and equity["position"].iloc[-1] == 0


def test_insufficient_initial_margin_fails_the_run(golden_bars):
    # Holding 2 at 104.25 needs 2 * 104.25 * 50 * 10% = 1,042.50 of initial margin.
    with pytest.raises(SimulationError, match=r"Insufficient capital on 2026-01-08: .*1,042.50 USD but equity is 1,000.00"):
        simulate(golden_bars, targets(0, 0, 2, 2, 0, 0), **{**COSTS, "initial_cash": 1_000.0})


def test_reversal_needs_initial_margin_for_the_new_position(golden_bars):
    # Long 1 at 102.25 (needs 511.25). Reversing to short 2 at the 103.75 bid needs
    # 2 * 103.75 * 50 * 10% = 1,037.50; equity is 950 - 2.50 + (103.75 - 102.25) * 50 = 1,022.50.
    with pytest.raises(
        SimulationError, match=r"Insufficient capital on 2026-01-08: holding -2 contract\(s\) needs initial margin 1,037.50 USD but equity is 1,022.50"
    ):
        simulate(golden_bars, targets(0, 1, -2, -2, 0, 0), **{**COSTS, "initial_cash": 950.0})


def test_reducing_a_position_needs_no_initial_margin():
    # Long 2 at 100.25 with 1,100 cash. The next open gaps down to 90, leaving equity
    # 1,095 + (89.75 - 100.25) * 100 = 45, far below initial margin for even one contract (448.75).
    # Reducing to 1 must still be accepted: SELL 1 at 89.75 realises (89.75 - 100.25) * 50 = -525,
    # commission 2.50 -> cash 567.50; the close of 99 marks the remaining contract at -62.50.
    index = pd.bdate_range("2026-01-05", periods=5, name="session")
    bars = Bars(ohlc([100, 100, 100, 90, 99], [100, 100, 100, 99, 99], index), INSTRUMENT, "sha256:" + "0" * 64)
    fills, equity = simulate(bars, pd.Series([0, 2, 1, 1, 1], index=index, dtype="int64"), **{**COSTS, "initial_cash": 1_100.0})
    assert fills[["side", "quantity", "price"]].values.tolist() == [["BUY", 2, 100.25], ["SELL", 1, 89.75]]
    assert equity["equity"].tolist() == [1_100, 1_100, 1_070, 505, 505]


def test_maintenance_margin_breach_fails_the_run(golden_bars):
    # Buy 2 at 104.25 with 1,100 cash (needs 1,042.50); a close of 95 leaves equity
    # 1,095 - (104.25 - 95) * 100 = 170, below maintenance 2 * 95 * 50 * 5% = 475.
    closes = CLOSES.copy()
    closes[3] = 95.0
    bars = Bars(ohlc(OPENS, closes), golden_bars.instrument, golden_bars.dataset_id)
    with pytest.raises(SimulationError, match=r"Maintenance margin breached on 2026-01-08: equity 170.00 is below 475.00"):
        simulate(bars, targets(0, 0, 2, 2, 0, 0), **{**COSTS, "initial_cash": 1_100.0})


def test_targets_must_share_the_bars_index(golden_bars):
    with pytest.raises(SimulationError, match="session index"):
        simulate(golden_bars, targets(0, 0, 0, 0, 0, 0).iloc[1:], **COSTS)
