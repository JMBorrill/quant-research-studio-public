# Stage 4 plan: Phase 4A, Python and ML

**Status:** approved; nothing here is implemented. Starts from `main` at `d775d6f`,
where Stage 3 is verified ([stage-3-verification.md](stage-3-verification.md)).

**Basis:**
- **The Addendum's Phase 4A scope:**
  - trusted Python modules
  - as-of joins
  - fit/predict and chronological evaluation
  - bounded grid/random optimisation
- **Its exit criteria:**
  - a custom operator is added without editor or compiler changes
  - train-only preprocessing and label purging pass leakage probes
  - every trial persists
  - the final holdout stays separate
- **The extensions the Stage 3 plan reserved for Phase 4A,** both additive: new port types, and new
  temporal alignments (as-of joins and a fit scope).

## Adopted decisions

1. **Scope:** Phase 4A only. Model training is in scope for the first time. The exclusions are listed
   under [Not in Stage 4](#not-in-stage-4).
2. **Everything new is an operator.** Features, labels, fit, predict and joins register on the
   existing `run(context, inputs, params)` contract. The strategy document stays `qrs/strategy`
   version 1, and the editor, validator and compiler stay operator-agnostic.
3. **Three built-in port types,** each with a runtime check:
   - `table`: named float64 feature columns on the session index, NaN during warm-up.
   - `label`: a training target on the session index, plus its forward horizon *h* in sessions.
     Registration refuses an operator that takes a label input unless all its outputs are `model`s.
     A target can therefore reach only a fit operator, never a rule, a position policy or a backtest.
   - `model`: the walk-forward sequence of fitted models. Each model holds its cutoff, training
     rows, label horizon, feature names, preprocessing statistics and coefficients.

   Predictions are ordinary `series`. Port types stay a built-in table, with no registration API
   for extensions.
4. **Model:** ridge regression in NumPy, solved in closed form in float64. No machine-learning
   dependency is added.
5. **Walk-forward fit and predict operators:**
   - **Cutoffs:** a model with cutoff *c* is fitted at the close of session *c*. It uses the rows *t*
     whose features are valid and whose label is known by then (*t* + *h* ≤ *c*). The first cutoff is
     the first session with at least the minimum number of training rows. After that, fit retrains
     every *k* sessions, on an expanding or rolling window.
   - **Predictions:** at each session, predict uses the latest model whose cutoff is at or before
     that session.
   - **Warm-up:** a label output declares its horizon *h* as its warm-up. A label input's warm-up is
     added to the longest of the node's other inputs, not compared with them. A model's warm-up is
     therefore the features' warm-up, plus *h*, plus the minimum training rows minus one. For a label
     with no warm-up of its own, such as `qrs.forward_return`, that is exactly its first cutoff. A
     label with a warm-up of its own makes the warm-up later than the first cutoff, never earlier.
     Predict adds nothing. Validation, the data check and the ranking window all use this.

   There is no experiment-level fold runner and no graph cycle.
6. **Information-availability cutoffs on every read:**
   - **Run cutoff:** a run may carry an `until` date, which `qrs run --until` also accepts. The run
     context is the only route to data, and it returns no bar on or after `until`. It also returns no
     observation published after the close of the last session before `until`. The cutoff is
     recorded in the run record only when set, so existing records are unchanged.
   - **Chronological training:** a model trains only on the rows its cutoff allows (decision 5). This
     purge comes from the label's own horizon, never from a separate parameter.
   - **Train-only preprocessing:** imputation and scaling are fitted on each model's training rows only.
   - **Predictions:** a prediction at a session uses only models fitted by that session's close.
   - **Observations:** joins use only observations published by the session's close (decision 9).
7. **Isolated holdout:**
   - An experiment's trials run with `until` set to its holdout start, so they cannot read holdout rows.
   - **Holdout evaluation:** an explicit, recorded action that runs the selected trial on the full
     data. Its metrics cover only the sessions from the holdout start, computed from its own ledger
     as run comparison computes a window.
   - **Repeats:** any later holdout evaluation in the same experiment, of whichever trial, is a
     repeat. It is allowed, but recorded and flagged.
8. **Bounded, sequential experiments:**
   - At most 200 trials per experiment. A grid with more than 200 points, or a random search asking
     for more than 200 trials, is refused before anything is queued.
   - Trials run one at a time in the existing run queue.
   - Random search is seeded, and the seed is recorded.
9. **As-of availability:**
   - **Timestamps:** every observation row needs a publication timestamp with an explicit UTC offset
     (ISO 8601). Missing or naive timestamps are refused, with their row numbers, whatever the
     dataset's evidence class. No publication lag is inferred.
   - **Decision time:** an observation is usable for the decision at a session's close if it was
     published at or before that close. The close comes from the exchange calendar declared for the
     traded bars, so bars without one, the synthetic demo's included, cannot be joined.
   - **Value used:** at each session, the observation with the latest event date published by the
     session's close, in its latest revision published by then.
   - **Maximum age:** measured from the publication of the revision used to the session's close. A
     value published longer ago than the maximum age is missing, never carried forward.
   - **Estimated timestamps:** a reconstructed timestamp is marked as an estimate on its row, and
     the dataset records how the estimates were made. Both stay in the dataset, and so in its ID.
10. **Evidence classes:**
    - **Retrospective data is never point-in-time evidence.** A dataset with any estimated timestamp
      is `retrospective`, and an import declaring it `point_in_time` is refused. `point_in_time`
      requires every timestamp to be the one recorded at publication.
    - **The class cannot change in place:** it is part of the manifest, and so of the dataset ID.
      Changing it in place fails the dataset's checksum, as any manifest edit does.
    - **A run's class is the weakest among the datasets it read** (`synthetic`, then
      `retrospective`, then `point_in_time`). Results, comparisons and experiment trials show it, so
      a run that read retrospective data is never shown as point-in-time.
