# NautilusTrader evaluation

**Decision:** adopt NautilusTrader **1.230.0** as the single simulation backend, behind
`src/qrs/simulation.py` and with the adapter controls below. Adoption followed a spike on
synthetic daily bars, and the results are now enforced by the golden tests in
`tests/test_simulation.py`.

## Compatibility

| Check | Result |
| --- | --- |
| Install (Linux x86_64, Python 3.12) | Wheel installs with uv; imports and runs. One backtest of 500 sessions takes about 0.2 s. |
| Published wheels | 1.230.0: CPython 3.12–3.14 for Linux x86_64/aarch64, Windows x86_64, and **macOS 15+ on Apple silicon**. 1.231.0 requires macOS 26, so 1.230.0 is pinned. Intel Macs have no wheels. |
| Python dependencies | pandas 3.1, numpy 2.5 and pyarrow 26 work together. |
| Several backtests in one process | **Problem:** the second engine aborts the process, because Nautilus's logger can be initialised only once. **Control:** the adapter sets `bypass_logging=True` and reports failures itself. |
| Determinism | Repeated runs produce identical fills, equity and hashes. No random fill model is used. |
| Licence | LGPL-3.0-or-later; used unmodified as a library (see decision 5). |

## Futures accounting

Spike and golden cases: multiplier 50, tick 0.25, commission $2.50 per contract and 1 tick of
slippage. Nautilus's figures matched every hand calculation once two defaults were corrected:

| Behaviour | Default finding | Control |
| --- | --- | --- |
| P&L uses the multiplier | Correct: (exit − entry) × quantity × 50 | Golden tests |
| Per-contract commission | Correct with `PerContractFeeModel`. Commission is included in realised P&L and cash. | Golden tests |
| Long-to-short reversal in one order | Correct: realises the long and opens the short at the fill price | Golden test |
| Margin model | **Wrong for exchange futures:** a margin account defaults to leverage 10, and the default model divides margin by it. Maintenance margin came out at 52.13 instead of 521.25. | Use `StandardMarginModel` (fixed fraction of notional) |
| Insufficient capital | **Not enforced:** a market order needing $1,042.50 initial margin filled on a $500 account | The adapter checks initial margin before each order that opens, enlarges or reverses a position, and maintenance margin at each close; a breach fails the run. Orders that only reduce a position are always accepted, even after a gap leaves equity below initial margin. |
| Equity | Cash balance plus unrealised P&L at the close | Reconciled every session against independent cash-flow accounting on the full EMA demo |

## Execution semantics

| Question | Finding | Control |
| --- | --- | --- |
| When does an order sent from a daily bar fill? | **At that bar's close.** Close-derived signals would trade on the same close, which the audit forbids. | The adapter feeds no bars. Each session becomes two quotes: the open (open ∓ slippage) and the close. Orders are sent on the open quote, after the previous close's decision. |
| Slippage | `FillModel` slips one tick at random | Deterministic slippage of N ticks via the open quote's bid and ask |
| Final session's decision | There is no next open | Not executed; stated in the run's assumptions |
| Fills match the plan | — | The position at every close is checked against the target; a mismatch fails the run |

## Not covered by Stage 2

The following are outside Stage 2 scope and would need new golden cases before use:
- contract expiry and rolls
- historical margin schedules
- partial fills and volume limits
- multiple instruments
- intraday data
