# Architectural decisions (Stage 2)

These records implement the Stage 1 Architectural Audit and its Addendum. Each states the decision,
why, and what it rules out. "Audit" refers to those two reports.

## 1. Independent product

QRS is its own repository, package, UI, database and release. It has no runtime, package, database
or Git dependency on AI Workflow Studio (AIWS) or the corn research project. Selected AIWS editor
code was adapted by copy, recorded in [reuse.md](reuse.md). Neither donor repository was modified.

## 2. One contract, defined in Python

`src/qrs/contracts.py` (Pydantic) is the only hand-written definition of the strategy document,
operator description, dataset manifest and run record. `qrs schema` writes
`schemas/qrs.schema.json`, and `npm run gen:types` generates the studio's TypeScript types from it.
A test fails if the committed schema is stale.

The strategy document (`kind: "qrs/strategy"`, `version: 1`) holds nodes (ID, operator ID and
version, parameters) and edges between named ports. Canvas layout, name and template provenance
are stored in the document but never reach generated code, so they cannot change a result.
Unsupported document versions are rejected.

## 3. Typed graph semantics

- Each port has a type: `bars`, `series`, `signal`, `positions` or `backtest`. Edges connect only
  equal types; values are checked again at runtime, on the way into and out of every operator.
- Stage 4 workstream 1 added three types for model training (`qrs.ports`):
  - `table`: named float feature columns
  - `label`: a training target with its horizon, the sessions until each value is known
  - `model`: walk-forward fitted models, each with its cutoff, the exact sessions it trained on,
    horizon, features, statistics and coefficients. Its runtime check refuses a model that records
    a training row whose target was not known by its cutoff (the row plus a horizon of at least
    one session). The causality harness checks that what a fit computes agrees with what it
    records.

  A label looks ahead by definition, so registration refuses an operator that takes a label unless
  all its outputs are models. A target can therefore only train a model, never reach a rule, a
  position or a backtest.
- Missing values mark warm-up for both series (NaN) and signals (nullable booleans, NA). A rule's
  signal is missing wherever any of its inputs is, so warm-up flows through rules nested to any
  depth. Position policies treat a missing signal as no signal. Signals became nullable in Stage 3
  workstream 3; trading results are unchanged (the EMA demo keeps its result hash).
- An output may feed any number of inputs (fan-out), and an operator may take several inputs. Each
  input takes exactly one connection.
- The graph must be acyclic. Nodes execute sequentially in dependency order, with ties broken by
  node ID. Market time advances inside the Backtest operator, never through graph cycles.
- Validation reports every problem it finds at once, each tied to a node where possible: unknown
  operators, unsupported versions, invalid parameters, missing or duplicate inputs, type
  mismatches, cycles, and a strategy with no backtest output.

## 4. Compile once, run the same program everywhere

The compiler emits a readable module of `ctx.op(node, operator, version, inputs, params)` calls.
Parameters are normalised through the operator's model (defaults written out) and serialised as
Python literals; node IDs are restricted to safe Python names. The same strategy always produces
byte-identical code, and its SHA-256 identifies the executable strategy.