11. **Run limits:**
    - **Time limit:** every service run has a time limit (default 30 minutes, set on `qrs serve`).
      When a run exceeds it, the service:
      - terminates the run's worker process
      - records the run as failed, with the limit as its reason and its log kept (no new run status)
      - releases the queue, so the next queued run starts
    - **Timed-out trials:** a trial that times out is recorded as failed, and its experiment carries
      on with the next trial.
    - **Cancellation:** it works for single runs and for whole experiments.
    - **`qrs run`:** it runs in-process, with no limit.
12. **Trusted code only:**
    - Extension operators are single-file local modules that import only the standard library and
      packages in the runtime lock.
    - They run with the user's privileges, in the service when it loads them and in workers. Neither
      is a sandbox.
    - There is no code editing in the studio, and no untrusted or generated code.
13. **Tolerance:** Stage 3 decision 6 still applies. Trades must match exactly; metrics must match
    exactly on one platform and within its tolerance across platforms.

## Compatibility guarantees

- **Existing artefacts behave as before:** strategies, datasets, runs and stores open unchanged. The
  Stage 2 demo keeps dataset `sha256:91a9b520…`, code `d8fad9fa…` and result `24357159…`. Every
  Stage 3 template keeps its fills, its recorded metrics, and the code and result hashes in
  [stage-3-verification.md](stage-3-verification.md).
- **New fields appear only when used:** the run cutoff, experiment links and model outputs. The store
  gains tables when it is opened, as it gained the `predecessor` column in Stage 3.
- **Dataset IDs:** observation datasets are a new manifest kind, so existing IDs do not change.
- **Operator versions:** old ones stay registered.

## Exit gate

1. **Custom operator:** an extension operator with a `table` output appears in the palette,
   validates, compiles, runs and exports with no editor or compiler change.
2. **Leakage probes fail closed:**
   - Outliers injected after a cutoff leave that cutoff's model unchanged (train-only preprocessing).
   - Rewriting prices after a cutoff leaves that cutoff's model unchanged (label purging).
   - Every fit, predict and as-of operator passes the causality harness.
   - A deliberately leaky test operator is caught.
