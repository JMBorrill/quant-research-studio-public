# Stage 3 verification

Recorded 31 October 2026 on Linux x86_64 with Python 3.12.3, NautilusTrader 1.230.0, pandas 3.1.0,
Node 22 and Chromium 153 (Playwright 1.63.0). It covers the exit gate of the
[Stage 3 plan](stage-3-plan.md), which workstreams 1–7 implemented (PRs #3–#9). Every result
below was observed by running the command shown, not inferred.

Most of the evidence comes from tests that already passed CI on Ubuntu and macOS 15 at `main`
`4cba498`
([run 41231698705](https://github.com/JMBorrill/quant-research-studio/actions/runs/41231698705)).
Workstream 8 added the checks marked *(new)*. They passed locally and in CI at `97b04ed`: the
browser, export and offline checks on Ubuntu, and the metric comparison on Ubuntu and macOS 15. The
CI runs were push run
[41237218692](https://github.com/JMBorrill/quant-research-studio/actions/runs/41237218692) and
pull-request run [41237223369](https://github.com/JMBorrill/quant-research-studio/actions/runs/41237223369).

## Exit gate

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | **Reproduction:** every template, drawn, saved, run, exported and run outside the studio, reproduces its trades exactly and its metrics within tolerance; code, runtime and result hashes match | Met on Linux; metrics also within tolerance on macOS 15 | See [Reproduction](#reproduction) |
| 2 | **Arbitrary combinations:** every indicator output with every rule, feeding each position policy, validates, compiles, runs and passes causality | Met | `tests/test_combinations.py`: 9 indicator outputs × 4 series rules = 36 strategies. Each nests `not` and `all` (the long/flat ones `any` too) and feeds long/flat or long/short, so every rule meets both policies. Each validates, compiles and runs, trades only after its warm-up, and is unchanged up to a rewrite of later prices |
| 3 | **Indicators:** warm-up and crossings match hand-calculated fixtures and a pandas reference | Met | `tests/test_operators.py` has hand-calculated fixtures checked exactly (including RSI with no losses and no movement), crossings with equality and missing values, and `test_indicators_match_independent_pandas_references`. `tests/test_operator_contracts.py` checks every operator's warm-up and causality. Every template's signals equal an exact rational reference on every session (`test_template_signals_match_the_reference_on_every_session`) |
| 4 | **Bad input fails before simulation:** wrong types, undeclared price units, future-data leakage | Met | See [Bad input](#bad-input) |
| 5 | **Units:** cents and currency imports give identical datasets and backtests | Met | `test_cents_and_currency_files_give_identical_bars_and_backtests`, `test_fractional_cents_give_the_same_bars_as_currency` |
| 6 | **Explicit outcomes:** missing sessions, zero trades, insufficient capital and invalid input each give a clear result | Met | Missing sessions are reported and never filled, for an exchange calendar and for the approximate weekday check (`test_missing_sessions_are_reported_and_never_filled`). Zero trades is a flat result with zero metrics (`test_no_trades_is_an_explicit_flat_result`). Insufficient initial or maintenance margin fails the run, naming the shortfall (`test_insufficient_initial_margin_fails_the_run`, `test_maintenance_margin_breach_fails_the_run`), and the failed run keeps its error (`test_failed_runs_keep_their_error_and_log`). Invalid files are refused with row numbers (`test_each_rejection_names_its_row_and_publishes_nothing`). Invalid strategies are refused with node names before queuing (`test_invalid_strategies_and_missing_data_are_refused_before_queuing`) |
| 7 | **Offline:** no LLM key or network access needed | Met, locally and in CI | See [Offline](#offline) |
| 8 | **Durability:** closing the browser loses no run, and a stale save fails visibly | Met | Browser tests: `test_a_run_started_before_the_browser_closes_is_finished_when_the_studio_reopens`, `test_a_stale_save_is_refused_and_its_edits_can_be_recovered`. Service tests: `test_runs_persist_across_restart_and_orphans_become_interrupted`, `test_stale_saves_are_rejected` |

### Reproduction

- **Drawn in the studio** *(new)*: `test_draw_each_template_from_an_empty_canvas_then_save_run_and_export` draws each template from the palette in Chromium. The test then checks:
  - its saved operators, parameters and edge count equal the template's
  - its backtest shows the warm-up worked by hand (19 for Bollinger/RSI, 50 for the crossovers)
  - its run gives the template's expected fills
  - the exported bundle, run outside the studio, gives the same code hash, result hash and outputs

  Before Workstream 8, only Bollinger/RSI was drawn.
- **App, CLI and export parity:** `test_app_cli_and_export_produce_identical_results` runs every template on its fixture, and EMA on synthetic and uploaded data. The three routes give identical code hashes, runtime identity (including the lock hash), dataset hashes, outputs and result hashes, and the template's expected fills.
- **Fresh virtualenv** *(new)*: CI's export job used to run only the EMA bundle on synthetic data. It now runs every template's bundle, exported for its fixture, and checks each matches the CLI result hash. All the bundles run in one fresh virtualenv, without qrs, installed from the bundles' hashed lock (every bundle has the same `runtime.lock`). The EMA bundle on synthetic data is still checked by the parity test. In CI the job passed, and the same script run locally gave the same hashes:

  | Template | Fills | Net P&L | `code_sha256` | `result_sha256` (CLI = bundle) |
  | --- | --- | --- | --- | --- |
  | Bollinger/RSI | 4 | 440.00 | `fff3fd9a…` | `9debb8e1…` |
  | EMA crossover | 2 | −167.50 | `2e0a0d84…` | `277a3eab…` |
  | SMA crossover | 3 (one position open at the end) | −207.50 | `c280fe54…` | `336b2b53…` |

- **Metrics across platforms** *(new)*: `test_every_template_reproduces_its_expected_fills_and_metrics_on_its_fixture` compares every metric with values recorded on Linux x86_64, using decision 6's tolerance, `|a − b| ≤ max(1e-9 × max(|a|, |b|), 1e-6)`. The tolerance check has its own test. CI's macOS 15 job (Apple silicon, Python 3.12.10) passed all 303 tests, these included, so the metrics agree across the two platforms within that tolerance.

### Bad input

- **Wrong types:** a mismatched connection is refused by validation (`test_type_mismatch_duplicate_and_missing_inputs_are_reported`) and by the editor (`test_a_connection_between_mismatched_types_is_refused`). Strategies that fail validation are refused by the service before queuing (`test_invalid_strategies_and_missing_data_are_refused_before_queuing`).
- **Undeclared price units:** the import declaration requires a unit, and an upload without one is refused with 422 (`test_upload_problems_are_client_errors`).
- **Future data:**
  - Every registered operator, the example extension included, passes the causality harness (`test_outputs_never_depend_on_later_sessions`).
  - A lag of zero or fewer sessions, the one parameter that could look ahead, is refused by validation *(new test)*: `test_a_lag_that_would_read_ahead_is_refused_before_anything_runs`.
  - An operator output off the bars' session index fails the run at that node, before the backtest (`test_the_runtime_rejects_misaligned_inputs_and_outputs`).
  - Rewriting later prices leaves earlier fills unchanged (`test_future_data_cannot_change_earlier_decisions`).

### Offline

- **No network or LLM code:** nothing in `src/qrs` imports an HTTP client or reads an API key. The studio loads no external resources.
- **As root:** run inside `unshare --net` with only loopback up; a connection to an outside address fails with "Network is unreachable". Everything passed:
  - `pytest`: 303 passed
  - `pytest -m e2e`: 9 passed
  - `qrs demo`, then `qrs run` and `qrs export` of the EMA strategy, with the bundle run from its own qrs: all reproduced code `d8fad9fa…` and result `24357159…`
- **As a non-root user, as CI runs it:** in a fresh clone owned by an unprivileged user, set up online with `uv sync --locked`, `npm ci` and `npm run build`. Root then ran `unshare --net`, brought loopback up, and dropped to the user with `sudo -H -u`, as CI's e2e step does:
  - `pytest -m e2e`: 9 passed, using the clone's own qrs, with `HOME` set to the user's home
  - `pytest`, without `test_lock`: 302 passed. `test_lock` needs the `uv` binary, which is outside that user's reach here; it passed offline as root

  Differences from CI:
  - The runner user calls `sudo unshare` itself; here root did.
  - CI brings loopback up with `ip link set lo up`; this container has no `ip`, so a short `ioctl` script did it.
  - The browsers came from the container's preinstalled Playwright browsers instead of `playwright install`.
- **CI** *(new)*: the e2e job runs the browser tests this way on every push, as the runner user. They exercise the service, workers, imports, runs and exported bundles. All 9 passed in both runs above.

## Checks

| Check | Result |
| --- | --- |
| `uv run pytest` | 303 passed, 9 deselected (the browser tests) |
| `uv run pytest -m e2e` | 9 passed |
| `npm test` / `npm run typecheck` / `npm run build` | 21 passed / clean / clean |
| `ruff check`, `ruff format --check` | Clean |
| Schema and generated types, `uv lock --check`, `runtime.lock` against a fresh export | No drift |
| Stage 2 demo | Dataset `sha256:91a9b520…`, code `d8fad9fa…` and result `24357159…` unchanged |

## Open items

- **CI before the reruns.** On their first attempts, runs 52–60 (pushes and pull-request runs) ran no job. No runner was assigned, and GitHub's annotation said: "The job was not started because recent account payments have failed or your spending limit needs to be increased." After the account's Actions minutes were increased, runs 59 and 60 were rerun and every job passed.
- **An unexplained browser-test failure.** `test_undo_and_redo_from_the_keyboard_save_each_step` failed once in CI, on a commit that changed only `CLAUDE.md` ([job 124628923412](https://github.com/JMBorrill/quant-research-studio/actions/runs/41232328345/job/124628923412)). Its first connection after opening a strategy made no edge. It was not reproduced locally:
  - 15 plain runs
  - runs with Chromium's CPU throttled 8× and 20×
  - an experiment holding a drag while the strategy's check returned
  - an experiment delaying the operator list

  The cause is unknown and no fix is applied. It did not recur in the two CI runs above. `test_a_connection_between_mismatched_types_is_refused` also connects straight after opening a strategy.
- **Not verified:**
  - your own Mac
  - Intel Macs and macOS before 15, which NautilusTrader 1.230.0 does not support
  - Windows
- **Scope of the drawing tests:** they cover the three templates. Arbitrary graphs are covered through the API by the combination test, not drawn.
- **What the data shows:** fixture and synthetic data verify the mechanics only, not profitability.
