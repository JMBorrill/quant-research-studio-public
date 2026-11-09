# Stage 5 plan: Phase 5, the research layer

**Status:** approved on 2 November 2026: decisions P1–P14, the 11 workstreams in their order and
the 15 exit criteria, with three amendments, A1–A3 under [Amendments](#amendments), which take
precedence over anything below that they change. Starts from
`main` at `09bdd30`, where Phase 4B, the CI changes (#27) and the `CLAUDE.md` correction (#28) are
merged and verified locally.

**Basis:**
- **The roadmap as this repository records it:** Phase 5 was reserved for corn data and importers
  ([Stage 4 plan](stage-4-plan.md#not-in-stage-4), [Phase 4B plan](stage-4b-plan.md#not-in-phase-4b)),
  and Phase 6 for desktop packaging and the per-launch API token. The Stage 1 Audit and its
  Addendum are not in the repository, so this plan relies on them only as the plans and
  [decisions](decisions.md) quote them.
- **The Phase 5 brief (2 November 2026):** generalise QRS into a quantitative research platform:
  1. research projects and hypothesis tracking
  2. exploratory data analysis and visualisation
  3. statistical hypothesis testing and uncertainty
  4. robustness, sensitivity and regime analysis
  5. reproducible reports and evidence trails
  6. integration with strategies, experiments, ML and AI-derived datasets
  7. a corn futures research programme demonstrating the whole workflow

  It also sets limits: no commodity-specific logic in the core, no live trading or autonomous
  execution, and no reliance on hosted CI.
- **Standing rules:** the decisions in [decisions.md](decisions.md) and `CLAUDE.md`: one
  authoritative implementation per behaviour, no speculative abstraction, and dependencies only
  when needed.
- **An independent review of the draft** found problems that are corrected here:
  - Confirmation looks could be evaded.
  - Families could be gamed.
  - Record-only strategies needed unscoped engine changes.
  - Analysis hashes would not be stable across machines.
  - The as-of alignment was too slow for the budget.
  - Several statistical defaults were wrong.
  - The corn data did not fit the bars contract.
  - Rights rules were too loose.
  - The scope was over-built.
  - The order did not match the dependencies.

## Amendments

Approved with the plan; where they differ from the text below, they govern.

**A1. SciPy for established statistical inference, isolated from the strategy runtime (changes P1).**
- Distributions and established procedures (normal, Student t and χ² distributions; rank
  correlation; `scipy.stats.false_discovery_control` for Benjamini–Hochberg and –Yekutieli) come
  from SciPy (BSD), not hand-written code. Holm, Newey–West, the stationary bootstrap and the
  selection-bias statistics are written against their published definitions, as SciPy has none.
- SciPy is an optional extra, `qrs[research]`, as `qrs[hosted]` is. The dev group installs it.
  `runtime.lock` is exported without extras, so it, strategy bundles and their export parity are
  unchanged; `uv.lock` changes and is committed.
- Only the research modules import it. Strategy execution (`runtime`, operators, `simulation`,
  `compiler`, a worker executing a run, and exported bundles) never does: a test runs a strategy
  in a subprocess and checks that SciPy was not imported, and another that `runtime.lock` does not
  list it.
- Run records are unchanged: SciPy is not added to `runtime_identity()`. Analysis records give its
  version in a field of their own. An analysis is refused before it is queued, naming
  `qrs[research]`, when SciPy is not installed.

**A2. Confirmatory research fails closed; registration and read history are immutable (changes
section 3 and P4, and exit criterion 2).**
- **Read history** is an append-only `reads` table: one row per dataset a read may touch, with its
  cutoff (none for a full read), its kind, what made it, and the family whose evaluation it is,
  if any. It is written when a read is queued or made, and is not derived from run directories,
  which can be deleted. Reads are:
  - each run, experiment trial, holdout evaluation and analysis queued by the service
  - `qrs run` and `qrs analyse`, in the store of the data directory they read
  - `qrs export`, as a full read of the strategy's datasets, since the bundle can run anywhere
  - A queued read counts whatever happens to it: failed and cancelled runs may have read data.
- **A Phase 4 store** is backfilled once, when opened, from each run's frozen strategy and cutoff.
  A run whose directory is gone is an **unknown read**: a full read of every dataset present at the
  backfill (a trial's cutoff is still its experiment's holdout start). Data imported later is not
  affected.
- **A read reaches the test period** of a dataset when it has no cutoff, or a cutoff after the
  confirmation start (cutoffs are exclusive).
- **Families declare themselves** confirmatory or post hoc at registration.
  - **Confirmatory registration is refused** if a read has reached the test period of any dataset
    in the project's scope. Post hoc families can always be registered, and are labelled post hoc
    everywhere.
  - **Confirmatory evaluation is refused** if, after registration, the test period of a dataset in
    scope was reached by anything other than an evaluation of one of the project's confirmatory
    families. Those families are frozen, so reading for one cannot change another. A refused
    family is shown as compromised, which is derived from the read history and cannot be undone.
  - Evaluations are queued through their family, which records itself on their reads, and may read
    only datasets in the project's scope.
  - An experiment named by a confirmatory specification must exist, have finished, and have a
    holdout start no later than the confirmation start, when its hypothesis is evaluated.
- **Immutability:** projects, note revisions, families, withdrawals, evaluations (with their
  results and the runs or trials their symbolic references resolved to) and reads are inserted,
  never updated or deleted. SQLite triggers refuse `UPDATE` and `DELETE` on those tables, and tests
  check it. Each hypothesis is evaluated once: its first evaluation is binding, and a failed one
  counts as p = 1 and is not retried.

**A3. Continuous futures keep complete roll, adjustment and contract provenance, and are never
executable prices (changes section 7, P9, P10 and the corn programme).**
- A continuous instrument (`Instrument.continuous`) records:
  - the roll rule, as the provider states it
  - the adjustment method: `difference` (back-adjusted by price differences) or `none` (spliced)
  - every contract it is built from, in order: contract symbol, expiry, first and last session in
    the series, and the adjustment added to that contract's prices
- The import refuses a series whose contracts do not cover every session exactly once, in order,
  each ending before its expiry.
- **Not executable:** the backtest refuses a continuous instrument. Research methods use price
  changes per contract on it, never percentage returns, and report its roll sessions. They accept
  only `difference`-adjusted series: a spliced series' roll gaps, at fixed months, would
  manufacture seasonality.
- **Features of a continuous series** come from methods, from observations aligned by an analysis,
  or from a run that trades an executable contract and records operators computed on the
  continuous series beside it.
- **Family B** therefore trades an individual, unadjusted contract dataset, which has an expiry and
  executable prices; its confirmation window ends before that expiry. The corn programme adds that
  contract's fixture. Family A's analyses use the continuous series.
- Datasets that are not continuous leave the field out of their manifests, so their IDs are
  unchanged.

## Starting point

`main` passes the local gate:
- 597 Python, 13 browser and 33 Studio tests
- export parity for 7 bundles
- historical hashes unchanged

Hosted CI and macOS remain outstanding until runners return.

| Phase 5 need | What exists | Change |
|---|---|---|
| Immutable inputs | Content-addressed bars, observations, documents and feature datasets (`datasets.publish`, `verified`) | Analyses read them through `runtime.Run`, which already applies `until` |
| Point-in-time alignment | `qrs.as_of` via `as_of_values` (`operators/data.py`); `until` cutoffs; `extraction.usable_times`; the causality harness | `as_of_values` is vectorised, with identical results, because it is too slow for research panels |
| Derived series | Operators compute features, signals and predictions inside runs, but a run saves only backtests and fitted models | A generic `qrs.record` operator saves a table beside a backtest (P3) |
| Strategy results | Run directories: `equity.csv`, `fills.csv`, `summary.json`, `models/*.json`, `warm_up.json`, `record.json` | None. Analyses read succeeded runs' outputs |
| Search and selection | Experiments: up to 200 trials, ranking by an objective, holdout windows (`store.SAME_WINDOW`), repeat counting | None. Trial counts and trial equity feed selection-bias tests |
| Evidence | `synthetic < exploratory < retrospective < point_in_time`; `datasets.weakest` | None. Findings take the weakest class of their inputs |
| Jobs | One worker process per run, with a time limit and cancellation; `JobManager` handles runs only | A second job kind, the analysis run. This is a real change to `jobs.py` (P2) |
| Rights | `redistributable` on each dataset; bundles include only redistributable data | Applied to analysis outputs and reports (P11) |
| Reproducibility | `canonical_json`, `sha256_hex`, result hashes; hashes compared on Linux x86_64, metrics within 1e-9 elsewhere | The same policy for analyses (P13) |
| Studio | Canvas, `ParamsForm` (scalar fields, typed for operators), runs panel, compare and experiment dialogs, one SVG `LineChart` | Research views. `ParamsForm` is generalised for method parameters |

**What does not exist today:**
- no project, hypothesis or report entity
- no statistics: no Sharpe or volatility, no standard errors, intervals or tests, and no
  multiple-testing correction; the trial count is shown but never used statistically
- no way to analyse a dataset or a derived series outside a backtest
- no charts beyond equity lines

## Design principles

1. **A layer, not a second engine.** Analyses read what the engine already produces and verifies:
   datasets, runs and experiments. Features are computed by operators inside runs. Research adds no
   second implementation of an indicator, alignment, return or metric; it reuses `as_of_values`,
   `simple_returns`, `window_stats` and `analytics.metrics`.
2. **One new unit of computation, the analysis run,** built like a run:
   - a declarative document
   - validated before it is queued
   - executed by the worker
   - an immutable directory with a record and hashes
3. **Generic methods only.** Methods are registered, versioned and typed like operators, and know
   nothing of any market. Commodity knowledge lives in data, declarations, project content and
   templates.
4. **Honesty enforced by the system:**
   - hypotheses are registered before they are tested, in frozen families
   - every read of data is accounted for
   - best-of-N results carry N
   - every finding carries the weakest evidence class of its inputs

   The store is trusted local state ([decision 11](decisions.md)), so these rules guard against
   mistakes and self-deception, not against someone editing `.qrs`.
5. **Reports are generated, not written.** A report references stored artefacts. It renders
   deterministically, and its evidence trail is fixed at a point in the store's history.

**Terms.** To keep one meaning per word:
- **Descriptive** methods summarise data; the brief calls them exploratory data analysis.
- **`exploratory`** stays the evidence class and the experiment flag of Phase 4B.
- A hypothesis is **confirmatory** or **post hoc**.

## Proposed decisions

### 1. Analyses

- **The document** (`qrs/analysis`, version 1):
  - a method ID and version
  - typed parameters, validated like operator parameters
  - inputs
  - an `until` (exclusive)
  - a seed, where the method resamples
  - optionally, its project
- **Inputs** are one of:
  - **A dataset:** bars, observations (imported or AI-derived) or chosen value columns.
  - **A succeeded run's outputs:**
    - equity, from which returns are derived with `simple_returns`
    - fills
    - fitted models (`models/*.json`)
    - tables saved by `qrs.record`
  - **An experiment:** its trials' parameters, metrics, equity and status.
- **Alignment:**
  - **Calendar:** inputs are aligned on a base bars calendar.
  - **Observations** are aligned as of each close with `as_of_values`, so a value appears only
    after it is published.
  - **Forward returns** from close t to t+h pair with a feature at t only when t+h is before
    `until`.
- **Effective cutoff:** the earliest of the analysis's `until` and the cutoffs of the runs it
  reads. A run's output is never read past the run's own `until`.
- **Execution:** queued and run by the worker under the time limit, with cancellation. The run
  directory is `analyses/<id>/`:
  - `analysis.json`, the frozen document, with each input's resolved hash
  - `tables/*.parquet` and `figures/*.svg`
  - `result.json`: estimates, intervals, p-values, n, the effective n, and warnings
  - `record.json`: the runtime identity, input and output hashes, and `result_sha256`
- **`qrs analyse FILE --out DIR`** runs in-process, as `qrs run` does.
- **Every analysis run is recorded,** failed ones too. Projects count them, and reports state the
  count.

### 2. `qrs.record`, and faster as-of alignment

- **`qrs.record`** (operator, `evaluation` family, version 1):
  - It takes one table (a port has one type; `qrs.table` turns series into a table) and a name.
  - The run saves it as `records/<node>.parquet`, and it is included in the run's result hash.
- **Placement:** record nodes are allowed only beside a backtest, so decision 3's rule (every
  strategy has a backtest output) and `graph.validate`, the warm-up gate and comparison are
  unchanged.
- **Hashes:** runs without record nodes write exactly what they write today, so every existing hash
  is unchanged.
- **Tests:** the operator has no output for the causality harness to check. A test checks that the
  saved table equals its input and is cut by `until`.
- **`as_of_values`, vectorised** (`searchsorted` over publication times): its outputs are
  identical on every existing test, template and hash. One series on 12,615 closes × 1,200 rows
  takes 16.6 s today; the target is under 0.2 s.

### 3. Projects, hypotheses and families

- **Project** (`qrs/project`, version 1):
  - a name, a research question and the datasets in scope
  - a **confirmation start**
  - notes, as append-only revisions

  Every analysis in a project, except a hypothesis evaluation, has its `until` capped at the
  confirmation start.
- **Hypothesis** (inside a family):
  - a statement and its **test specification:** the analysis document, the statistic, the
    direction, and the evaluation window from the confirmation start
  - The specification may refer to things that do not exist yet: "experiment X's top-ranked
    trial", or "that trial's holdout evaluation".
- **Family** (`qrs/family`, version 1), registered as one frozen unit:
  - its hypotheses, the significance level and the correction method (P5)
  - It is stored append-only with its SHA-256 and time.
  - It cannot gain members or change.
  - A hypothesis can be withdrawn, which is recorded and counts as p = 1.
  - A family of one is allowed, and is declared as such.
- **Conclusions** are derived, never chosen: a hypothesis is supported when its adjusted p-value is
  below the family's level in the registered direction. A family's adjusted results appear when
  every member is evaluated or withdrawn; a failed evaluation counts as p = 1.
- **Read horizons:** for each dataset, the latest date anything has read:
  - every run (to its `until`, or the data's end)
  - every experiment trial and holdout evaluation
  - every analysis (to its effective cutoff)

  (Superseded by A2: an append-only read history.) Horizons are derived from the run and analysis records already kept, and Phase 4 stores give
  theirs when opened.
- **Confirmatory status** (superseded by A2, which fails closed): a family is confirmatory only if, when it was registered, no dataset its
  hypotheses evaluate had been read at or past the confirmation start. Otherwise it is **post
  hoc**, labelled everywhere, and never reported as confirmed.
  - Ordinary runs count as reads (P4): a full backtest of the data is a look at it.
  - An experiment's holdout evaluation on the same bars is a read too, so experiments and analyses
    share one account.
- **Store:** new append-only tables `projects` and `families` (A2 adds note revisions, evaluations and reads). They are added when an older store
  is opened, as earlier columns were.
- **Interfaces:** `/api/projects…`, `qrs project`, `qrs family`.

### 4. Methods

Each method declares:
- its inputs and typed parameters
- its outputs, and for each table or figure whether it holds **rows** of its inputs or only
  **aggregates**, for rights (P11)
- a **return definition**, for price inputs: percentage changes, or price changes per contract,
  which an adjusted continuous series needs (P9)

Only the methods the corn programme and the brief need are planned:
- **Descriptive** (WS3):
  - **summary:** count, missing, mean, standard deviation, skew, kurtosis, quantiles
  - **coverage:** sessions present and missing; for observations, publication lag and revisions
  - **distribution:** a histogram
  - **rolling mean and volatility,** through a generic `qrs.rolling_std` operator on
    `window_stats`, which the regime example also uses
  - **seasonal profile:** monthly returns per year, grouped by calendar month
  - **event study:** returns around an observations dataset's publication times
  - **feature against forward return:** a scatter plot and the rank correlation
- **Tests and intervals** (WS5):
  - **mean and regression tests:** the mean of a return series, and predictive regression of
    forward returns on features, with Newey–West (HAC) errors. The lag is
    `max(floor(4·(n/100)^(2/9)), h−1)` for horizon h. Weekly features may instead be sampled at
    their publication sessions, so that horizons do not overlap.
  - **rank correlation (IC):** with a stationary-bootstrap interval.
  - **stationary bootstrap** (Politis–Romano), for any mean-like statistic, and paired differences
    between two runs on their common window:
    - block length: Politis–White with the Patton–Politis–White (2009) correction
    - 2,000 replicates (at most 10,000)
    - the seed recorded
    - No bootstrap interval for maximum drawdown, whose bootstrap is unreliable; it is reported as
      a point value.
  - **return metrics:** annualised return, volatility and Sharpe, with bootstrap intervals.
  - **a joint test of several means** (for example all calendar months), using a Wald statistic
    with HAC covariance. This needs a χ² distribution, from SciPy (A1).
  - **multiple-testing adjustment** within a family: Holm, Benjamini–Hochberg, or
    Benjamini–Yekutieli where dependence may be negative (P5).
- **Selection bias** (WS6):
  - **The probabilistic Sharpe ratio** and **deflated Sharpe ratio:**
    - They use skewness and kurtosis.
    - They assume serially independent returns: a warning is shown when lag-1 autocorrelation is
      significant, beside the bootstrap interval.
    - N is every trial of every experiment on the same holdout window. Experiments whose axes vary
      only backtest costs are left out, since they select no strategy.
    - They apply to the selection the experiment actually made, by its objective. That objective is
      never Sharpe (`contracts.Objective`), and the report says so.
  - **White's Reality Check** over the trials that succeeded:
    - on their common window (`comparison.common_window`)
    - against a zero-return benchmark
    - sharing one resampled index set across trials

    Failed trials are counted in N and listed, but have no returns to resample.
- **Robustness** (WS7):
  - **parameter sensitivity:** an experiment's objective over one or two axes, shown as a line or a
    heatmap, with the best trial's neighbours shown beside it.
  - **sub-period results:** by calendar year or equal splits. Only additive quantities (P&L, counts)
    are reconciled with the whole; ratios are not.
  - **regimes:** returns conditional on a regime label at the previous close.
    - The label is a recorded signal or an observations column, computed causally by operators.
    - For example, volatility above its expanding median, through a generic
      `qrs.expanding_rank` operator.
    - A regime with fewer than 30 independent observations is warned.
  - **cost sensitivity:** an experiment over `commission_per_contract` and `slippage_ticks`,
    which experiments can already vary. No new mechanism.

Each method reports:
- n
- the effective n after alignment and, for overlapping horizons, after allowing for the overlap
- its assumptions
- warnings

### 5. Figures

- **One renderer,** `qrs.figures`, in Python with no dependency.
- **What it writes:** deterministic SVG of line, bar, scatter, heatmap and estimate-with-interval
  charts:
  - fixed font names
  - rounded coordinates
  - escaped text
  - no timestamps
- **In the Studio:** the SVGs are shown as images, so nothing in them can run. The Studio keeps its
  equity `LineChart`.
- **No figure contract:** a figure is an output of its method, not a contract.

### 6. Reports and evidence trails

- **Report** (`qrs/report`, version 1): a project, a title, and sections. Each section holds plain
  paragraphs and headings, and figures, tables and family results from analyses, referenced by ID.
- **`qrs report build`** writes two files. The report's hash covers both.
  - **`report.html`:**
    - self-contained, with SVGs inline
    - no scripts or external resources
    - all text escaped
  - **`evidence.json`,** fixed at the store sequence when the report was built:
    - every dataset referenced: ID, source, evidence class, rights
    - every run, experiment and analysis referenced, with its hashes
    - families: registration times, specification hashes, confirmatory or post hoc
    - read horizons
    - the number of analyses run, and failures
    - the runtime identity
    - each finding's weakest evidence class
- **`qrs report verify`** rebuilds the report from stored artefacts, as of the same store sequence,
  and checks every hash.
- **Deferred:** report bundles that reproduce analyses in a fresh environment (see
  [Not in Phase 5](#not-in-phase-5)).

### 7. Generic data support the corn programme needs

- **Continuous futures series (P9, amended by A3):** a bars dataset may declare its instrument **continuous**,
  with no expiry, and record the provider's roll and adjustment method.
  - Today `Instrument.expiry` is required, and data must end before it (`contracts.py:116`).
  - The field becomes optional only for continuous instruments, and is left out of the manifest's
    JSON when absent, so every existing dataset ID is unchanged.
  - The importer's checks stay:
    - positive prices
    - prices on the tick grid, so a ratio-adjusted series is refused
    - OHLC consistency
  - A continuous series is `retrospective` at best: adjusted levels embed later roll gaps.
- **Nothing else changes:** observations already carry publication times, revisions and
  estimated-time flags, so release data needs no other format change.

### 8. The corn research programme (content, not code)

- **Location:** `research/corn/`, holding:
  - declarations
  - the project, family, analysis and report documents
  - a README on obtaining each dataset, with its terms
- **No corn in the core:** a test checks that no programme term appears under `src/qrs/`. The
  terms are matched as whole words: `corn`, `wasde`, `usda`, `nass`, `cftc`, `crop`. Exchange
  calendar names such as `CBOT_Agriculture` are generic, and allowed.
- **Data (P10):**
  - **CBOT corn:** a continuous daily series of front-month or most-active contracts, supplied by
    the user under their data licence. Not redistributable.
  - **USDA NASS Crop Progress:** weekly condition and planting progress.
  - **USDA WASDE:** monthly US corn supply and use, first releases and revisions.
  - **CFTC Commitments of Traders:**
    - The disaggregated report, with managed-money positions, starts in 2006.
    - Positions are as of Tuesday, released Friday.
  - **Release times** come from the agencies' archives of actual release dates, not their regular
    schedules. Government shutdowns (2013, 2018–19, 2025) delayed releases, and a schedule would
    place them too early. These datasets are `retrospective`.
  - **A WASDE text-tone extraction** through the fixture provider, as in Phase 4B's macro template.
    It is `exploratory` by rule.
- **Fixtures:** synthetic, seeded series with the same shapes, calendars and release patterns,
  including a delayed release. No licensed data is committed.
- **Hypotheses:** two families, registered before the confirmation start is read.
  - **Family A,** market behaviour (Holm):
    1. Mean monthly returns differ across calendar months (a joint HAC test on monthly returns per
       year).
    2. Absolute returns are larger on WASDE release sessions (event study).
    3. The weekly change in crop condition predicts next-week returns (predictive regression,
       sampled weekly).
    4. Returns after extreme managed-money positioning differ from others (regime-conditional).
  - **Family B,** strategy (one member), trading an individual contract (A3):
    5. The top-ranked trial of a trend-following experiment keeps a positive return on the
       confirmation window, with its deflated Sharpe ratio and Reality Check reported.
- **The report** is built and verified offline from the fixtures in the local gate, and from real
  data on the user's machine.

## Architecture assessment

**Reused unchanged:**
- **Contracts:** `StrategyDocument`, the dataset manifests, `Extraction`, `Experiment`, the
  evidence classes.
- **Data and runs:** `datasets.verified` and loaders, `session_closes`, `runtime.Run` (cutoffs),
  `simple_returns`, `window_stats`, `analytics.metrics`, `comparison.common_window`.
- **Experiments:** holdout windows and repeat counting.
- **Store and jobs:** the store's patterns (append-only records, columns added on open) and the
  worker.
- **Reproducibility:** `hashing`, `runtime_identity`, the causality harness, `scripts/local-gate.sh`.
- **Studio:** `Dialog`, the API client pattern, the generated contracts.

**Extended:**
- **Engine:**
  - `as_of_values`, vectorised
  - `Instrument.expiry`, optional for continuous series
  - three generic operators: `qrs.record`, `qrs.rolling_std`, `qrs.expanding_rank`
- **Jobs:** `JobManager`, with a second job kind.
- **Registry:** methods beside operators.
- **Store:** append-only tables for projects, families and the read history (A2).
- **Schema:** new kinds `qrs/analysis`, `qrs/project`, `qrs/family` and `qrs/report`.
- **Studio:** `ParamsForm`, for method parameters (lists of columns as well as scalars).

**Not changed:**
- the strategy document schema
- run records and result hashes of runs without record nodes
- dataset IDs
- strategy bundles
- every historical hash

**Statistical validity:**
- **Dependence:** returns are autocorrelated, and overlapping horizons inflate significance. HAC
  errors and block bootstraps are the defaults. Naive i.i.d. tests are not offered, and the one
  i.i.d. assumption kept (the probabilistic and deflated Sharpe ratios) is stated and warned.
- **Small samples:** seasonal tests use monthly returns per year, not days, so their sample sizes
  are honest. With 20–40 years there are 20–40 observations per month: expect wide intervals and
  inconclusive results.
- **Coverage:** finite-sample coverage of HAC and bootstrap intervals is below nominal under strong
  dependence. On AR(1) data (φ = 0.5, n = 250–1,000) the review measured 89–91% for nominal 95%.
  The tests check that:
  - coverage is at least 88% on that process at n = 1,000
  - coverage is higher than the naive i.i.d. interval's
  - The numbers are recorded rather than promised.
- **Correctness:** every method is checked against hand calculations and published worked
  examples.

**Multiple testing and selection bias:**
- frozen families, with derived conclusions
- withdrawn and failed members counted
- read horizons deciding confirmatory status
- N carried into the deflated Sharpe ratio and the Reality Check
- one decisive confirmation window
- every analysis run counted and disclosed

**Point-in-time:**
- one alignment implementation, and `until` on every input
- effective cutoffs across runs
- records made inside runs
- regime labels lagged one session
- release times from actual archives
- leakage probes: rewriting every row after the cutoff, or published after a close, leaves every
  method's result unchanged
- AI-derived inputs stay `exploratory`

**Reproducibility (P13):**
- **Integrity:** analyses and reports are content-addressed, and `verify` detects any changed
  artefact.
- **Reproduction:**
  - Rerunning an analysis on the same machine gives the same hash.
  - On another machine its numbers agree within 1e-9 relative, the policy runs follow. numpy's
    vectorised sums and transcendental functions can differ in the last bits between x86_64 CPUs
    and platforms.
  - Sums that decide a test's outcome use exactly rounded summation (`math.fsum`), as the ridge
    model does.

**Performance:** budgets on the local gate's machine, refused before queuing when exceeded:
- `as_of_values`, one series on 12,615 closes × 1,200 rows: under 0.2 s
- descriptive methods on 50 years of daily data × 20 series: under 10 s
- a 2,000-replicate bootstrap on 12,500 sessions: under 15 s
- the Reality Check over 200 trials × 2,500 sessions × 1,000 replicates: under 60 s

**Data licensing (P11):**
- **The strict rule by default:** an analysis output is redistributable only if every input is.
  Rows and aggregates are labelled per output.
- **CME data** is never committed, bundled or exported.
- **USDA and CFTC data** taken from the agencies themselves are US public domain; the README cites
  each source and its terms.

## Exit gate

1. **Families:**
   - A family is frozen at registration.
   - Withdrawals and failed evaluations count as p = 1.
   - Conclusions follow from the adjusted p-values.
   - Specifications can name an experiment's top trial before it exists.
2. **Confirmatory status (A2):** confirmatory registration is refused, and confirmatory evaluation
   fails closed, when a dataset in scope had its test period read, or a read is unknown. Registration
   and read history cannot be updated or deleted. This is tested on histories:
   - a full run beforehand
   - a holdout evaluation beforehand
   - an analysis read beforehand
   - observations only
   - a Phase 4 store, including one with a deleted run directory
   - a read of the test period after registration
3. **Analysis reproducibility:**
   - A rerun gives the same `result_sha256` on the same machine.
   - Numbers agree within 1e-9 relative from the local gate's environment to a clean checkout.
   - On macOS this is outstanding until a Mac or runner is available.
4. **Point-in-time:**
   - Leakage probes leave every method's result unchanged.
   - `qrs.rolling_std` and `qrs.expanding_rank` pass the causality harness.
   - `qrs.record` saves exactly its input, cut by `until`.
5. **Statistical correctness:**
   - Every method matches hand calculations and published worked examples: Newey–West, the
     stationary bootstrap and its block length, the probabilistic and deflated Sharpe ratios,
     Holm, Benjamini–Hochberg and –Yekutieli, the Wald test, and the Reality Check.
   - Coverage meets the bounds above.
6. **Selection bias:**
   - N counts every trial on the window, cost sweeps excepted.
   - The Reality Check uses the common window and lists failed trials.
   - Best-of-N always shows N.
7. **Evidence:** every finding, figure and report shows the weakest evidence class of its inputs.
   AI-derived inputs make findings `exploratory`, and post hoc families are labelled.
8. **Robustness:**
   - Sensitivity surfaces equal their trials' metrics.
   - Additive sub-period quantities reconcile with the whole.
   - Regime labels are lagged and causal, and small regimes are warned.
9. **Integration and compatibility:**
   - Analyses read bars, observations, AI-derived features, runs (equity, fills, models, records)
     and experiments.
   - The strategy schema, the run hashes of strategies without records, dataset IDs and historical
     hashes are unchanged.
   - Phase 4 stores open.
   - All existing tests pass.
   - SciPy is never imported by strategy execution and is not in `runtime.lock` (A1).
10. **Reports:**
    - Deterministic HTML and evidence.
    - `verify` detects any changed artefact and is stable as the store grows.
    - Every number traces to an output hash.
    - Text is escaped.
11. **Rights:** non-redistributable rows never leave the machine in an export, and outputs are
    labelled rows or aggregates.
12. **Figures:** byte-identical SVG for the same numbers, rendered only in Python, and shown as
    images.
13. **Performance:** the budgets above are met, and larger requests are refused before queuing.
14. **Corn programme:**
    - The whole workflow runs offline on the fixtures in the local gate: import, project, family,
      analyses, experiment, evaluation, report and verify.
    - The no-programme-terms test passes.
    - The continuous series carries complete roll, adjustment and contract provenance, and a
      backtest on it is refused (A3).
15. **Offline and safety:** everything runs with no network, no generated code, no live trading
    and no autonomous execution. The research views pass the browser tests offline.

Hosted CI on Ubuntu and macOS runs every workstream when runners return. Until then each
workstream's evidence is the local gate, and macOS is recorded as outstanding.

## Workstreams

Each workstream is one pull request, independently reviewed, with the local gate run before it is
proposed for merging.

### 1. `qrs.record` and vectorised as-of alignment
- **Depends on:** nothing.
- **Scope:** the operator (beside a backtest), its saving and hashing, and the vectorised
  `as_of_values`.
- **Acceptance:**
  - The saved table equals its input and is cut by `until`.
  - Runs without records keep their hashes.
  - `as_of_values` gives identical outputs on every test, template and historical hash, and meets
    its budget.

### 2. Analysis runs
- **Depends on:** 1.
- **Scope:**
  - the `qrs/analysis` contract
  - the method registry
  - input resolution through `runtime.Run`, with effective cutoffs
  - the analysis job kind in `JobManager` and the worker, its directory, record and hashes
  - `qrs analyse`, and the API
  - one method, `summary`
- **Acceptance:**
  - A rerun gives the same hash.
  - The leakage probes pass.
  - Run outputs are never read past their cutoffs.
  - Failed and cancelled analyses are recorded.
  - Runs are unaffected.

### 3. Descriptive methods and figures
- **Depends on:** 2.
- **Scope:** the descriptive methods, `qrs.rolling_std` and the SVG renderer.
- **Acceptance:**
  - Hand-calculated values.
  - The operator passes the harness.
  - Byte-identical SVG, with escaped text.
  - The descriptive budget is met.

### 4. Projects, families and read horizons
- **Depends on:** 2.
- **Scope:**
  - the `qrs/project` and `qrs/family` contracts and tables
  - registration, withdrawal and symbolic references
  - the read history, fail-closed confirmatory registration and evaluation (A2), and the cap on
    analyses' `until`
  - the API and CLI
- **Acceptance:** exit criteria 1 and 2, on hand-built histories, and older stores open.

### 5. Tests, intervals and adjustment
- **Depends on:** 3 (figures), 4 (families).
- **Scope:**
  - the HAC tests and regression, the Wald test, the stationary bootstrap, return metrics with
    intervals, the rank correlation
  - Holm, Benjamini–Hochberg and –Yekutieli
  - evaluating a family and deriving conclusions
- **Acceptance:** exit criterion 5 for these methods, and conclusions derived on worked families.

### 6. Selection bias
- **Depends on:** 5.
- **Scope:** the probabilistic and deflated Sharpe ratios and the Reality Check over experiments,
  with N as defined.
- **Acceptance:** exit criterion 6, and published worked examples reproduced.

### 7. Robustness, sensitivity and regimes
- **Depends on:** 5.
- **Scope:** sensitivity from experiments, sub-periods, regime-conditional returns, and
  `qrs.expanding_rank`.
- **Acceptance:** exit criterion 8, and the operator passes the harness.

### 8. Reports and evidence trails
- **Depends on:** 4–7.
- **Scope:** the `qrs/report` contract, `qrs report build` and `verify`, `evidence.json` fixed at
  a store sequence, and rights labelling.
- **Acceptance:** exit criteria 10 and 11.

### 9. Studio research views
- **Depends on:** 8.
- **Scope:**
  - **Projects:** a list, with their notes and read horizons.
  - **Families:** registration behind a confirmation step that shows the horizons.
  - **Analyses:** created through a generalised `ParamsForm`, and viewed with their tables and SVG
    figures.
  - **Reports:** viewed in a sandboxed frame.
- **Acceptance:** Vitest for the view logic, and browser tests offline: create a project, register
  a family, run an analysis, view a report.

### 10. Continuous series and the corn programme
- **Depends on:** 8; the declarations and fixtures can be prepared beside 5–7.
- **Scope:** continuous instruments with their provenance (P9, A3), `research/corn/`, synthetic fixtures, the README, and the
  end-to-end test.
- **Acceptance:**
  - Exit criterion 14.
  - Existing dataset IDs are unchanged.
  - Any capability found missing is added to the core as a generic method with its own tests, never
    as programme code.

### 11. Exit-gate verification
- **Depends on:** 1–10.
- **Scope:**
  - the record, `docs/stage-5-verification.md`
  - the local gate extended with `report verify` on the programme's fixtures
  - a mutation pass over the new rules
  - an independent full-stack review

## Order

1 → 2 → 3 and 4 (independent) → 5 → 6 and 7 (independent) → 8 → 9 and 10 (independent) → 11.

## Risks

- **Statistics can still be misread.** The system enforces registration, adjustment, N and evidence
  classes, and reports state assumptions and warnings. It cannot make conclusions right.
- **Forking paths:** analyses before the confirmation start are unlimited by design. They are
  counted and disclosed, not prevented.
- **Strict looks:** counting every full run as a read means most data already backtested in full
  cannot support a confirmatory family. A project must set its confirmation start before such runs,
  or use data that arrives later.
- **Small samples** in commodity research: expect inconclusive results. That is the honest outcome.
- **Continuous series:** results depend on the provider's roll method. Contract coverage is
  checked on import (A3); the roll rule itself is recorded but not checked.
- **Family B's sample** is one contract's life, so its confirmation window is short and its result
  likely inconclusive (A3).
- **Release times:** archive dates can still be wrong by hours. Rows are marked as estimates where
  times are uncertain.
- **Trusted local store:** the rules prevent mistakes, not deliberate edits of `.qrs`.
- **Hosted CI:** unavailable until 1 December. Each workstream relies on the local gate, and macOS
  stays outstanding.
- **Scope:** methods could sprawl. Each workstream adds only what is listed here.

## Decisions for approval

| # | Decision | Recommendation | Alternatives |
|---|---|---|---|
| P1 | Statistics library | **Amended (A1):** SciPy, in the optional `research` extra, imported only by research modules; its version is recorded | numpy and the standard library with a hand-written χ² (the original recommendation) |
| P2 | How analyses run | Queued on the existing worker, as a second job kind; `qrs analyse` in-process | In-process in the service: no time limit, cancellation or crash isolation |
| P3 | Derived series | `qrs.record` beside a backtest | Record-only strategies: reverses decision 3, and touches validation, the warm-up gate and the runtime |
| P4 | What counts as a look | Every read: runs, trials, holdout evaluations and analyses, through read horizons. **Amended (A2):** an append-only read history, and confirmatory work fails closed | Only analyses and holdout evaluations (weaker; full backtests escape) |
| P5 | Families and adjustment | Registered as frozen units. Holm by default; Benjamini–Hochberg for screens; Benjamini–Yekutieli where dependence may be negative. Withdrawn or failed members count as p = 1 | Per-hypothesis registration; no default adjustment |
| P6 | Selection-bias tests | The deflated Sharpe ratio and White's Reality Check | The deflated Sharpe ratio only; Hansen's SPA |
| P7 | Figures | A deterministic SVG renderer in Python, shown as images | matplotlib (its SVG varies with fonts and versions); a JavaScript chart library (a second renderer) |
| P8 | Report format | Self-contained HTML and `evidence.json` | Markdown with a renderer dependency; PDF; notebooks |
| P9 | Continuous series and returns | Continuous instruments with no expiry, difference-adjusted (on the tick grid), `retrospective`. Methods take a return definition: price changes per contract for adjusted series. **Amended (A3):** complete roll, adjustment and contract provenance, and never executable | Unadjusted front-month bars, with roll gaps in returns; ratio adjustment (refused by the tick check); building rolls in QRS |
| P10 | Corn data sources | CBOT bars from the user's licensed source. USDA NASS and WASDE, and CFTC COT, from the agencies, with archive release dates. Synthetic fixtures in the repository | Add weather or satellite data (more value, more licensing and volume) |
| P11 | Rights of outputs | Strict: an output is redistributable only if every input is | Aggregates shareable when labelled (vendor licences often forbid derived data) |
| P12 | Notebooks | Not supported; reports are generated from documents | A Jupyter integration (flexible, not reproducible by construction) |
| P13 | Hash policy for analyses | The runs' policy: identical hashes on the same machine, numbers within 1e-9 relative elsewhere; `math.fsum` for sums that decide outcomes | Hash rounded numbers (rounding boundaries still flip); exact arithmetic everywhere (slow) |
| P14 | Confirmation start | Set per project, and the cap applied to every analysis but evaluations | Per family (more flexible, easier to game) |

## Not in Phase 5

- **Trading:** live trading, brokers and order routing; autonomous or scheduled execution.
- **Code:** generated or untrusted code; notebooks; analysis code other than trusted extension
  modules, as for operators.
- **Data:**
  - fetching from the network
  - building continuous contracts or roll schedules
  - intraday data, or several traded assets in one backtest
  - weather and satellite data
- **Methods:**
  - walk-forward re-optimisation of strategies
  - Bayesian methods
  - Hansen's SPA and Romano–Wolf
  - autocorrelation plots, Q–Q plots and correlation matrices, which no planned hypothesis uses
  - machine-learning libraries beyond the existing ridge
- **Reports:**
  - bundles that reproduce analyses in a fresh environment
  - caching analyses by document hash
  - a public figure contract
- **Typed contracts** for the existing comparison and experiment summaries, unless a report needs
  one.
- **Product:** desktop packaging and the per-launch API token (Phase 6); collaboration services.

## Implementation plan

Eleven pull requests in the order above. Each is reviewed independently and passes the local gate,
and none is merged without your approval.

| # | Workstream | New or changed modules | Size |
|---|---|---|---|
| 1 | `qrs.record`, faster as-of | `operators/`, `runtime.py`, `operators/data.py` | Small |
| 2 | Analysis runs | `analysis.py`, `methods/`, `jobs.py`, `worker.py`, API, CLI | Medium |
| 3 | Descriptive methods, figures | `methods/describe.py`, `figures.py`, one operator | Medium |
| 4 | Projects, families, read horizons | `projects.py`, `store.py`, contracts, API, CLI | Medium |
| 5 | Tests, intervals, adjustment | `methods/inference.py`, `stats.py` | Large |
| 6 | Selection bias | `methods/selection.py` | Medium |
| 7 | Robustness and regimes | `methods/robustness.py`, one operator | Medium |
| 8 | Reports | `reports.py`, CLI | Medium |
| 9 | Studio research views | projects, families, analyses and reports views | Medium |
| 10 | Continuous series, corn programme | contracts, `importer.py`, `research/corn/`, fixtures | Medium |
| 11 | Exit-gate verification | record, gate, review | Small |

**Approved** on 2 November 2026, with amendments A1–A3.