3. **As-of joins:**
   - Nothing is used before it is published.
   - A later revision never changes an earlier session's value.
   - A value published longer ago than the maximum age is missing, never carried forward.
   - An import with a missing or naive timestamp is refused.
   - A dataset with estimated timestamps cannot be imported as point-in-time, and a run that read
     retrospective data is never shown as point-in-time.
4. **Trials persist:** every trial is kept with its status, including failures, cancellations and
   time-outs. An experiment over 200 trials is refused before queuing.
5. **Holdout isolated:**
   - Rewriting holdout prices changes no trial's result hash.
   - Holdout metrics cover only the holdout window.
   - Every holdout evaluation is recorded, and repeats are flagged.
6. **Reproducibility and provenance:**
   - The ML template gives identical results from the studio, the CLI and an export.
   - Its models are output and hashed in the run record.
   - On Ubuntu and macOS, trades match exactly and metrics stay within tolerance.
   - Re-running an experiment with the same seed reproduces its trials and their result hashes.
7. **Backward compatibility:** the guarantees above hold, and every existing test passes.
8. **Execution limits:**
   - A service run past its time limit has its worker terminated, is recorded as failed with that
     reason and its log, and the next queued run starts.
   - Cancelling an experiment stops its running trial and cancels the queued ones.
9. **Offline:** no network access or LLM key is needed, as CI's offline browser tests check.

## Workstreams

Each workstream ships as its own pull request with the listed tests. CI must stay green on Ubuntu
and macOS 15.

### 1. Contracts and run limits (first)

- The `table`, `label` and `model` port types and their runtime checks.
- **Alignment:** `as_of` is registered for operators that read an observations dataset through the
  run context by publication time. Their ports keep the session-index check. The harness probes
  them by rewriting observations published after a session, as well as later bars.
- **Label rules:** registration refuses an operator with a label input unless all its outputs are
  models, and a label connects only to a label input.
- **Warm-up:** label outputs declare their horizon, and a label input's warm-up is added to the
  other inputs' (decision 5).
- **Run cutoff:** the run context's `until`, applied to every dataset read and recorded when set,
  and the `qrs run --until` option.
- **Causality harness:** samples for the new port types, and a rule for labels: a label's value at a
  session may depend only on that session and the next *h*.
- **Time limit:** the worker's time limit on service runs, with the `qrs serve` option.

**Acceptance:**
- All existing tests pass, and the Stage 2 and 3 hashes are unchanged.
- A label wired to a `series` input is refused by validation and by the editor. The test uses a test
  operator with a label output.
- Registering an operator that takes a label and outputs anything but a model is refused.
- A test fit node's effective warm-up equals its first cutoff, worked by hand.
- A run with `until` reads no row on or after it, and its record states the cutoff. A run without it
  writes no cutoff field, and its result hash is unchanged.
- The harness covers every port type and catches a deliberately leaky test operator.
- A run past its time limit has its worker terminated and is recorded as failed, with the limit as
  its reason and its log kept. The next queued run then starts. A run under the limit is unaffected.

### 2. Features and labels

- `qrs.returns`: bars to a series of the past *n*-session return.
- `qrs.table`: series to a one-column table.
- `qrs.merge`: table plus series to a wider table. Chain merges for more columns.
- `qrs.forward_return`: bars to a label of the next *h*-session return, horizon *h*.
- The example extension gains a custom operator with a `table` output.

**Acceptance:**
- A hand-calculated fixture for each operator, checked exactly, and each passes the harness.
- The custom table operator appears in the palette, compiles, exports, and runs in a fresh
  virtualenv with no editor or compiler change (exit gate 1).

### 3. Walk-forward fit and predict

- **`qrs.fit_ridge`:** features and label to a model.
  - Parameters: minimum training sessions, retrain interval, expanding or rolling window, and ridge
    alpha.
  - Purging uses the label's horizon (decision 5).
  - Mean imputation and standardisation are fitted on training rows only.
