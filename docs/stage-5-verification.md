# Stage 5 verification

Recorded on 2 November 2026 on Linux x86_64 with Python 3.12, NautilusTrader 1.230.0, pandas 3.1.0,
SciPy 1.18.1 (the optional `research` extra), Node 22 and Chromium (Playwright 1.63.0). It covers the
exit gate of the [Stage 5 plan](stage-5-plan.md), approved with amendments A1–A3 and merged in #29.
The results below come from running the tests and commands named. GitHub-hosted Actions has no
minutes until 1 December, so verification is local: `scripts/local-gate.sh`, run on each
workstream's head (see [Local gate](#local-gate)). What only hosted CI or a Mac can show is marked
outstanding.

The code verified is the stack of eleven pull requests, each reviewed independently after it was
written and fixed before it was proposed; their findings and fixes are in each pull request. The
complete stack was then audited independently against the exit gate, and its findings fixed in the
last pull request (see [Full-stack audit](#full-stack-audit)):

| Workstream | Pull request |
| --- | --- |
| 1. `qrs.record` and vectorised as-of alignment | #30 |
| 2. Analysis runs | #31 |
| 3. Descriptive methods and figures | #32 |
| 4. Projects, families and the read history | #33 |
| 5. Tests, intervals and adjustment | #34 |
| 6. Selection bias | #35 |
| 7. Robustness, sensitivity and regimes | #36 |
| 8. Reports and evidence trails | #37 |
| 9. Studio research views | #38 |
| 10. Continuous series and the corn programme | #39 |
| 11. This record, the local gate's research and mutation steps, and the audit's fixes | #40 |

**Outstanding:** hosted CI on Ubuntu and macOS 15 for every head (the account has no Actions minutes
until 1 December); macOS 15 locally (no Mac is attached); and the corn programme on real data, which
needs the user's licensed CBOT data.

## Exit gate

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | **Families** | Met | `test_projects.py`: registration stores the family's SHA-256 and the store refuses to change it; `test_a_hypothesis_names_an_experiments_top_trial_before_it_is_known_and_is_evaluated_once`; `test_inference.py::test_a_familys_conclusions_follow_its_adjusted_p_values` (withdrawn and failed members p = 1; Holm and Benjamini–Hochberg); `test_reports.py::test_a_family_with_a_failed_evaluation_is_reported_as_p_1` |
| 2 | **Confirmatory status (A2)** | Met | `test_confirmatory_registration_is_refused_once_the_test_period_was_read` on a full run, a holdout evaluation, an analysis and observations only; `test_a_store_from_before_the_read_history_is_backfilled_failing_closed` (deleted run and trial directories); `test_a_read_of_the_test_period_after_registration_compromises_a_confirmatory_family`; `test_registrations_withdrawals_evaluations_and_reads_are_never_changed_or_removed` (SQLite triggers); and the review's regressions: a failed backfill, a read landing during registration, two evaluations at once, copies of the data under another ID; and the audit's: extractions backfilled as full reads of their documents, and a family whose hypotheses are all settled not relabelled by later reads (`test_a_read_after_every_hypothesis_is_settled_leaves_the_family_as_it_concluded`) |
| 3 | **Analysis reproducibility** | Met on Linux; macOS outstanding | `test_an_analysis_reproduces_its_hash_and_records_what_it_read`. The corn programme run twice in separate stores: 11 of its 14 analyses have identical `result_sha256`; the other three name an experiment or trial, whose store-specific IDs appear in their disclosure of N, and are byte-identical once those IDs are masked. Across environments: the research step's stores from the gate's environment and from a fresh clone, each with its own synced environment, give identical numbers for all 14 analyses (largest relative difference 0) |
| 4 | **Point-in-time** | Met | Leakage probes: `test_leakage_probe_every_method_ignores_bars_and_observations_after_the_cutoff` runs each of the 17 methods reading datasets on two stores alike before the cutoff, with every later bar rewritten, later sessions missing and observations published after the last close changed, and requires the same result hash; with the cutoff removed, all 17 fail. The three methods reading experiments read trials run only to their holdout start (`test_selection.py`); `qrs.rolling_std` and `qrs.expanding_rank` pass the contract harness and the exit-gate combination test (12 indicator outputs, 48 combinations); `test_a_record_saves_exactly_its_input_cut_by_until`; one calendar across an analysis's inputs; regime labels lagged in the returns' sessions; experiments read only to their holdout start |
| 5 | **Statistical correctness** | Met, with stated limits | `tests/reference/inference.json` (statsmodels 0.15, arch 8.0): HAC, regression, Wald (χ² and F), Holm, BH, BY and block lengths to 1e-9 or better; White's Reality Check against arch's on four cases, within Monte Carlo error (0.02 at 20,000 replicates; the largest difference measured was 0.0044), since the two resample with different generators; Benjamini and Hochberg's and Bailey and López de Prado's published examples; coverage bounds (see [Statistics](#statistics)). The stationary bootstrap's resampling itself has no reference value: it is checked by its block structure, its reproducibility, its coverage and through the Reality Check. Benjamini–Yekutieli is checked against statsmodels, having no published worked example, and the probabilistic Sharpe ratio against its formula. Every method's block length is checked to follow each series it resamples |
| 6 | **Selection bias** | Met | `test_selection.py`: N over four experiments (one a cost sweep, one on another window), a failed trial counted and listed, the common window, N shown by any analysis of a trial, a holdout run, or a re-run of a re-run of a trial |
| 7 | **Evidence** | Met | Every analysis result and report finding carries the weakest evidence class of its inputs, and the report its own; `test_ai_derived_inputs_make_findings_exploratory`; post hoc and compromised families labelled in reports |
| 8 | **Robustness** | Met | `test_robustness.py`: sensitivity surface against the trials' metrics with neighbours; sub-period totals by hand; lagged causal regimes with small regimes warned |
| 9 | **Integration and compatibility** | Met | Analyses read bars, observations, AI-derived features, runs (equity, fills, fitted models and records) and experiments (`test_fills_are_read_per_session_and_models_at_each_fit`). The strategy schema is unchanged; run hashes of strategies without records, dataset IDs and the eight pinned historical hashes are unchanged (`scripts/check-hashes.py`); Phase 4 stores open (simulated by dropping the read history of a Stage 5 store, not a store written by Phase 4 code); every earlier test passes; SciPy is not in `runtime.lock` and is never imported by a strategy run in a worker, by `qrs run` or from a bundle, and the CLI and service load without it (A1) |
| 10 | **Reports** | Met | `test_reports.py`: escaped self-contained HTML, `verify` stable as the store grows and failing on any changed artefact, every number with its hash; and the gate's research step verifies the programme's report from the command line |
| 11 | **Rights** | Met | Outputs labelled rows or aggregates; outputs redistributable only if every dataset read (directly, through runs or trials) is, a report's rights through a run tested (`test_reports_through_the_cli_and_rights`); reports labelled, `--shareable` refused; strategy bundles include only redistributable data (Stage 2 decision, unchanged) |
| 12 | **Figures** | Met | A golden SVG hash reproduced in a fresh process; escaped text and dropped control characters; served only when declared, as `image/svg+xml` under a sandboxing CSP; shown as images in the studio |
| 13 | **Performance** | Met on this machine | As-of: 2 ms for 12,615 closes × 1,200 rows (budget 0.2 s); descriptive methods on 20 × 50 years in 3.2 s (10 s); 2,000 bootstrap replicates on 12,500 sessions under 15 s; the Reality Check over 200 × 2,500 × 1,000 under 60 s. Larger requests are refused before queuing: inputs over 20,000 sessions, and resampling beyond those budgets in resamples × sessions (× trials), counted on every session of the data (`test_resampling_beyond_the_plans_budget_is_refused_before_queuing`) |
| 14 | **Corn programme** | Met on fixtures; real data outstanding | `test_the_corn_programme_runs_end_to_end_offline` and the gate's research step: import, extraction, project, exploration, experiment, both families registered confirmatory and evaluated, report built and verified; the no-programme-terms test; continuous provenance complete and checked against the prices, and a backtest on it refused (A3) |
| 15 | **Offline and safety** | Met on Linux | The suite, the browser tests and the research step run in a network namespace with only loopback. No code is generated by AI or from documents: analyses run registered methods, and strategies compile from their validated JSON as they have since Stage 2. Nothing trades live, and nothing runs on its own: every job is queued by a person or a command |

## Statistics

Measured in this stage, and stated by the methods themselves:

- **Coverage of 95% intervals** on AR(1) data, φ = 0.5, n = 1,000: HAC 92.8%, stationary bootstrap
  93.5%, naive i.i.d. 73.8%. The plan's bound is 88% and better than naive.
- **The seasonal Wald test** rejected about 38%, 16% and 10% at 5% against χ² with 8, 20 and 40
  years of i.i.d. data. It is an F test on (months − 1, years − 1) degrees of freedom, which with HAC
  errors still rejects about 10–12% with 2 to 4 years per month, 8% with 5 to 10, 7% with 12 and 6%
  with 20 or more; its results say so below 20 years. (Measured in the final review with the
  method's own code; the figure stated before, 5–6.4%, did not reproduce.)
- **HAC t tests** at 5% on AR(1) data, φ = 0.3: 13% at n = 20, 10% at n = 100, 8% at n = 250 and 6%
  at n = 1,000. With overlapping horizons the regression rejects more, most with few pairs: for
  h = 21 and a persistent feature, 25% at n = 120, 19% at 250, 12% at 500, 10% at 1,000; its results
  warn whenever horizons overlap. Pairs h or more sessions apart take the plan's lag, not h − 1.
- **The Reality Check** at 5%: 6% for independent returns, 9% for AR(1) φ = 0.2 (n = 250).
- **The volatility interval** of `qrs.return_metrics` covered about 77% on GARCH(1,1) returns.

## Mutation pass

`scripts/check-mutations.py` breaks each of 36 safeguards in turn, in a copy of the committed tree,
and requires the tests guarding it to fail. On its first run 22 of 23 were killed: the vectorised
as-of join's tie-break (of two revisions published at the same moment, the earlier row stands) was
held by no test, which was then added (`test_of_two_revisions_published_at_the_same_moment_the_earlier_row_stands`).
The audit's fixes added five more. The first gate of the whole stack found one more
survivor: the seasonal test's F degrees of freedom, whose test checked the degrees it reported but
not that its p-value used them; it now does. The final release review added eight more. All 36 are killed now, and the local gate runs the pass; a
mutation whose text no longer appears stops it.

## Full-stack audit

An independent audit of the complete stack against the exit gate reran the research tests, the
studio's research tests and browser test, compared the vectorised as-of join with the original loop
on 3,000 random histories, probed ten methods for leakage and ran the corn programme twice. It
confirmed the reproducibility, mutation, hash and performance claims, and found:

| Finding | Severity | Fix |
| --- | --- | --- |
| `qrs run`, `qrs serve` and every command imported SciPy, through `qrs.stats`, so they failed without the extra and a strategy run in `qrs run` loaded it (A1) | High | `qrs.stats` imports SciPy only in the functions that use it; the A1 tests cover `qrs run` and loading the CLI and service without SciPy |
| A finished confirmatory family was relabelled compromised by any later read of the test period | High | Only reads until every hypothesis is evaluated or withdrawn count |
| Analyses could not read a run's fills or fitted models, which criterion 9 names, and this record had reworded it | Medium | `fills` and `model` inputs, in the studio too |
| A re-run of a trial dropped N | Medium | Re-runs are followed back to the trial or holdout evaluation they repeat |
| A store from before the read history did not count its extractions | Medium | The backfill counts each feature dataset's extraction as a full read of its documents |
| An evaluation's analysis could start before its holdout run when both were queued in one millisecond | Low | Runs go first among jobs queued in the same millisecond |
| The leakage probes covered one method; the Reality Check had no reference value | Evidence | A probe of every method reading datasets; arch reference values |
| Minor: duplicated frozen-folder lookups and report labels, a 500 for a trial that no longer compiles, a contradiction in decisions §12 on regimes, a misleading studio hint | Low | Fixed |

The fixes were reviewed independently in turn; that review found no material defect, and its two
gaps were closed: settling a family must not clear a read made while it was open (a test and a
mutation), and a record input naming the `session` index is refused before queuing. Because a
settled family is no longer relabelled, a report built before these fixes that showed one as
compromised only through a later read no longer verifies; rebuild it.

Left as they are, as minor: each analysis context opens its own store
connection; a report counts only analyses made in its project (others appear through the read
history); an evaluation's analysis shown as a values block carries no confirmatory label of its own
(its family's block does); and the studio's bars input defaults to percentages, which the service
refuses, with a reason, for a continuous series; and a store moved to an environment without
SciPy shows complete Benjamini–Hochberg or –Yekutieli families as an error, since their adjustment
needs it.

## Final release review

The complete stack, 7dcb178 to the last pull request, was reviewed again as one release by four
independent reviewers: statistics and selection bias; registration, the read history and reports;
point-in-time, futures provenance and compatibility; and the exit criteria with a revert of each
fix against its test. No critical defect was found. Every finding below was fixed with a regression
test that fails with the fix reverted, and eight of the safeguards joined the mutation pass. The fixes were then reviewed again
independently: that review found one departure from the plan (every continuous-fed record was
refused, though A3 allows a difference-adjusted one), corrected with its test, and minor points
corrected with theirs.

| Finding | Severity | Fix |
| --- | --- | --- |
| A finished hypothesis could be withdrawn after its binding evaluation, replacing its result with p = 1 | Medium | An evaluated hypothesis cannot be withdrawn; withdrawals take the evaluation lock |
| Lineage was one step: features of the same documents file imported twice under different names were not related | Medium | Followed to a fixed point |
| `verify` failed on a report whose text held a carriage return, reading the file as text | Low–medium | Compared as bytes |
| `INSERT OR REPLACE` through the store replaced append-only rows without meeting the triggers | Low | Recursive triggers on, so a replacement is refused |
| The seasonal test over-rejects with few years (10–12% at 5% with 2 to 4 years); its stated 5–6.4% did not reproduce | Medium | Measured sizes stated, and warned below 20 years |
| The regression forced a lag of h − 1 when sampled horizons did not overlap (24% at 5% for 36 monthly pairs) | Medium | h − 1 only where horizons overlap; warned whenever they do, with measured sizes |
| A change sampled where the feature changes could span a gap in the feature | Medium–low | Changes are taken against the previous session |
| The Reality Check gave its least p-value when any return was missing or infinite | Low | Infinite returns are missing, sessions any trial lacks are left out, and the check refuses non-finite input |
| A constant return crashed the regression | Low | No test, as other methods |
| The regression's effective n divided by h whenever pairs overlapped, reaching below 1 | Low | The sampled span over h, at most n |
| Repeat holdout looks and cost-only searches were not disclosed with N; the Reality Check did not say its p-value covers only its experiment's trials | Low | Disclosed |
| A contract boundary off the sessions, or a contract with no session, was accepted, dropping its roll from the record (A3) | Medium | Contracts start and end on sessions of the series |
| A record or fitted model fed by a spliced continuous series could be analysed, carrying roll gaps past the A3 rules | Medium | Refused before queuing; a difference-adjusted series beside a traded contract stays allowed, as A3 says |
| Observations joined on bars with no exchange calendar were refused only in the worker | Low | Refused before queuing |
| Resampling far beyond the plan's budgets was accepted (criterion 13) | Medium | Refused before queuing, on the sessions read (from `since`, before `until` or an experiment's holdout) and the trials that succeeded; a hypothesis is checked at registration as its evaluation will read, so a registered family is never refused at evaluation |
| Tests that held nothing: the registration race test (the read it injected landed after registration too); and no test for `qrs.forward_return` refusing a continuous series, the block lengths of `qrs.return_metrics` and `qrs.rank_correlation`, the seasonal months' own degrees of freedom, infinite values as missing, or rights through runs | Evidence | Each now has a test that fails with its fix reverted |

**For a decision:** a read of the same series under a different file (a refreshed download with one
more session; a contract and the continuous series built from it; observations re-imported with
another vintage) is not related by lineage, which follows identical data, identical files and the
same documents only. A confirmatory family can therefore register after its test period was read
through such a copy. Relating datasets by instrument symbol, or by overlapping rows, would close it
at the risk of refusing registrations that should pass, permanently; it needs approval.

**Known limits, unchanged:** if the process dies in the instant between an evaluation's first
test-period read and storing its binding row, nothing binds it (decisions §12); N counts trials on
exactly the same holdout window, so a search with a holdout a day later is another window; a
hypothesis with two trial inputs records one holdout run on its binding row (both are recorded as
holdout evaluations); report evidence trusts dataset lists in analysis records that `result_sha256`
does not cover (the store is trusted local state); and a report built in the millisecond a row is
stored with an earlier time could later fail `verify`. A continuous dataset declared with a roll off
its sessions no longer loads (the check runs on load); none can exist from before Stage 5. A session
on which any trial's return is missing is left out of an experiment's matrix for every trial.

## Local gate

`scripts/local-gate.sh` gains two steps: the corn programme against a real `qrs serve` and its
workers, offline, with `qrs report verify` on its report from the command line
(`scripts/check-research.sh`); and the mutation pass. Every workstream's head passed the gate
before it was proposed; each pull request records its counts. The gate found two faults the suite
alone did not: workstream 10 changed all eight pinned result hashes, because a backtest recorded
its instrument with `"continuous": null` among its assumptions (fixed there: the field is left
out, since a backtest never runs on a continuous series); and a report test that failed when the
clock crossed a millisecond (fixed in workstream 8).

## Risks

- **Statistics can still be misread.** The system enforces registration, adjustment, N, evidence
  classes and disclosure; it cannot make conclusions right.
- **Small samples:** commodity research gives few independent observations; the programme's
  calendar-month test has two denominator degrees of freedom. Expect inconclusive results.
- **Selection among confirmatory families:** registering several and choosing which to evaluate
  is not covered by a family's correction; every family is listed with its project and report.
- **Trusted local store:** the rules guard against mistakes, not deliberate edits of `.qrs`; data
  copied into a new store starts with no read history there.
- **Continuous series:** results depend on the provider's roll method, which is recorded and
  checked for consistency, not for correctness.
- **Phase 4 stores** are tested by removing a Stage 5 store's read history, not with a store
  written by Phase 4 code.
- **Release times:** archive dates can be wrong by hours; such rows should be marked estimated.
- **Hosted CI and macOS** remain outstanding until runners return.
