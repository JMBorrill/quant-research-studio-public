# Stage 2 verification

Recorded 31 October 2026 on Linux x86_64 with Python 3.12.3, NautilusTrader 1.230.0, pandas 3.1.0
and Node 22. Every result below was observed by running the command shown, not inferred.

## Completion gate

| Gate | Status | Evidence |
| --- | --- | --- |
| Strategy runs reproducibly | Met | The same strategy run twice, and through three routes, gives identical `record.json` files (code, runtime, dataset and output hashes) |
| Accounting reconciles | Met | Hand-calculated golden cases match exactly. On every session of the EMA demo, equity matches an independent cash-flow recomputation. |
| Python export matches application execution | Met | App worker, CLI and exported bundle produce the same `result_sha256`, including the bundle in a fresh virtualenv |
| Extensibility demonstrated | Met | The `ext.sma` operator registers from outside the package. It validates, compiles, runs, exports and appears in the editor with no editor or compiler change. |

## Automated tests

`uv run pytest`: **65 passed**. `npm test` (Vitest): **10 passed**. `npm run typecheck` and
`npm run build`: clean. `ruff check` and `ruff format --check`: clean.

| File | Tests | What it proves |
| --- | --- | --- |
| `tests/test_operators.py` | 6 | EMA seeding, warm-up and α; cross-above/below definitions including equality and NaN; long/flat precedence. All checked against hand values. |
| `tests/test_simulation.py` | 9 | Hand-calculated Nautilus golden cases: next-open fills with slippage and commission; equity, cash and unrealised P&L each session; a one-order reversal; zero trades; final-session target; initial margin for new and reversed positions; reductions accepted after a gap down; maintenance breach; misaligned targets |
| `tests/test_strategy_run.py` | 7 | EMA template end to end: rerun gives an identical record; every fill is at the next open ± 1 tick; equity reconciles with independent cash-flow accounting; rewriting future prices leaves earlier fills and equity unchanged; explicit errors name the failing node; the CLI refuses a used output directory |
| `tests/test_graph.py` | 16 | Fan-out and multi-input; type mismatches, duplicate and missing inputs, unknown operators and versions, invalid parameters, cycles, a missing backtest, unsafe node IDs, unknown templates and unsupported schema versions are reported; compilation is deterministic and ignores layout, names and ordering; the committed schema matches the contracts |
| `tests/test_datasets.py` | 16 | Content IDs; read-only publication; deterministic synthetic data; 10 kinds of invalid bars rejected with reasons; changing the data or manifest in place fails the checksum; the listing skips datasets that fail verification |
| `tests/test_service.py` | 11 | Loopback-only hosts; stale saves rejected (409); invalid strategies and missing data refused before queuing; export problems answered with 422; failed runs keep their error and log; runs survive a service restart, and orphaned runs become `interrupted` with their logs; cancelling queued and running workers; a crashing worker fails only its own run; a worker exits when the service goes away; app/CLI/export parity; the extension operator |
| `apps/studio/src/graph.test.ts` | 6 | Editor edits: fan-out kept, an input's connection replaced, type-checked connections, node and edge removal, parameters |
| `apps/studio/src/autosave.test.ts` | 4 | Run/export wait for an edit taken up by a concurrent save; switching strategies saves the pending edit under its own ID and ignores late responses; a conflict stops saving; a failed save retries after a delay, not in a loop |

## Hand-calculated golden case

Multiplier 50, tick 0.25, commission $2.50 per contract, 1 tick of slippage, $100,000 starting
cash. The target of 2 contracts is decided at the 7 January close; the exit is decided at the
9 January close.

| Session | Open | Close | Event | Cash | Unrealised | Equity |
| --- | --- | --- | --- | --- | --- | --- |
| 5–7 Jan | — | — | flat | 100,000.00 | 0 | 100,000.00 |
| 8 Jan | 104.00 | 103.00 | BUY 2 @ 104.25, commission 5 | 99,995.00 | −125.00 | 99,870.00 |
| 9 Jan | 103.00 | 105.00 | hold | 99,995.00 | +75.00 | 100,070.00 |
| 12 Jan | 105.00 | 105.50 | SELL 2 @ 104.75, commission 5 | 100,040.00 | 0 | 100,040.00 |

Net +40 = gross +100 (open-to-open) − commission 10 − slippage 50. NautilusTrader reproduces every
figure.

## EMA crossover demo (synthetic data)

`qrs demo` then `qrs run`, on 500 synthetic sessions (seed 42; instrument SYN with multiplier 50 and
tick 0.25). EMA 20/50, 1 contract, commission $2.50, 1 tick of slippage:

| Metric | Value |
| --- | --- |
| Fills / round trips | 13 / 6 (one position still open at the end) |
| Net P&L / gross P&L | −1,620.00 / −1,425.00 |
| Commission / slippage | 32.50 / 162.50 |
| Max drawdown | 1,762.50 (1.76%) |
| Exposure | 33.6% of sessions |

These numbers verify the mechanics only. The data is synthetic and says nothing about
profitability.

## Reproducibility and parity

| Check | Result |
| --- | --- |
| `qrs run` twice | `code_sha256 d8fad9fa…`, `result_sha256 24357159…` both times |
| `qrs export` twice | Identical ZIP bytes (`sha256 97266d3e…`) |
| `qrs run` into a used output directory | Refused before anything is written ("is not empty; choose a new output directory") |
| App, CLI and export (test) | Same code hash, runtime identity, dataset hashes, output hashes and `result_sha256` |
| Bundle exported from the browser, run in a **fresh virtualenv** containing only `requirements.txt` (no qrs install, no FastAPI) | `result_sha256 7ce67848…`, the same as the studio run of that revision |

## Browser checks (Chromium via Playwright, against the built studio)

- The EMA template loads with 7 nodes and 10 edges.
- A parameter edited in the generated form autosaves as revision 2.
- **Run:** succeeds and shows metrics, an equity chart and 13 fills.
- **Export Python:** downloads `strategy-…-r2.zip`.
- **Connection type check:** dragging a `bars` output onto a `series` input does not connect (still 10 edges).
- **Errors:** none in the browser console.
- **Two tabs:** after tab A saves, tab B's save is refused with "The strategy was saved elsewhere (now at revision 3)". Reload loads tab A's version.
- **Extension:** with `QRS_OPERATOR_MODULES=qrs_sma`, SMA appears in the palette and its form (Period, Column) is generated from its Pydantic model.
- **Edit then Run at once:** three quick edits and an immediate Run produce a run of the latest revision, which contains the last edit.

Screenshots: [editor](images/editor.png), [results](images/results.png),
[extension](images/extension.png).

## Fresh environments

- **Fresh clone** of `stage-2-foundation`: `uv sync --locked` and 55 passed; `npm ci`, generated
  types unchanged, typecheck, 6 Vitest tests and build all passed.
- **GitHub Actions** [run 1](https://github.com/JMBorrill/quant-research-studio/actions/runs/41203883127)
  on commit `4a9a66f`. Every job passed:

  | Job | Result |
  | --- | --- |
  | `python (ubuntu-latest)` | 55 passed, plus ruff |
  | `python (macos-15)` (Apple silicon, Python 3.12.10) | 55 passed, plus ruff, including the golden accounting, cancellation and parity tests |
  | `studio` | gen:types drift check, typecheck, Vitest, build |

## Pre-merge review

An independent review of the PR against both Stage 1 reports, plus a targeted check of the
NautilusTrader adapter, found the following. All are fixed with regression tests.

| Severity | Finding | Fix |
| --- | --- | --- |
| Material | Run or Export straight after an edit could use the revision before it, while a second autosave was still in flight | Autosave moved into `autosave.ts`; `flush()` waits until nothing is pending or in flight |
| Material | Switching strategy within the autosave pause dropped the edit; a late save response could corrupt the next strategy's base revision | Pending edits are saved under their own strategy before switching; late responses are ignored |
| Material | After a failed save the editor retried immediately in a loop | Retries wait for the retry delay |
| Minor | Margin: an order that only reduced a position after a gap down failed the run | Initial margin is checked only for opening, enlarging or reversing orders |
| Minor | A service killed outright left the worker running and its log unpublished | Workers exit when the service's pipe closes; interrupted runs are published at startup |
| Minor | One unreadable dataset made the dataset list, and so the studio, fail | The listing skips entries that fail verification |
| Minor | A strategy without a backtest passed validation | Validation requires a backtest output |
| Minor | `qrs run` into a used directory overwrote `strategy.py` before refusing | The CLI refuses before writing |
| Minor | Template IDs could name files outside the template directory | Only known templates are accepted |
| Minor | Export failures (missing data, unexportable module) returned 500 | Returned as 422 with the reason |

The adapter was also checked on 25 randomised target schedules (478 fills, 197 one-order
reversals). Fill session, side, quantity, price, commission, position and equity all matched the
next-open rule and independent cash-flow accounting. Exceptions raised inside Nautilus callbacks
reach the caller, and a worker killed by a signal fails only its own run.

## Not verified, or blocked

- **Your own Mac:** not tested. GitHub's macOS 15 runner passed. NautilusTrader 1.230.0 has no
  wheels for Intel Macs or for macOS versions before 15.
- **Outside Stage 2 by design:** desktop packaging, real market data, contract expiry and rolls,
  historical margin, and untrusted-code isolation.