- **`qrs.predict`:** model and features to a series.
- **Model provenance:** every model becomes a run output, hashed in the record. It holds the cutoff,
  training rows, horizon, feature names, means, scales and coefficients.
- **An ML template:** returns, RSI and the MACD histogram feed ridge, a threshold, long/flat and the
  backtest. The threshold is a fixed predicted return, set above the fixture's round-trip costs and
  stated in the template's conditions. The template ships with a fixture CSV and expected fills, as
  the Stage 3 templates do.

**Acceptance:**
- A closed-form ridge fixture matches an exact rational calculation within decision 6's tolerance,
  and every model in it is checked against an independent NumPy calculation.
- **Leakage probes:**
  - outliers after a cutoff leave that cutoff's model unchanged
  - rewriting prices after a cutoff leaves it unchanged
  - fit and predict pass the harness
- The template's fills match on its fixture, with app, CLI and export parity. Its metrics are
  recorded and checked across platforms, as Stage 3's are.
- Every fixture prediction lies at least 1e-6 from the threshold, so trades cannot differ across
  platforms by a rounding difference.

### 4. Observations and as-of joins

- **A `qrs/observations` manifest kind:**
  - event date
  - publication timestamp
  - value columns with units
  - an optional revision label
  - a per-row flag marking an estimated publication timestamp, and how the estimates were made
  - evidence class, source, and whether the data may be redistributed
- **CSV import** through `qrs import` and the upload endpoint, reusing the importer's row-numbered
  rejections.
- **`qrs.as_of`:** the traded bars to a series on their sessions, using the value decision 9 defines.
  - Parameters: the observations dataset, the column, and the maximum age in days since publication.
  - The dataset is a dataset reference, like `qrs.dataset`'s. Runs freeze it, and exports include it
    when it may be redistributed. It does not count towards the sessions the data check counts.
- The exchange calendar's library version is recorded with the run's runtime.
- **A run's evidence class,** the weakest among its datasets, is shown with its results and in
  comparisons.

**Acceptance:**
- **On a fixture with late releases, revisions and an early-close session:**
  - nothing is used before its publication
  - an observation published exactly at the close is used at that close
  - a later revision never changes an earlier session's value
  - a revision of an older event, published after a newer event's first release, does not replace
    the newer event's value
  - values published longer ago than the maximum age are missing
- **Run cutoff:** a run with `until` reads no observation published after the close of the last
  session before it.
- **Refused imports:**
  - naive timestamps, and missing ones, are refused with their row numbers, whatever the declared
    evidence class
  - an import declared `point_in_time` with any estimated timestamp is refused
- **Estimates kept:** an imported dataset with estimated timestamps is `retrospective`, and keeps each
  row's estimate flag and how the estimates were made.
- **Run class:** a run that read a retrospective dataset is shown as retrospective in its results and
  comparisons, never as point-in-time.
- **Refused strategies:** a join onto bars with no exchange calendar is refused before running.
- The operator passes the harness, and existing dataset IDs are unchanged.

### 5. Experiments

- **A `qrs/experiment` document:**
  - the base strategy revision
  - a grid, or a seeded random space, over node parameters
  - an objective metric and its direction
  - the holdout start
- **Every trial's parameters** are validated by the operators' models before anything is queued.
- **Trials as runs:** each trial is an ordinary queued run that freezes its own strategy and links to
  the experiment and trial number. Its `until` is the holdout start.
- **Store and interfaces:** store tables (by migration), an API, and a `qrs experiment` CLI command.
- **Ranking:** over the common window, reusing the run comparison's window and mismatch logic.
- **Holdout evaluation:** an explicit, recorded run of the selected trial on the full data, with
  metrics over the holdout window only (decision 7).
- **Cancellation:** cancelling an experiment stops its running trial and cancels the queued ones.

