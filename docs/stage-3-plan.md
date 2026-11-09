# Stage 3 plan: technical MVP

**Status:** approved, then implemented from `main` at `d8e9451` in workstreams 1–7 (PRs #3–#9). The
exit gate is verified in [stage-3-verification.md](stage-3-verification.md).

**Basis:**
- The Addendum's Phase 3 scope: visual builder; EMA/SMA, Bollinger Bands, RSI and MACD; rule
  composition; position policies; file import; crossover and Bollinger/RSI templates; run
  comparison.
- Its P0: settle port and time semantics, indicator conventions and entry/exit policy before the
  palette grows.
- The Stage 1 MVP acceptance cases.

## Adopted decisions

1. **Bollinger Bands:** middle ± k × *population* standard deviation (ddof 0).
2. **RSI edge cases:** 100 when the Wilder average loss is 0, 50 when both averages are 0. The
   averages never reset, so this means no loss, or no movement, since the first change.
3. **No expression language:** rules are composed from typed nodes only; there is no `eval`.
4. **Long/short policy:** added in Stage 3 alongside long/flat.
5. **CSV price units and missing sessions:**
   - Prices are declared as currency units, or as cents (divided by 100). No other scale factors.
   - Every expected trading session missing from the data is reported. Expected sessions come
     from the venue's exchange calendar where one is available. Otherwise they are weekdays,
     and the report is explicitly labelled approximate.
   - Missing prices are never filled automatically.
6. **Tolerance:** trades must match exactly. Metrics must match exactly on one platform. Across
   platforms, two metric values match when `|a − b| ≤ max(1e-9 × max(|a|, |b|), 1e-6)`. The 1e-6
   absolute floor, in the metric's own units, keeps near-zero values such as a flat P&L comparable;
   it is far below a cent or a hundredth of a percentage point.
7. **Charts:** stay inline SVG, including comparison overlays. Plotly is reconsidered in Phase 4,
   when distributions and parameter heatmaps arrive.

## Extensibility guarantees

Stage 3 adds operators and editor features, never a second graph format, compiler, runtime or
backtester. Specifically:

- **Same graph format, any combination:**
  - every indicator outputs `series` (or several named `series`)
  - every rule consumes `series` or `signal` and outputs `signal`
  - every position policy consumes `signal`

  So any indicator combines with any rule, and rules nest without limit through `all`, `any` and
  `not`.
- **Editor, validator and compiler stay operator-agnostic.** New operators, Stage 3 or later,
  appear in the palette, get a form from their parameter model, validate, compile, run and export
  with no changes to those components. Stage 2 proved this, and its extension test stays in place.
- **Phase 4 operators register against the same contract** `run(context, inputs, params)`. This
  covers trusted Python modules, Fit/Predict and LLM extraction. Each needs two additive extensions,
  planned for Phase 4A and not built now:
  - **new port types**, such as tables, predictions, documents and model artifacts, registered with
    their runtime checks the way operators are
  - **new temporal alignments**: as-of joins and a fit scope
- **Old operator versions stay registered** whenever a convention changes, so saved strategies keep
  reproducing.

## Exit gate

1. **Reproduction:** for every template, draw → save → run → export → run outside the studio
   reproduces trades exactly and metrics within the adopted tolerance (decision 6). The code hash, runtime identity
   (including the dependency lock hash) and result hash all match.
2. **Arbitrary combinations:** a generated test covers every pairing of indicator output and rule
   operator (about 40 strategies), with each rule feeding each position policy. Every one
   validates, compiles, runs and passes the causality check.
3. **Indicators:** warm-up and crossings match hand-calculated fixtures and an independent pandas
   reference.
4. **Bad input fails before simulation:** wrong types, undeclared price units and future-data
   leakage.
5. **Units:** cents and dollar imports of the same prices give identical dataset contents and
   backtests.
6. **Explicit outcomes:** missing sessions (reported, never filled), zero trades, insufficient
   capital and invalid input each produce a clear result.
7. **Offline:** no LLM key or network access is needed.
8. **Durability (from Stage 2):** closing the browser loses no run, and a stale save fails visibly.

## Workstreams

Each workstream ships as its own pull request with the listed tests. CI must stay green on Ubuntu
and macOS 15.

### 1. Contracts and reproducibility (P0, first)

**Temporal policy.** Every operator declares one at registration. It flows into `OperatorInfo`,
the schema and the TypeScript types:

```python
temporal=Temporal(
    warm_up=lambda p: p.period - 1,  # sessions before the first valid output
    alignment="same_index",          # the only value in Stage 3; Phase 4A adds "as_of"
)
```

- **Alignment, checked once:** the runtime checks centrally that every series input and output of
  a `same_index` operator shares the bars' session index. This replaces the separate index checks
  now inside individual operators.
- **Causality, required of every operator:** a harness runs every registered operator, extensions
  included, on generated sample inputs for each port type. It checks that:
  - the first valid output falls exactly where the declared warm-up says
  - rewriting rows after t leaves every output up to t unchanged
- **Effective warm-up:** a node's own warm-up plus the largest of its inputs'. Run submission
  rejects a strategy whose effective warm-up uses up the dataset. Results show the first tradable
  session.
- **Palette grouping:** each operator gains a `family` text field. Unknown families are grouped
  under "Other", so extensions can add their own.

**Full dependency locking.** Stage 2 exports pin only five packages.

- Commit `src/qrs/runtime.lock`, produced by
  `uv export --frozen --no-dev --no-emit-project --format requirements-txt`. It holds every package
  at an exact version with SHA-256 hashes, for all platforms. This was checked against today's
  `uv.lock`.
- Exports ship it as `requirements.txt`, installed with `pip install --require-hashes`.
- The lock's hash joins the runtime identity in each run record.
- CI pins the uv version, and pins GitHub Actions by commit SHA to releases that run on Node 24.
  This also clears the current Node 20 deprecation warning.

**Acceptance:**
- The harness passes for every operator.
- A test fails if `runtime.lock` differs from a fresh export of `uv.lock`.
- A bundle installs with `--require-hashes` in a fresh virtualenv and matches the studio's result
  hash.
- The operator-level index checks are removed, and their tests now exercise the central check.

### 2. Indicators (each operator version 1)

| Operator | Outputs | Convention | Warm-up |
| --- | --- | --- | --- |
| `qrs.ema` (exists) | series | Seeded with the SMA of the first n values; α = 2/(n+1) | n−1 |
| `qrs.sma` | series | Mean of the last n | n−1 |
| `qrs.bollinger` | upper, middle, lower | Middle = SMA(n); bands at ± k × population standard deviation | n−1 |
| `qrs.rsi` | series | Wilder smoothing, seeded with the means of the first n changes; decision 2 | n |
| `qrs.macd` | macd, signal, histogram | EMA(fast) − EMA(slow); signal = EMA of the MACD line, seeded from its first `signal` valid values | slow−1 (macd); slow+signal−2 (signal, histogram) |
| `qrs.column` | series | One price column from bars, needed to compare the close with the bands | 0 |

- **One implementation per calculation:** MACD reuses the EMA function, and Bollinger reuses the
  SMA function.
- **The extension example changes** to a rate-of-change operator, so SMA exists only once.
- **Acceptance:**
  - every operator has a hand-calculated fixture with exact equality, a pandas reference check on
    the synthetic dataset, and passes the harness
  - RSI fixtures cover no losses and no movement

### 3. Rule composition and position policies

- **New rule operators:**
  - `qrs.compare`: series against series, with < ≤ > ≥
  - `qrs.threshold`: series against a constant; it shares the comparison function with `compare`
  - `qrs.all` and `qrs.any`: two inputs each; chain them for more
  - `qrs.not`
  - `qrs.lag`: past values only

  Missing values are false, and a signal stays false until all its inputs are past warm-up.
  `qrs.cross` already exists.
- **New position policy, `qrs.long_short`:**
  - inputs: long entry, short entry, exit
  - exit wins over any entry
  - conflicting entries go flat
  - a reversal is one order, which the backtest already handles and verifies
- **Acceptance:**
  - truth tables for each rule, including missing values and warm-up
  - a hand-calculated `long_short` case through the backtest covering entry, reversal, exit,
    conflicting entries and exit-wins, reconciled like the Stage 2 golden cases

### 4. CSV import

- **One import function** behind `qrs import` on the command line, an upload endpoint (10 MB
  limit) and a studio dialog.
- **User declares:**
  - the date and OHLC column mapping
  - the date format
  - the price unit (decision 5)
  - the venue's exchange calendar, or none
  - instrument details
  - evidence class, source, and whether the data may be redistributed
- **Result:** an ordinary immutable dataset. The manifest gains an optional `import_report`
  holding:
  - the source file's SHA-256
  - the column mapping
  - the raw price unit
  - the missing-session report: every expected session absent from the file, with the calendar
    used, or "weekdays (approximate)" when there is none

  It is absent on synthetic datasets, so existing dataset IDs do not change. There is no manifest
  version bump.
- **Calendar source:** a maintained calendar library chosen in this workstream. Before adoption,
  check its licence and its coverage of the futures venues needed. A venue it does not cover falls
  back to the labelled weekday check. Missing sessions are only reported: the backtest continues to
  execute at the next session present in the data, as in Stage 2.
- **Rejected, with row numbers:**
  - unparsable values
  - duplicate or unordered dates
  - missing OHLC values
  - inconsistent highs and lows
  - prices off the tick grid
  - settlement-only files, since OHLC is never fabricated
- **Acceptance:**
  - a test for each rejection
  - the cents and dollar files produce identical bars and identical backtest results
  - the missing-session report on a fixture with known holidays and a missing week, for both a
    calendar venue and the approximate weekday check
  - no price is ever filled: the imported bars equal the file's rows
  - the Stage 2 demo dataset keeps its ID
  - an upload over the limit is refused

### 5. Templates

- **Template file additions:** required dataset kind, research conditions, a fixture CSV and its
  expected fills. Parameter bounds come from the operators.
- **Templates:**
  - EMA crossover (exists)
  - SMA crossover
  - Bollinger/RSI: long when the close is below the lower band (20, 2) and RSI(14) < 30; exit when
    the close is at or above the middle band, or RSI > 50; exit wins
- **Acceptance:**
  - every template reproduces its expected fills on its fixture
  - every template passes app/CLI/export parity
  - Bollinger/RSI entry, exit and boundary behaviour are verified independently:
    - its fixture's signals equal an exact reference on every session, covering an exit by each
      condition alone, an entry while already long, and a close below the lower band without RSI
      below 30
    - its rule nodes are tested at each boundary: a close at the lower or middle band, and RSI at
      30 or 50
  - exit-wins is tested on a strategy where entry and exit can fire together: the Bollinger/RSI
    graph with its exit threshold lowered to RSI > 25, and its position node given both signals
    while long
  - *Amended in workstream 5.* The two bullets above replace "the Bollinger/RSI fixture includes a
    session where entry and exit both fire". That is impossible under these rules: an entry needs
    the close below the lower band, so below the middle band, and RSI below 30, which rules out both
    exit conditions.

### 6. Visual builder

- **Palette:** grouped by family.
- **Template gallery:** with a dataset picker.
- **Editing:**
  - undo/redo, coalescing rapid edits to one field
  - recovery of unsaved edits after a failed save (the AI Workflow Studio pattern deferred in
    Stage 2)
- **Node display:** labelled multi-output ports and each node's effective warm-up.
- **Import dialog** for CSV files.
- **Acceptance:**
  - Vitest for undo/redo and recovery
  - a Playwright suite in CI covering:
    - build the Bollinger/RSI strategy from an empty canvas, then save, run and export; the bundle
      result must match
    - a stale save is refused
    - a run started before the browser closes is finished when the studio reopens
    - a connection between mismatched types is refused

### 7. Run comparison

- **Selection:** two or more succeeded runs on the same dataset, compared over a common window that
  starts after the latest effective warm-up.
- **Shown:**
  - parameter differences by node
  - metrics recomputed over the common window, side by side
  - equity curves overlaid in SVG
  - sessions excluded from each run
- **Mismatches flagged, never compared silently:** differing costs or execution assumptions.
- **Re-run action:** the new run records its predecessor (a Stage 2 gap).
- **Acceptance:**
  - common-window metrics equal those computed directly from the sliced ledgers
  - a cost mismatch is flagged
  - comparing runs on different datasets is refused
  - a re-run links to its predecessor

## Order

1. **First:** contracts and locking (1).
2. **Then, in parallel:** indicators (2), rules (3) and CSV import (4).
3. **After 2 and 3:** templates (5).
4. **Builder (6):** begins after 1. Its gallery waits for 5 and its import dialog for 4.
5. **Last:** comparison (7). The combination test in the exit gate runs once 2 and 3 are merged.

## Not in Stage 3

- **Deferred within the product:**
  - paged result endpoints and downsampled chart series, which daily data does not need; revisit
    with intraday data
  - palette search
  - scale factors other than cents
- **Later phases:**
  - trusted Python modules, as-of joins, ML, LLM and optimisation sweeps (Phases 4A/4B)
  - corn integration
  - desktop packaging and the per-launch API token
  - live trading
  - multiple assets, intraday data, contract rolls and historical margin