The studio worker, `qrs run` and exported bundles all call `qrs.runtime.execute()` on that file.
Each run writes `record.json`, which holds:
- the code hash
- the runtime identity: qrs version, hash of the qrs source tree, hash of the dependency lock,
  Python and package versions (since Stage 4, including the exchange calendar library's)
- the source hash of each operator used
- the hash of every dataset used
- the hash of every output, and `result_sha256` over all outputs. Since Stage 4 workstream 3 the
  outputs include each fit node's models, in `models/<node>.json`: every model's cutoff, training
  rows, horizon, features, means, scales, coefficients and intercept. A run without models writes
  none, so its record and result hash are unchanged. Since Stage 5 workstream 1 the outputs also
  include each `qrs.record` node's table, in `records/<node>.parquet`, so analyses can read what a
  strategy computed. A record node has no output and is allowed only in a strategy with a backtest;
  a run without one writes no `records/`, so its record and result hash are unchanged. Results and
  comparisons read only the backtests under `results/`.

The record contains no timestamps, so identical inputs produce identical records.

Editing exported Python creates a separate program; converting Python back into a graph is not
offered.

## 5. Simulation backend: NautilusTrader 1.230.0, behind one adapter

Adopted after the evaluation in [nautilus-evaluation.md](nautilus-evaluation.md). Only
`src/qrs/simulation.py` imports Nautilus. Its execution model:
- A target is decided at a session's close and executed by a market order at the next session's
  open.
- Slippage is a declared number of ticks: buys fill at open + slippage, sells at open − slippage.
- Commission is a fixed amount per contract.
- Margin is a fixed fraction of notional (`StandardMarginModel`).
- Positions are marked at each close.
- An initial-margin shortfall before an order that opens, enlarges or reverses a position, or a
  maintenance-margin breach at a close, fails the run. Orders that only reduce a position are always
  accepted.

The adapter checks that the position after each open equals the plan. Unsupported cases fail
rather than being guessed.

Licence: NautilusTrader is LGPL-3.0-or-later and is used unmodified as a separate library. That is
fine for private use. Any distribution must ship its licence and allow it to be replaced.

## 6. Immutable, content-addressed datasets

A dataset is `bars.parquet` plus `manifest.json`:
- The manifest records the instrument (multiplier, tick, currency, margins, expiry), evidence class,
  source, redistribution permission and the data file's SHA-256.
- The dataset ID is `sha256:` plus the hash of the canonical manifest.
- Validation runs on both save and load: index, columns, finite positive prices, OHLC consistency,
  tick grid, and data ending before expiry. Missing values are rejected, never filled.
- Datasets are published atomically and made read-only. Any later change to data or manifest fails
  the checksum on load.

The synthetic dataset is labelled `evidence_class: synthetic`.

**CSV import (Stage 3):** one function, `qrs.importer.import_csv`, serves `qrs import` and the
upload endpoint, so both produce the same dataset ID from the same file and declaration.
- **Row numbers:** each problem is reported by the file's row number, counting the header as row 1
  and blank lines as rows. The per-session rules (date order, positive prices, OHLC consistency,
  tick grid) live in `datasets.session_problems`, shared with `check_bars`.
- **Settlement-only files are rejected:** a mapping that names one column for several prices, or a
  file whose every row has open = high = low = close.
- **Price units:** cents are divided by 100 in decimal arithmetic before conversion to float, so a
  cents file and a currency file give identical bars.
- **Import report:** the manifest gains an optional `import_report` holding:
  - the file's SHA-256
  - the column mapping and date format
  - the price unit
  - the calendar used, with its library version
  - every expected session between the first and last rows that the file lacks

  The report is left out of the manifest's JSON when absent, so earlier dataset IDs (including the
  Stage 2 demo's) are unchanged. Missing sessions are reported, never filled; the backtest simply
  executes at the next session present.
- **Calendar library:** [pandas_market_calendars](https://github.com/rsheftel/pandas_market_calendars)
  5.5, MIT licence, released 2026-10-05. It builds on exchange_calendars (Apache-2.0), which on its
  own has no CBOT grain calendar. Futures coverage includes:
  - CME Group product calendars (CME/CBOT equity, rates, bonds and agriculture; NYMEX/COMEX energy
    and metals; Globex FX and livestock)
  - ICE US, EUREX and CFE

  A venue it does not cover is declared with no calendar, and every weekday is then expected; the
  report labels that check `weekdays (approximate)`.
- **Upload limit:** uploads over 10 MB are refused. The endpoint requires `Content-Type: text/csv`,
  which is not a CORS-safelisted type, so another web page cannot post a file to the local service
  without a preflight that the service never grants.

**Observations (Stage 4 workstream 4):** a second dataset kind, `qrs/observations`, held as
`observations.parquet` plus its manifest, with the same content addressing, checksums and atomic,
read-only publication. Each row has:
- the event date it is about
- its publication timestamp, stored in UTC
- an optional revision label
- whether that timestamp is an estimate
- one column per declared value, with its unit in the manifest

`qrs.importer.import_observations` serves `qrs import` (a declaration with
`"kind": "qrs/observations"`) and the same upload endpoint, with the same row-numbered rejections:
- every publication timestamp needs an explicit UTC offset (ISO 8601); missing or naive ones are
  refused whatever the evidence class, and no publication lag is ever inferred
- a row repeating another's event and publication time is refused
- a file with any estimated timestamp must say how the estimates were made, which the manifest
  keeps, and cannot be declared `point_in_time` (a `synthetic` one stays synthetic, which is weaker
  still); the evidence class is in the manifest and so in the dataset ID, and cannot change in place
- a timestamp outside the representable range in UTC, such as `9999-12-31T23:59:59-05:00`, is
  refused rather than wrapped

**Documents (Phase 4B workstream 1):** a third kind, `qrs/documents`, held as `documents.parquet`
plus its manifest, with the same content addressing, checksums and atomic, read-only publication.
Each row is one version of a document, such as a news story or a report:
- the document's ID and an optional revision label; no two versions of a document share a revision
  label or a publication time, so which version was the latest at any moment is never ambiguous
- the event date it is about, by default the UTC date of its publication
- its publication timestamp, stored in UTC, and whether that timestamp is an estimate
- an optional title, the text, the text's SHA-256, and an optional source URL that is kept as
  metadata and never fetched

`qrs.importer.import_documents` serves `qrs import` and the upload endpoint, with the observations'
row-numbered rejections and estimate rules: a missing, naive or out-of-range timestamp, a missing ID
or text, and a repeated version are refused by row, whatever the evidence class. A record is
reported by the row on which it starts, since quoted text can span lines. Text is kept exactly,
whitespace included, and hashed as stored; a field may be as long as the 10 MB upload limit, and
error messages never echo a long field whole. The manifest records `hosted_processing`, whether the text may be sent to a hosted
model provider, which every declaration must state: there is no default. No operator takes a
documents dataset, so a strategy never reads raw text, and the data check refuses one wired to a
dataset parameter.

A dataset parameter names the kind it takes (`x-qrs-dataset: qrs/dataset` or `qrs/observations`),
and the data check refuses a dataset of the other kind before a run is queued. Observations never
count towards the sessions a backtest needs.

**A run's evidence class** is the weakest among the datasets it read (synthetic, then
retrospective, then point-in-time). Its results and comparisons show it, so a run that read
retrospective data is never shown as point-in-time.

## 7. Durable jobs in the service, one worker process per run

SQLite (`.qrs/qrs.sqlite`) indexes append-only strategy revisions and runs. The service writes it;
`qrs demo` and `qrs experiment` only add strategies and queued trials, which the service runs. Run
lifecycle:
1. Submission compiles the revision and checks its datasets. Invalid strategies are refused before
   anything is queued.
2. The run is prepared in `runs/.staging/<id>/` with the frozen `strategy.json` and `strategy.py`.
3. Runs execute one at a time in `python -m qrs.worker`.
4. The run directory is moved to `runs/<id>/` together with its log and any error, whatever the
   outcome.
5. A run is marked `succeeded` only if every output matches its recorded hash.

Cancellation terminates the worker. If the service stops, runs in progress become `interrupted`,
and re-running creates a new run. If the service is killed outright, each worker notices its stdin
pipe close and exits, and the next start publishes the interrupted runs' folders and logs. A worker
process isolates crashes; it is not a security sandbox. Only curated operators and trusted local
modules run.

**Time limit (Stage 4 workstream 1):** a run still going after the time limit (30 minutes by
default, `qrs serve --time-limit`) has its worker terminated, and killed if it has not exited 10
seconds later. It fails with the limit as its reason and keeps its log, and the next queued run
starts. `qrs run` runs in-process, with no limit.

The API binds to 127.0.0.1 and rejects other Host headers. A per-launch token is deferred to the
desktop packaging stage.

**Re-runs (Stage 3 workstream 7):** re-running a run queues the same strategy as a new run that
records its `predecessor`: since Stage 4, the strategy and cutoff frozen with the run. A run that
evaluated an experiment's holdout is not re-run; its holdout is evaluated again from the experiment,
which records it. Stores created earlier gain the column when opened.

**Run comparison (Stage 3 workstream 7):** `qrs.comparison` reads everything from run directories,
which freeze what each run used. It compares two or more succeeded runs, each with one backtest, on
the same dataset; anything else is refused with the reason.
- **Common window:** it starts at the latest effective warm-up among the runs (from each run's
  frozen `warm_up.json`), so no run is judged on sessions where another could not yet have a
  signal. Runs from before warm-ups were recorded are refused.
- **Metrics:** each run's metrics are recomputed by `analytics.metrics` from its own ledger sliced
  to the window:
  - the equity from the window's first session, which is the baseline for P&L and return
  - the fills after that session, since a fill on it is already in the baseline equity

  A run may enter the window holding a position. Round trips count from that position, which for a
  whole run is flat, so whole-run metrics are unchanged.
- **Shown for each run:** its own warm-up, the sessions excluded, its position and equity at the
  window start, and its equity change over the window (the overlaid chart).
- **Differences:** operators and parameters that differ are listed by node, with each operator's
  defaults filled in. These are flagged as mismatches above the results, never compared silently:
  - costs and execution assumptions (each backtest's `summary.json`)
  - the runtime: qrs source, Python, dependency lock and package versions
  - the code of any operator that more than one run used
- **Same dataset:** every backtest must have traded the same dataset, the one feeding its bars in
  the frozen strategy. A strategy may also read other datasets for its signals.
- **Same sessions:** runs that end on different dates, because a cutoff ended one early (an
  experiment's trials), are refused, so no full run is measured over sessions another never read.

**Run cutoff in the service (Stage 4 workstream 5):** a queued run may carry an `until` date, frozen
with it as `until.txt` and passed by the worker to `execute`. A re-run repeats the strategy and
cutoff frozen with the run it repeats, not the revision as it is now stored.

**Experiments (Stage 4 workstream 5):** `qrs.experiments`, over the same store, queue and run
directories.
- **The document** (`Experiment`): the base strategy revision; a grid, or a random search with its
  number of trials and seed, over node parameters (each axis lists the values to try); an objective
  metric and its direction; and the holdout start.
- **Bounded:** at most 200 trials. A larger grid, or a random search asking for more trials (or for
  more than its space's distinct points), is refused before anything is queued. A random search
  draws distinct grid points with `random.Random(seed).sample` (a seed of 0 or more), so the same
  seed gives the same trials on the pinned Python 3.12.
- **Validated first:** every trial's strategy is validated and compiled for the data with its
  cutoff before any trial is queued; a refusal names the trial and its values. The strategy must
  have exactly one backtest, and an axis may not name a dataset or list a value twice: every trial
  runs on the strategy's own data, which the holdout splits.
- **Trials are runs:** each is an ordinary queued run of its own frozen strategy, cut off at the
  holdout start, linked to the experiment and its trial number (new `runs` columns, added when an
  older store is opened). Trials run one at a time; a failed, cancelled or timed-out trial keeps
  its status and the next one runs.
- **Ranking:** the succeeded trials over their common window, from run comparison, by the
  objective; ties go to the earlier trial. If comparison cannot rank them, the reason is shown
  instead. The experiment shows how many trials there were beside the best.
- **Holdout:** evaluated only on request, by running a succeeded trial's frozen strategy on the full
  data, and refused while any trial is queued or running, since the ranking the trial was chosen
  from could still change. Its metrics cover only the sessions from the holdout start, from its own ledger, measured
  from the last close before the holdout (`comparison.from_close_before`). That close's equity is
  the baseline and its position is carried in, so the first holdout session's price change, and
  the fills decided at that close with their commission and slippage, all count. (This supersedes
  the plan's "as run comparison computes a window", whose first close would be the baseline.)
  Drawdowns are measured from the baseline too, and round trips from the position carried in.
  Every evaluation is recorded; any after the experiment's first, of whichever trial, is flagged
  as a repeat.
- **Verified and exploratory analysis (Phase 4B workstream 6):**
  - **The flag:** an experiment is verified unless it says `"exploratory": true`.
  - **The check:** creating a verified experiment whose strategy reads any `exploratory` dataset is
    refused before anything is queued, naming the datasets. The check is made once: dataset axes
    are refused, so every trial reads the same datasets, and a dataset's class cannot change in
    place.
  - **Exploratory experiments** run like any other. They are labelled, and their holdout
    evaluations are listed apart from verified ones and never counted as verified.
- **Holdout windows across experiments (Phase 4B workstream 6):** an experiment's holdout window
  is the bars its backtest trades plus its holdout start, stored with the experiment. The bars are
  traced from the backtest's bars input through any operator that passes bars on.
  - **Repeats:** an evaluation is a repeat if its window was evaluated before in this or any
    experiment, of either kind. Each evaluation lists how many verified and exploratory ones came
    before it, counted from the append-only record in the order evaluations were recorded. The
    counts change only when an older store's experiment, left unfilled when it was opened, is filled
    by a later open and its evaluations join the window.
  - **Listing:** an experiment's summary lists every evaluation of its window, verified and
    exploratory apart.
  - **Bars from no dataset** (an extension that makes them): the window is the experiment's own,
    so its evaluations are still repeats of each other, as in Phase 4A.
  - **Older stores:** each experiment gets its window from its document and base revision, and is
    marked verified, when the store is opened. Recorded repeat flags are kept, so an evaluation
    counted within one experiment may show earlier evaluations from others beside no repeat flag.
    An experiment whose strategy revision is missing, or whose bars pass through an operator not
    loaded, is left unfilled for a later open, its own evaluations still tracked.
  - **Limits:** moving the holdout start, or re-importing the bars under another name, starts a new
    window with no recorded looks.
- **Cancelling** an experiment cancels its queued trials, then stops the running one.
- **Interfaces:** the API (`/api/experiments`) and `qrs experiment`, which validates an experiment
  file and queues its trials in the store for `qrs serve` to run.

## 8. Operator and template registries

An operator is a function `run(context, inputs, params) -> outputs`. It is registered with:
- a namespaced ID and version
- a palette family
- typed ports
- a Pydantic parameter model
- a temporal policy, `Temporal(warm_up, alignment)`

Parameter forms come from the model's JSON Schema. Extra operator modules are listed in
`QRS_OPERATOR_MODULES` and loaded at startup. Compiled strategies import any non-built-in module
they use, so workers and exports need no configuration, and exports include the module's source.

**Temporal policy** (Stage 3 workstream 1):
- **Warm-up:** `warm_up(params)` gives the number of sessions before the operator's first valid
  output: one number, or one per output port (MACD's line is valid before its signal). An output's
  effective warm-up is its own plus the longest among the outputs wired into its node. Run
  submission, `qrs run` and export share one gate (`compiler.compile_for_data`), which refuses a
  strategy when the warm-up feeding any backtest leaves no session to trade on that backtest's
  data. Each run freezes
  its warm-up in `warm_up.json` next to its strategy, and results report the first session on which
  a signal can occur from that file, so past results never depend on operators registered later.
- **Label warm-up** (Stage 4 workstream 1): a label output declares its horizon as its warm-up, and
  a label input's warm-up is added to the longest of the node's other inputs, not compared with
  them. A model fitted at the close of session *c* on targets known by then therefore gets the
  features' warm-up, plus the horizon, plus its minimum training rows minus one: its first cutoff.
- **Alignment:** `same_index` or `as_of`. The runtime checks once, for every operator, that all
  inputs and outputs carrying a session index (bars, series, tables, labels, models) share one. An
  `as_of` operator also reads an observations dataset through the run context by publication time.
  The causality check therefore rewrites every observation published after the cut's close as
  well as the later bars, so one reading observations early is caught.
- **Run cutoff** (Stage 4 workstream 1): a run may carry an `until` date (`qrs run --until`). The run
  context is the only route to data, and it returns no bar on or after `until`. The data check
  counts only the sessions before it. The cutoff is written to `record.json` only when set, so
  records of runs without one are unchanged.
- **Causality:** every operator must be causal. `tests/test_operator_contracts.py` runs every
  registered operator, extensions included, on a sample of each port type. It fails if outputs
  appear before the declared warm-up or change when later sessions are rewritten. A label may look
  ahead by its horizon and no further, and a model must not change unless its cutoff is after the
  rewrite. Operators that fit models are also probed at each of their cutoffs, and operators that
  use models just before each sample model's cutoff, where a leak around a model would show. Test
  operators show the checks pass causal operators of each new port type and catch leaky or
  misdeclared ones.

**Dependency lock:** `src/qrs/runtime.lock` is the full dependency closure exported from
`uv.lock`, with exact versions and SHA-256 hashes for every platform. A test fails if it drifts.
Exports ship it as `requirements.txt` for `pip install --require-hashes`. CI pins uv to the version
that wrote it and installs a bundle from it in a fresh virtualenv.

Templates are versioned JSON files. Instantiating one copies the graph and records the template
ID, version and hash.

**Template files (Stage 3 workstream 5):** each `qrs/templates/<id>.json` is validated as the
`Template` contract and holds:
- the strategy graph
- `required_bar`: the kind of dataset the strategy needs, as a manifest's `bar` (only `1D` exists)
- `conditions`: the market conditions it is designed for and the assumptions its results rest on
- `fixture`: an import declaration for `<id>.csv` beside it, and the fills the template gives on it

Parameter bounds are not restated; they come from the operators' parameter models. The templates
are EMA crossover (version 2: the same graph with the new fields), SMA crossover and Bollinger/RSI.

Each fixture's fills are checked three ways:
- the engine reproduces them, and its entry and exit signals equal the reference on every session
- an exact rational reference derives them from the template's plain-English rules
- the app, CLI and export give identical results on the uploaded fixture

**Templates that read features (Phase 4B workstream 5):** a fixture may also hold a documents
declaration and an extraction. The documents are `<id>.documents.csv`, the fixture provider's
answers `<id>.answers.json`, and the responses recorded from them `<id>.responses.jsonl`, committed
so that every platform replays them to the same feature dataset. `templates.prepare` imports a
template's fixture with its features, never calling a provider. Instantiation fills each dataset
parameter with the chosen dataset of the kind it declares, so one call gives bars and features; a
single dataset ID still fills the bars. Only files named like template IDs are templates.
- **News and technical trend** (`news_trend`): MACD 12/26/9 crossing above its signal, the close
  above SMA 200, and a positive 3-day mean sentiment to enter; MACD crossing below to exit, whatever
  the news. Its fixture has a correction, a syndicated copy, a story after the close, a stale story,
  an invalid and a refused answer, and a story usable exactly 3 days before a close.
- **Economic report and ML** (`macro_ridge`): the ridge template's walk-forward model on the
  5-session return, RSI 14 and two figures extracted from monthly reports (growth and tone), each as
  of the close, with first releases and revisions. Two revisions' answers are out of bounds, so their
  figures are missing until the next report. One report comes out after the close and another
  within the latency before it, so each counts from the next session, and a wire service's copy of
  a third is dropped by deduplication. It computes no surprise, which would need dated consensus.

Their references read the documents, the answers and the declared latency from the files, and the
closes from the exchange calendar, not through the importer, the extraction or the as-of operators.

Each fixture also tells its template from neighbouring parameters: periods ±1, k ±0.1 and RSI
thresholds ±1 all change the reference signals, so a template built with any of them fails. Opens
gap one tick above the previous close, so a fill price shows it came from the next open.

**Bollinger/RSI** can never fire entry and exit on the same session:
- entry needs the close below the lower band, and so below the middle band, and RSI below 30
- that makes both exit conditions false: the close at or above the middle band, or RSI above 50

So its "exit wins" never has to decide. The fixture covers each rule separately: an entry while
already long, a close below the lower band without RSI below 30, an exit by RSI alone, and an exit
by the middle band alone. The template's rule nodes are also tested at each boundary:
- the close at the lower band, which is not below it
- RSI at 30, which is not below it
- the close at the middle band, which counts as at or above it
- RSI at 50, which is not above it

Exit-wins is tested end to end on the same graph with its exit threshold lowered to RSI > 25, where
the two signals can coincide. Its position node is also given both signals while long, and must
go flat.

## 9. Indicator, feature, label, rule and position conventions (operator version 1)

- **Price:** one column of the bars as a series. No warm-up.
- **SMA:** mean of the last `period` sessions, computed relative to the window's first value so that
  a constant window gives exactly its price, even for cent prices. Warm-up period − 1.
- **EMA:** α = 2/(period+1). Seeded with the simple mean of the first `period` values; earlier
  sessions are NaN (warm-up period − 1). Recursive, with no bias adjustment. The same EMA function
  serves MACD, where it skips its input's leading warm-up.
- **Bollinger Bands:** middle = SMA(period). Bands = middle ± k × the *population* standard
  deviation (divisor `period`) of the same sessions, from the same window calculation as SMA, so
  a constant window gives zero-width bands. Warm-up period − 1.
- **RSI:** Wilder smoothing. The first value, at session `period`, uses the simple means of the
  first `period` gains and losses. Later averages are (previous × (period − 1) + latest) / period.
  RSI = 100 − 100 / (1 + average gain / average loss); 100 when the average loss is 0, 50 when both
  averages are 0. Wilder averages never reset, so after any move a flat market holds the RSI at its
  last value. Warm-up `period`.
- **MACD:** line = EMA(fast) − EMA(slow), valid from session slow − 1. Signal = EMA(signal) of the
  line, seeded from its first `signal` valid values. The signal and the histogram (line − signal)
  are valid from session slow + signal − 2, declared as per-output warm-ups. fast must be shorter
  than slow.
- **Cross above:** previous A ≤ previous B and current A > B. Missing unless both sessions are
  valid. Cross below is symmetric.
- **Compare and Threshold:** A compared with B, or with a constant, using <, ≤, > or ≥ on the same
  session. They share one comparison function.
- **All, Any, Not:** logical and, or, not of signals. Missing wherever an input is missing, so
  "missing or true" stays missing until every input is valid; this is deliberately not
  three-valued logic. Chain nodes for more than two inputs.
- **Lag:** a series' value from `sessions` earlier. Warm-up `sessions`.
- **Return** (Stage 4): price / price `period` sessions earlier − 1, as a fraction. Warm-up `period`.
- **As of** (Stage 4): the bars' sessions, each decided at its close, which comes from the exchange
  calendar the bars were imported with, early closes included. Bars without one, the synthetic demo's
  included, cannot be joined; the data check refuses the strategy. At each close: the observation
  with the latest event date published by then (at or before the close), in its latest revision
  published by then. Its age runs from that revision's publication to the close; at more than
  `max_age_days` the value is missing, never carried forward and never replaced by an older event.
  The run context returns no observation published after the close of the bars' last session, so a
  run with `until` reads none published after the close of its last session before it. No warm-up:
  missing until the first publication. Of revisions of one event published at the same moment, the
  earliest row stands. Stage 5 workstream 1 vectorised the join (a running maximum over rows in
  order of publication), with identical results: a test compares it with the original loop.
- **As-of aggregate** (Phase 4B): at each close, each document's latest version published by then
  (each event's, for observations without documents), if published at most `window_days` before the
  close (inclusive); their values' mean, sum or count, or missing with fewer than `min_count`
  values. A latest version with no value (an invalid answer) counts as none: never as an earlier
  version's value. So a story counts once, in its latest version, a later version never changes an
  earlier close's value, and a count or sum with no values is missing, not zero. No warm-up.
- **Feature table and Merge** (Stage 4): a table starts from one series as a named column, and each
  merge adds one more. Names are lower-case identifiers, and a merge refuses a name already in the
  table. They add no warm-up: a table is complete from its longest column's warm-up.
- **Forward return** (Stage 4): a label of the return over the next `horizon` sessions, from the same
  calculation as Return, moved back `horizon` sessions. It is missing for the last `horizon`
  sessions and declares `horizon` as its warm-up (decision 8).
- **Fit ridge** (Stage 4): at the close of session c, the training rows are those with complete
  features and a target whose row plus the target's horizon is at or before c, so the horizon
  purges each target until it is known. The first cutoff is the first session with `min_rows` such
  rows; it refits every `every` sessions, on every such row (expanding) or the latest `min_rows`
  (rolling). Each model standardises its features by its training rows' means and population
  standard deviations. A feature constant over them is centred on its value exactly, with a scale of
  1, so it gets no weight even when its value has no exact binary form. It then solves
  (ZᵀZ + αI) b = Zᵀ(y − ȳ) in float64, with the intercept ȳ unpenalised. Models are hashed in the run
  record (decision 4), so they avoid BLAS, whose results differ in the last bits between machines:
  sums of products are exactly rounded (`math.fsum`), and the small system is solved by Gaussian
  elimination in plain Python. Infinite features or targets are refused. Warm-up `min_rows` − 1, on
  top of the features' warm-up plus the horizon (decision 8).
- **Predict** (Stage 4): at each session, the latest model whose cutoff is at or before it, applied
  to that session's features without BLAS; a missing feature takes the model's training mean.
  Missing before the first cutoff. The features must be the ones the model was fitted on. A
  template's predictions must still stay at least 1e-6 from its threshold, so a last-bit difference
  in its features between platforms cannot change a trade.
- **Long/flat and long/short:** one shared function. Start flat. An exit, or long and short entries
  on the same session, go flat (exit wins). A long or short entry alone goes to ±quantity,
  reversing in a single order when on the other side. Repeating the same side adds nothing (no
  pyramiding). A missing signal is no signal, so no position is taken during warm-up.

Changing any of these requires a new operator version.

## 10. Deliberate simplifications versus the audit's proposals

- **Flat modules** under `src/qrs/` instead of sub-packages per area. Split a module when it
  outgrows one file.
- **Charts:** an inline SVG equity chart instead of Plotly.js, until richer charts are needed.
- **Not built in Stage 2:**
  - Tauri packaging, the per-launch API token and request-size limits
  - undo/redo and local crash recovery in the editor (added in Stage 3 workstream 6)
  - CSV import and unit (cents/dollars) normalisation, which belong to the Phase 3 file importer
  - links from a re-run to its predecessor run (added in Stage 3 workstream 7)

## 11. Model providers and stored responses (Phase 4B)

Model providers answer only `qrs extract --record`; nothing else in QRS calls one
([stage-4b-plan.md](stage-4b-plan.md) decision 1).

- **One narrow interface** (`qrs.providers`): an adapter is a module with
  `complete(request, endpoint) -> Reply`. A reply holds:
  - the exact response body and the answer text
  - the response ID and every model identifier the provider reported
  - how the answer finished (normally, truncated or refused) and the provider's stop reason
  - input and output tokens

  Adapters only transport. The registry names each adapter's module, whether it is hosted, the
  decoding options it accepts and its output-token limit, and imports a module only when a
  recording calls it. So replay, runs, experiments and exports never import an adapter.
- **One strict parser** (`providers.parse`) for every provider. An answer is valid only after a
  normal finish, as exactly one JSON object holding exactly the declared fields, each a finite JSON
  number within its inclusive bounds. Repeated keys, `NaN`, numbers given as strings, booleans, any
  surrounding text and nesting too deep to parse are refused. A refused answer has a reason: `truncated`, `refused`,
  `no_answer`, `not_json`, `wrong_fields`, `not_a_number` or `out_of_bounds`.
- **The answer schema** sent with each request asks for exactly the declared numbers. It carries no
  numeric bounds, which hosted structured outputs do not accept; each field's description states
  them, and the parser enforces them.
- **Cost bound:** a request's input is bounded by one token per UTF-8 byte sent, plus a framing
  allowance of 1,024 tokens; its output by its output-token limit, which includes any thinking. Cost
  is tokens at the extraction's declared prices per million tokens.
- **The response store** (`qrs.responses`, `.qrs/responses/<request SHA-256>.json`). The key is the
  hash of the canonical request:
  - provider and model
  - instructions and rendered prompt
  - the answer schema, exactly as sent
  - the output-token limit and the options
  - the capture label

  Credentials, endpoints and budgets are not part of a request, which refuses extra fields.

  A record keeps the request, the exact response body, the reply's fields, the cost, when the
  response arrived and how long it took. It is written once, by linking a complete temporary file,
  flushed to disk, into place, and made read-only. A different record for a stored request is refused, and a record
  that does not parse, or answers another request, is refused when read.

  The store is trusted local state. Its checksums establish consistency, not authenticity:
  - **What they establish:** a record's file name, the SHA-256 of its request, shows which request
    it answers; and a feature dataset's hashes show its files are unchanged since it was published.
  - **What they do not:** that a record came from the provider named, or that its response was
    captured when it says. Anyone with write access to `.qrs` can write a consistent record with
    any answer or capture time. The capture time, which decides whether a row is retrospective, is
    attested only by the machine that recorded it.

  Write-once records guard against overwriting and partial writes, not against edits: a record
  changed by hand is read as stored if it still parses and answers its request. A feature dataset
  holds a copy of every record it was built from, so what it was built from is fixed once it is
  published.
- **The fixture provider** answers from a JSON file, its endpoint, keyed by the SHA-256 of the
  rendered prompt. Like any provider, it sees only the request. A prompt with no entry gets no
  answer, an entry can stop early (truncated or refused), and an answer longer than the
  output-token limit is truncated there, as a real provider's would be; a malformed entry is
  refused. It reports the answers file's
  SHA-256 as its model. It is offline and deterministic.

**Extraction (Phase 4B workstream 3):** `qrs extract EXTRACTION.json` (`qrs.extraction`) turns a
documents dataset into a feature dataset.

- **The extraction document** (`qrs/extraction`) names:
  - the documents, the provider, the model and its endpoint
  - the instructions, and a prompt whose only placeholders are `{title}` and `{text}`, so nothing
    but a document's own title and text, not even its date, reaches a model
  - one to 16 numeric fields with units and bounds
  - the output-token limit, and the provider's decoding options
  - the declared processing latency
  - a capture label, and whether to drop copies of identical text that another document published
    first (once a document has a version kept, its later versions are all kept, so none it
    supersedes keeps counting)
  - a required budget, and whether the features may be exported

  The prompt is rendered in one pass, so text that looks like a placeholder is never substituted.
- **Replay is the default.** It builds the dataset from stored responses alone, and imports no
  adapter. If any document version has no stored response, it fails before publishing and names
  them. Rules that need no adapter are checked by replay and recording alike, before any call:
  - the options the provider accepts, and their types
  - the provider's output-token limit
  - a local provider needs an endpoint, and a hosted one takes none
  - hosted processing needs the documents' consent
  - a provider that cuts a long prompt without saying so (Ollama) needs a context window that holds
    every request whole (see the adapters, below)
- **Recording is explicit (`--record`).** It asks only for requests with no stored response.
  1. **Before the first call:** the budget bounds the whole extraction. Its distinct requests,
     stored or not, must fit the request limit, and the recorded cost of its stored responses, plus
     the upper-bound cost of the missing ones, must fit the cost limit; otherwise nothing is asked.
     Then the adapter checks its endpoint.
  2. **Each response** is stored as it arrives, and logged with its document, request hash, finish
     and tokens.
  3. **Stopping:** a provider error, or a response reporting more tokens than its bound, stops the
     recording. Nothing is published, and running it again asks only for what is still missing.
  4. **Building:** the dataset is then built by replay. A recorded and a replayed extraction
     therefore give the same dataset.
- **Feature datasets are observations.** There is one row per document version:
  - its event date, revision and document ID; the `document` column is never a value column
  - the fields' values
  - its publication time: when it could be used, the document's publication plus the larger of the
    declared and the measured latency. The latency of each version is at least that of the
    document's earlier versions, so a correction is always usable after the version it corrects,
    however slowly that version was answered.

  A row is estimated when its response was captured after that time (retrospective), or when its
  document's timestamp was estimated. Versions that render the same request (a copy, or a revision
  reverting to earlier text) share its stored response, each usable from its own publication. A
  stored response captured before the first of them was published, a copy that deduplication drops
  included, is refused: the store was altered. Capture times are read as UTC, whatever their offset. An invalid response leaves the row's values missing; its reason is recorded and counted.
- **Provenance:** the dataset holds `extraction.json`, `responses.jsonl` and `documents.json`
  beside its data, so an exported feature dataset keeps it without its documents:
  - `responses.jsonl` holds, for each row, its document version, that document's publication time
    and URL, and its stored response, with the rendered request and its validation status
  - `documents.json` is the documents dataset's manifest (its source, file hash and column mapping,
    no text), whose hash is the documents dataset ID

  The manifest's `derivation` records:
  - the first two files' hashes and the documents dataset ID, which loading checks against them
  - the documents dataset, provider, model, and every model the responses reported
  - whether text went to a hosted provider
  - the latency, the capture label, and counts of requests, retrospective rows, duplicates and
    invalid rows
  - tokens and cost

  Imported observations keep their file's hash, columns and event format. Feature datasets have a
  derivation instead, and existing manifests serialise byte for byte as before.
- **Evidence (decision 7):** a new class, `exploratory`, between `synthetic` and `retrospective`.
  A feature dataset's class is the weaker of its documents' class and, if any row is
  retrospective, `exploratory`. Only QRS's own capture record, showing that each response existed
  by the time it could be used, lifts AI outputs above `exploratory`; a model maker's stated
  knowledge cutoff is no such evidence. On synthetic documents, AI outputs stay `synthetic`.
- **Rights:** a feature dataset is redistributable only if its extraction and its documents both
  allow it. Exports then include its responses, and so the documents' text in each rendered
  prompt.

**Adapters (Phase 4B workstream 4).** Three providers, chosen by an extraction's `provider`:
- **`fixture`:** offline, deterministic answers (above).
- **`ollama`:** local inference through Ollama's native chat API (`POST /api/chat`), using only the
  standard library.
  - **Request:** the answer schema as `format`, `stream: false`, `shift: false`, the output-token
    limit as `num_predict`, and the allowed options (`temperature`, `seed`, `num_ctx`); no tools.
  - **Text stays on this machine:**
    - The endpoint's host must resolve only to loopback addresses, checked before every request.
    - The adapter connects to the address it checked with `http.client`, which never reads proxy
      settings, so neither `http_proxy` nor DNS rebinding can send text elsewhere.
    - The model must be listed by Ollama locally, with a digest, and not as a remote model (which
      Ollama would forward to a hosted service). The digest is recorded with the model's name, so
      a re-pulled tag is a different model.
  - **Finish:** a `length` finish is truncated.
  - **Endpoint:** an `http://` base address without user information. A malformed one, an error
    from a server that is not Ollama, and a timeout stop the recording, storing nothing.
  - **Limits:** a loopback port that forwards elsewhere, such as an SSH tunnel, cannot be detected.
    Ollama counts only the prompt tokens it evaluates, so a cached prefix lowers the recorded
    input tokens.
  - **Context window:** Ollama cuts a prompt longer than its context window (`num_ctx`) and still
    reports a normal finish, so an answer could come from part of a document. So:
    - **Every request whole:** an extraction through Ollama must declare `num_ctx`, at least the
      largest request's bound: its UTF-8 bytes, 1,024 tokens of framing and its output limit.
      Otherwise it is refused before any call, in replay as in recording, so no dataset is built
      from answers to prompts that may have been cut. Since the bound counts a token per byte, a
      window that passes holds the prompt whole for any tokenizer whose tokens are at least a byte
      each, provided the model's chat template adds at most 1,024 tokens.
    - **Within the model's context length:** Ollama also lowers a `num_ctx` above the model's
      context length to that length, again without saying so. So before each request the adapter
      reads the model's context length from Ollama (`POST /api/show`), and stops the recording
      before any chat if `num_ctx` exceeds it or Ollama does not report it.
    - **Refused by Ollama too:** each chat sends `shift: false`, so a current Ollama answers a
      prompt longer than its window with an error, which stops the recording, rather than cutting
      it. Older versions either ignore the field or, with chat truncation on (their default), may
      still cut the prompt; the answer then stops at its length and is recorded as truncated, with
      no values. In every version, the two checks above are what keep each prompt whole.
    - **What follows:** on an 8,192-token model with a 256-token output limit, for example, the
      instructions, prompt and schema can total at most 6,912 bytes, so a longer document is
      refused rather than cut. And since `num_ctx` is part of every request's key, raising it asks
      every request again, and answers captured late are retrospective. Declaring `num_ctx` once,
      as the model's context length, avoids that.
- **`anthropic` (optional):** the Messages API through Anthropic's official Python SDK, the
  optional extra `qrs[hosted]`. It is installed for development and CI, and is not part of
  `runtime.lock`.
  - **Request:** the instructions as the system text, the rendered prompt as the only message, and
    structured outputs (`output_config.format`) constrained to the answer schema, with an optional
    `effort`. There are no tools, no sampling settings (the API rejects them, so only stored
    responses replay), no fallback to another model and no beta features. SDK retries are off, and
    the SDK's telemetry is off, since prompts hold document text.
  - **Recorded:** the response ID, the model it reports, the stop reason with a refusal's category,
    and every input token, cached or not.
  - **Finish:** `end_turn` is a normal finish, `refusal` is refused, and anything else is truncated.
  - **Limits:** the output-token limit is 16,000, so requests need no streaming.
  - **Credentials** come from the SDK's own sources, such as `ANTHROPIC_API_KEY`. Nothing in QRS
    reads them, and a test shows that a credential reaches no file, record, log or export.
  - **Without the SDK** only recording through `anthropic` fails; its stored responses still replay.

## 12. Analyses (Stage 5)

The research layer reads what the engine already produces and verifies; it is not a second engine.
See the [Stage 5 plan](stage-5-plan.md) and its amendments.

- **The document** (`qrs/analysis`, version 1): a name, a registered method and its version, typed
  parameters, named inputs and an optional `until`. Inputs are:
  - `bars`: a dataset's session returns, as a fraction (`percent`) or in currency per contract
    (`price_change`, the close's change times the multiplier)
  - `observations`: a column joined at each close of named bars with `as_of_values`, as the As-of
    operator joins it
  - `equity`: a succeeded run's backtest equity, as session returns
  - `record`: a column of a table a succeeded run saved with `qrs.record`
  - `fills`: a run's backtest fills summed per session of its equity (quantity bought less sold,
    commission or slippage; 0 without a fill)
  - `model`: a term (a feature's coefficient, on the standardised feature as fitted, or the
    intercept) of the models a run's fit node fitted, at the session at whose close each was fitted
- **Methods** are registered and versioned like operators (`qrs.methods`), declare which input kinds
  each input accepts, and declare each table or figure as holding rows of their inputs or only
  aggregates, for data rights. They read inputs only through `analysis.Context`, and return values,
  warnings, tables and SVG figures. They know nothing of any market.
- **One cutoff:** the effective cutoff is the earliest of the analysis's `until` and the cutoffs of
  the runs it reads. Bars are read through `runtime.Run`, so its rules hold: no bar on or after the
  cutoff, and no observation published after the close of the last session before it. Run outputs
  are cut at it too, and a run whose outputs no longer match its record is refused.
- **Checked before queuing:** the method, its parameters, the input names and kinds, every dataset
  and its kind, an observations column, bars that start before the effective cutoff, and that every
  run read has succeeded, with one backtest for its equity, or the record node and column named.
  Problems are listed together.
- **Execution:** a second job kind in the run queue, with the same worker, time limit,
  cancellation and statuses. `analyses/<id>/` holds the frozen `analysis.json`, `tables/*.csv`,
  `figures/*.svg`, `result.json` (values with NaN as null, warnings, assumptions, the cutoff, the
  weakest evidence class read, and each output's rights) and `record.json`: the document's hash,
  the method's source hash, the runtime identity, SciPy's version, what each input read with run
  result hashes, the datasets, every output's hash and `result_sha256`. It has no timestamps, so a
  rerun on the same machine gives the same hash. Every analysis is kept, failed and cancelled ones
  too. `qrs analyse FILE --out DIR` runs one in-process.
- **SciPy** (amendment A1) is the optional extra `qrs[research]`. Only methods and the functions of
  `qrs.stats` that need it import it, when called; a strategy run (in a worker, by `qrs run` or from a
  bundle) never does, which a test checks in fresh processes, and `runtime.lock` does not list it, so
  run records, bundles and export parity are unchanged. Without it, the CLI and the service still
  load, and analyses are refused, naming the extra.
- **Descriptive methods** (Stage 5 workstream 3) summarise and test nothing: `qrs.summary`,
  `qrs.coverage` (sessions and missing sessions of bars; rows, events, revisions, estimated rows and
  publication lag of observations), `qrs.distribution` (a histogram), `qrs.rolling` (rolling mean
  and population deviation through `window_stats`, as the `qrs.rolling_std` operator computes it),
  `qrs.seasonal` (each calendar month's return in each year, compounded for fractions and summed for
  price changes, without the sample's first and last months, which may be partial),
  `qrs.event_study` (returns around the sessions at whose close an observation was first known; not
  the bars' first session, which has no close before it) and
  `qrs.feature_return` (a feature at t against the return from t to t + h, where all of it was read,
  with Spearman's correlation; overlapping horizons are warned and the effective n is n / h).
- **Figures** come from one renderer, `qrs.figures`, in Python with no dependency: line, bar,
  scatter, heatmap and estimate-with-interval charts as SVG of a fixed size and fonts, coordinates
  rounded to 0.01, all text escaped, and no timestamps, so the same numbers give the same bytes.
  The API serves a figure only if its analysis declared it, as `image/svg+xml` under a sandboxing
  content security policy, and the studio shows it as an image.
- **One calendar:** inputs are matched by session, so an analysis's inputs must all be on the
  sessions of one exchange calendar (or of one dataset without one): bars their own, observations
  their `on` bars', equity its run's traded bars', and a record the bars feeding its node. Otherwise
  a value known at one venue's close could be paired with a return that ended earlier at another.
  Infinite values (a return from zero equity) are missing.
- **Budget:** an input may be on at most 20,000 sessions, refused before queuing; descriptive
  methods over 50 years of daily data by 20 series take under 10 s, which a test checks.
- **Projects** (`qrs/project`, workstream 4): a question, the datasets in scope and a confirmation
  start. From it, those datasets' sessions are the test period. An analysis in a project has its
  `until` capped at the confirmation start; only a family's evaluation reads from it. Notes are
  append-only revisions.
- **Families** (`qrs/family`): hypotheses registered together as one frozen unit, with the
  significance level and the correction (Holm, Benjamini–Hochberg or Benjamini–Yekutieli), stored
  with the SHA-256 of their canonical JSON. A family declares itself confirmatory or post hoc. A
  hypothesis is a statement, the analysis that tests it and a direction; its inputs must lie in the
  project's scope. It reads a strategy's results only through a symbolic reference to an
  experiment's trial at a rank (`trial`), and that experiment's trials must stop at or before the
  confirmation start.
- **Read history** (amendment A2): an append-only `reads` table, one row per dataset a job may read
  with its cutoff (none for a full read), its kind, its source and the family whose evaluation it
  is. It is written before each run, trial, holdout evaluation and analysis is queued, and by
  `qrs run`, `qrs analyse` and every export (a full read, since a bundle runs anywhere). A store
  from before it is backfilled once, when opened: each run's datasets from its frozen strategy and
  cutoff, each analysis's from its document, and each extraction's, a full read of its documents,
  from the feature dataset it made. A job whose datasets cannot be told (its directory is
  gone, or it uses an operator not loaded) is an unknown read of every dataset present then, in
  full, or for a trial to its holdout start. A read reaches the test period when it has no cutoff
  or one after the confirmation start.
- **Failing closed:** confirmatory registration is refused, naming the reads, once any read has
  reached the test period of a dataset in scope; post hoc families can always register. A
  confirmatory family's evaluation is refused once, after its registration, the test period was
  read by anything other than an evaluation of one of the project's confirmatory families; it is
  then shown as compromised, which follows from the read history and cannot be undone. Only reads
  until the family is settled count: once every hypothesis is evaluated or withdrawn, nothing read
  later can change what it concluded, so later use of the data does not relabel it.
- **Evaluation:** each hypothesis is evaluated once, from the confirmation start. A trial reference
  is resolved then: once every trial has finished, the trial at the rank is chosen, its holdout
  evaluation queued (and recorded as one), and the analysis reads its equity. Refusals before
  anything reads the test period bind nothing; once the holdout is queued, the evaluation is bound
  to the hypothesis even if the analysis cannot be queued, and counts as p = 1. A withdrawn
  hypothesis counts as p = 1 and cannot be evaluated; an evaluated one cannot be withdrawn, which
  would hide its result, and withdrawals take the same lock as evaluations.
- **The same data under another ID:** a read counts for every dataset holding the same data or
  imported file, and for every feature dataset extracted from the same documents and those documents,
  followed to a fixed point (features of the same documents file imported twice are one data).
  Extraction is a full read of its documents. A file with different content (a refreshed download
  with one more session, a contract and the continuous series built from it) is not related. The history belongs to one store: data copied into a
  new home starts with none there.
- **One evaluation at a time** in the service, so a hypothesis is never evaluated twice at once. Of
  jobs queued in the same millisecond, runs start before analyses, so an evaluation's analysis never
  starts before the holdout evaluation it reads. The
  history and its backfill are created in one transaction, so a failed backfill leaves no history
  and is retried at the next open, never an empty one.
- **Limits:** registering several confirmatory families and choosing which to evaluate is a
  selection the per-family correction does not cover; every family, evaluated or not, is listed
  with its project. An evaluation whose binding row was stored is bound for good, as failed (p = 1)
  if a restart interrupts its jobs; if the process dies in the instant between its first
  test-period read and storing that row, nothing binds it, and the read compromises no family.
- **Immutability:** projects, note revisions, families, withdrawals, evaluations and reads are only
  inserted; SQLite triggers refuse `UPDATE` and `DELETE` on them. The store remains trusted local
  state (decision 11): this guards against mistakes, not deliberate edits of `.qrs`.
- **Tests and intervals** (workstream 5, `qrs.stats` and `qrs.methods.inference`): Newey–West (HAC)
  covariance with Bartlett weights at the plan's lag, max(⌊4 (n/100)^(2/9)⌋, h − 1), scaled by
  n / (n − k), with Student t on n − k degrees of freedom; Holm by hand and Benjamini–Hochberg and
  –Yekutieli from SciPy; the stationary bootstrap with the block length exactly as the arch package
  computes it (Politis and White, corrected by Patton, Politis and White); percentile intervals.
  Each is checked against statsmodels and arch values committed in `tests/reference/inference.json`
  and against Benjamini and Hochberg's worked example. Methods: `qrs.mean_test`, `qrs.regression`
  (forward returns on a feature's level or change, every session or at its changes),
  `qrs.event_test` (returns or absolute returns on event sessions against others),
  `qrs.seasonal_test`, `qrs.return_metrics` (annualised mean, volatility and Sharpe with bootstrap
  intervals; maximum drawdown as a point value), `qrs.rank_correlation` and
  `qrs.paired_difference`.
- **Small samples:** the seasonal Wald test is an F test on (months − 1, years − 1) degrees of
  freedom, not χ²: each month's mean is estimated from its years, and against χ² it rejected about
  38% at 5% with 8 years of i.i.d. data, 16% with 20 and 10% with 40. As F, with HAC errors, it
  still over-rejects with few years: about 10–12% with 2 to 4 years per month, 8% with 5 to 10, 7%
  with 12 and 6% with 20 or more, which its results state below 20 years. Each month's own interval
  uses its years less one. HAC t tests are
  oversized in autocorrelated samples: at 5% with AR(1) φ = 0.3, 13% at n = 20, 10% at n = 100, 8%
  at n = 250 and 6% at n = 1,000. The predictive regression with overlapping horizons rejects more
  often still, most with few pairs and a persistent feature: for h = 21 and lag-1 autocorrelation
  0.9, about 25% at n = 120, 19% at 250, 12% at 500 and 10% at 1,000; 12% with an independent
  feature at n = 100. Its result warns whenever the sampled horizons overlap. The lag is at least
  h − 1 only then: pairs sampled where the feature changes, h or more sessions apart, take the
  plan's lag, since a longer Bartlett window there shrinks the errors (24% at 5% for 36 monthly
  pairs at h = 21, against 8.5%). A change is taken against the previous session's value, so none
  spans a gap in the feature, and a constant return gives no test. Coverage of 95% intervals on AR(1) data (φ = 0.5, n = 1,000) is 92.8% (HAC) and
  93.5% (bootstrap), against 73.8% for the naive interval; the volatility interval of
  `qrs.return_metrics` covered about 77% on GARCH(1,1) returns, though its block length follows the
  squared returns too. Each method states its measured size. Constant inputs give no test.
- **Sums (P13):** means that decide a test use `math.fsum`; the HAC matrices use numpy's products,
  whose last bits can differ between machines, inside the 1e-9 relative policy runs follow.
- **Hypotheses are tested only by testing methods.** A method declares a `directional` test (an
  estimate and a symmetric two-sided p-value) or a `joint` one (two-sided only). A hypothesis's
  p-value is one-sided in its registered direction: half the two-sided one when the estimate lies
  that way, one less half of it otherwise. Once every hypothesis is evaluated, withdrawn or failed,
  the family's p-values are adjusted by its correction, and a hypothesis is supported only when its
  adjusted p-value is below the family's level. Withdrawn hypotheses, and evaluations that failed or
  gave no p-value, count as p = 1.
- **Selection bias** (workstream 6, `qrs.methods.selection`): an `experiment` input reads a finished
  experiment's trials, and its succeeded trials' session returns over their common window
  (`comparison.common_window`), best first by its objective, each verified against its record and
  cut at the holdout start. N is every trial of every experiment on the same holdout window, failed
  ones too, except experiments whose axes vary only backtest costs; every result shows N and the
  window's experiments. `qrs.probabilistic_sharpe` and `qrs.deflated_sharpe` follow Bailey and
  López de Prado (per-session Sharpe ratio, population skewness and kurtosis, the expected maximum
  of N with the Euler–Mascheroni constant), reproduce their published example (0.1132 and 0.9004),
  assume independent returns and warn of a significant lag-1 autocorrelation; the deflated ratio
  applies to the trial the experiment's objective chose, and says the objective is not the Sharpe
  ratio. `qrs.reality_check` is White's, against a zero return, sharing one stationary-bootstrap
  resampling across trials (block length the median of theirs), listing failed trials; its p-values
  agree with arch's (`SPA` without studentizing, its "upper" p-value) within Monte Carlo error, on
  committed reference values; its measured size at 5% is 6–9%, which it states; its p-value counts the sample among the resamples, so it is
  never 0. 200 trials by 2,500 sessions by 1,000 replicates take under 60 s.
- **Selections, after review:** an experiment is read only to its holdout start (an earlier
  `until` is refused, since the ranking that chose the best trial read every session to it); a
  trial that never traded has a Sharpe ratio of 0; a search over costs alone (commission and
  slippage only) deflates nothing and says so; the variance of Sharpe ratios comes from the
  experiment's own trials, and few or alike trials are warned. Any analysis reading a run that is a
  trial, or a trial's holdout evaluation, or a re-run of either, records which experiment and trial
  it is and its N, and says so: best-of-N results always show N.
- **Robustness** (workstream 7, `qrs.methods.robustness`): `qrs.sensitivity` shows an experiment's
  objective over one or two axes (a line or a heatmap), each point the trial's own metric over the
  common window, other axes held at the best trial's values, with the best trial's neighbours one
  step along each axis; a search over commission and slippage shows cost sensitivity, with no
  mechanism of its own. `qrs.subperiods` splits returns by calendar year or into equal parts; each
  part's sessions and total (summed, or compounded for fractions) reconcile with the whole, and its
  ratios are not reconciled. `qrs.regimes` compares returns on sessions whose previous close had a
  label above a threshold with the others (HAC difference in means), the label lagged one session of
  the returns' own sessions, so a label published between two of them never sets the regime of the
  return spanning it;
  with `rank`, the label's expanding percentile rank through `expanding_ranks`, which the generic
  `qrs.expanding_rank` operator shares (over the label's whole history read, as below); a regime with
  fewer than 30 effectively independent returns, or fewer than 5 episodes, is warned.
- **Reports** (workstream 8, `qrs.reports`, `qrs/report`): a project, a title and sections of
  blocks: text, or an analysis's figure, table or values, or a family's results, referenced by ID.
  `qrs report build` (and `POST /api/reports`) writes `report.html`, self-contained with its SVGs
  inline, no scripts or external resources, every text escaped and a content security policy
  forbidding any; `evidence.json`; and `record.json` with both files' hashes and the report's hash.
  The evidence is fixed at a point in the store's history (`projects.AsOf`: a time and the last read
  then): each dataset with its source, evidence class and rights; the read horizons then; each
  analysis with its result and output hashes, rights and evidence class; how many of the project's
  analyses had ended each way; every family registered by then, with its conclusions as they stood;
  each finding's weakest evidence class and label (confirmatory, post hoc or compromised); and the
  runtime identity. An analysis that finished after that point counts as running. `qrs report
  verify` rebuilds both files as of the same point, checking every artefact against its record,
  and compares them byte for byte, so any changed artefact is found and the store growing after the
  build changes nothing. The project status functions take the same `as_of`, so reports and the
  service share one implementation. The point is taken only once the clock has passed it, so nothing
  stored afterwards shares its millisecond.
- **What a report rests on, after review:** only the project's own analyses may be shown, so its
  count of analyses run and failed covers them; every family registered by the point is listed, and
  every evaluation of any of them that had succeeded is checked against its record, so each number
  shown traces to a hash, while failed, running and withdrawn ones are shown by their state (p = 1
  where they count). Each analysis's record must match the store; the runs and experiment trials it
  read are listed with their result hashes, each dataset with its data hash; and the report states
  the weakest evidence class of its findings.
- **Rights (P11):** an analysis output is redistributable only if every dataset it read, directly or
  through runs, is; a report lists what is not, says so at its top, and `--shareable` refuses to
  build it. Outputs are labelled rows or aggregates. Strategy bundles still include only
  redistributable data.
- **Studio research views** (workstream 9): a Research dialog with projects (their notes, read
  horizons and families, with each hypothesis's outcome; families registered behind a review step
  that shows the horizons, warns when a confirmatory family will be refused, and says the family
  cannot be changed), analyses (created through the same generated `ParamsForm` as operator
  parameters, now taking any JSON Schema with lists and booleans too, and viewed with their values,
  warnings, tables and SVG figures shown as images) and reports (built from a project's analyses and
  families, shown in a frame with an empty `sandbox`, and verified against the store). The view
  logic is in `research.ts`, checked by Vitest; a browser test drives the whole flow offline.
- **Continuous futures** (workstream 10, amendment A3): an instrument either has an expiry or is a
  continuous series with none (`Instrument.continuous`), recording the provider's roll rule, its
  adjustment (`difference` or `none`), and every contract it is built from: symbol, expiry, first
  and last session (before the expiry) and the adjustment added. Bars are refused unless the
  contracts cover every session exactly once, in order, each starting and ending on a session (so
  every roll is on a session and recorded); a continuous series is `retrospective` at
  best. The data check refuses a backtest whose bars are continuous, before anything is queued:
  their prices are not executable. Analyses read a continuous series only as price changes per
  contract, and only difference-adjusted (a spliced series' roll gaps, at fixed months, would make
  seasonality); the record lists its roll sessions. A run's record or fitted model fed by a spliced
  continuous series cannot be read by an analysis, since its levels carry the roll gaps; one fed by a
  difference-adjusted series, computed beside a traded contract, can (A3). Instruments
  that are not continuous leave both
  fields out of their manifests, so every existing dataset ID is unchanged.
- **The corn programme** (`research/corn/`) is content: declarations, synthetic fixtures with a
  deterministic generator, the programme's documents with placeholders, a README on sources and
  terms, and a driver using only the API and the `qrs` commands. It runs end to end offline in the
  suite, and a test checks that no programme term (corn, wasde, usda, nass, cftc, crop) appears in
  `src/qrs/`. No capability had to be added to the core for it.
- **After the programme's review:** the provenance must agree with itself and the prices: each
  contract once, expiries in order, adjustments on the tick grid, a difference-adjusted series of
  several contracts with adjustments, and no contract priced at zero or less as traded. The Return
  and Forward return operators refuse a continuous series, so its percentages cannot reach an
  analysis through a record either. The seasonal methods leave out a month at either end of the
  sample only when it is partial (fewer than 80% of the median sessions of its months), so a test
  period of whole years keeps them all; regimes rank the label over its whole history read and lag
  it over the returns' whole sessions before the sample starts at `since`; roll sessions are
  recorded from `since`.