**Acceptance:**
- Every trial persists with its status, including failed, cancelled and timed-out ones. A timed-out
  trial does not stop its experiment: the next trial starts.
- A grid over 200 points, or a random search over 200 trials, is refused before queuing.
- Rewriting holdout prices changes no trial's result hash.
- Holdout metrics cover only the holdout window.
- Holdout evaluations are recorded, and a repeat is flagged.
- `qrs run --until` with a trial's strategy and cutoff reproduces its result hash.
- The same seed reproduces the same trials and the same result hashes.

### 6. Studio experiment management

- **An experiment dialog:**
  - choose the parameters and values, the search, the objective and the holdout
  - run it, and follow trials as they finish
  - rank trials, each with its evidence class, and compare the top ones in the existing compare
    dialog
  - cancel it
  - evaluate the holdout, with a confirmation
- Colours for the new port types.
- The ML template in the gallery.

**Acceptance:**
- **A Playwright test:**
  1. create an experiment from a strategy
  2. see its trials ranked
  3. cancel a second experiment
  4. evaluate the holdout once and see it recorded
- Connecting a label to a series input is refused.
- Vitest covers the dialog's own logic.

### 7. Exit-gate verification

- `docs/stage-4-verification.md`: each exit criterion and its evidence, as Stage 3 recorded.
- CI on Ubuntu and macOS, offline, and mutation checks of the new tests.

**Acceptance:** every exit criterion is met with recorded evidence, or listed as a gap with its
reason.

## Order

1. **First:** contracts and run limits (1).
2. **Then:** features and labels (2), followed by fit and predict (3). In parallel, as-of joins (4)
   and experiments (5), which need only 1.
3. **After 3, 4 and 5:** the studio (6).
4. **Last:** exit-gate verification (7).

## Risks

- **Execution safety:**
  - Trusted modules run unsandboxed with the user's privileges, in the service and in workers.
  - The time limit stops runaway service runs, but `qrs run` has none.
  - A long experiment occupies the single run queue.
- **Reproducibility:**
  - Linear algebra differs between platforms, for example Accelerate on macOS and OpenBLAS on Linux.
    Predictions can therefore differ in the last bits, and a prediction near a threshold could flip a
    trade.
  - The margin test protects the template, not user strategies.
- **Leakage:**
  - The harness checks registered operators on generated data. A module loaded only at runtime is not
    checked unless it is added to the tests.
  - A trusted module could still compute a forward-looking value and output it as a plain `series`,
    or read files without going through the run context.
- **Selection bias:** the best of up to 200 trials is optimistic. Results show the trial count beside
  the best trial, and the holdout is the safeguard.
- **Provenance:**
  - Reconstructed publication times support only a `retrospective` claim.
  - Each run's results show the weakest evidence class among the datasets it used.
  - Observation rights follow the same redistribution rule as bars in exports.
- **Calendars:** session closes come from `pandas_market_calendars`. A new calendar release could move
  a close, which is why its version is recorded with each run.
- **Time limit:** a legitimate long run is stopped at the limit, which can be raised on `qrs serve`.
- **Carried over from Stage 3:**
  - the unexplained one-off browser-test failure
  - the move of `ubuntu-latest` to Ubuntu 26, whose `sudo` the offline CI step relies on
  - macOS CI minutes, which grow with the suite

## Not in Stage 4

- **Phase 4B:**
  - LLM adapters
  - document and news datasets
  - structured extraction and response replay
  - news and macro templates
- **Corn-specific data and importers** (Phase 5).
- **Live trading** and broker connections.
- **Desktop packaging and the per-launch API token** (Phase 6).
- **Other ML libraries,** such as scikit-learn.
- **Parallel trials.**
- **Reusing a model from one run in another.**
- **Code safety:**
  - code editing in the studio
  - untrusted or generated code
  - a sandbox
- **A port-type registration API for extensions.**
- **Importing observations in the studio:** CLI and API only.
- **Still excluded, as in Stage 3:**
  - multiple assets and intraday data
  - contract rolls and historical margin
