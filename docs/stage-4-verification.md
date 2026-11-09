# Stage 4 verification

Recorded 1 November 2026 on Linux x86_64 with Python 3.12.3, NautilusTrader 1.230.0, pandas 3.1.0,
NumPy 2.5.4, pandas_market_calendars 5.5.0, Node 22 and Chromium (Playwright 1.63.0). It covers the
Phase 4A exit gate of the [Stage 4 plan](stage-4-plan.md). Workstreams 1–3 are merged (PRs #12–#14);
workstreams 4–6 are stacked pull requests (#15–#17), and this record is stacked on workstream 6.
Every result below was observed by running the command shown, not inferred.

The code verified is the stack after its independent review: workstream 4 at `4c62f19`,
workstream 5 at `0e4195f` and workstream 6 at `426a8a1`, with this record's pull request on top.
The review's findings and fixes are under [Review](#review). Checks marked *(new)* were added by
this verification or by the review, each in the pull request of the workstream that owns what it
checks. The suite with them passed locally, offline, and in CI on Ubuntu and macOS 15 (see
[Checks](#checks)).

## Exit gate

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | **Custom operator:** an extension operator with a `table` output appears in the palette, validates, compiles, runs and exports with no editor or compiler change | Met | See [Custom operator](#custom-operator) |
| 2 | **Leakage probes fail closed** | Met | See [Leakage](#leakage) |
| 3 | **As-of joins** | Met | See [As-of joins](#as-of-joins) |
| 4 | **Trials persist**, including failures, cancellations and time-outs; an experiment over 200 trials is refused before queuing | Met | See [Experiments and the holdout](#experiments-and-the-holdout) |
| 5 | **Holdout isolated** | Met | See [Experiments and the holdout](#experiments-and-the-holdout) |
| 6 | **Reproducibility and provenance** | Met on Linux; fills exact and metrics within tolerance on macOS 15 | See [Reproducibility](#reproducibility) |
| 7 | **Backward compatibility:** the guarantees hold, and every existing test passes | Met | See [Compatibility](#compatibility) |
| 8 | **Execution limits:** a run past its time limit is terminated and recorded as failed, and the queue moves on; cancelling an experiment stops its running trial and cancels the queued ones | Met | `test_a_run_past_its_time_limit_is_stopped_and_the_next_run_starts` (failed with that reason, keeping its log file; the queued run then succeeds), `test_a_worker_that_ignores_termination_is_killed_at_its_time_limit`, `test_a_timed_out_trial_is_kept_as_failed_and_the_next_trial_runs`, `test_cancelling_an_experiment_stops_its_running_trial_and_cancels_the_queued_ones` |
| 9 | **Offline:** no network access or LLM key is needed | Met, locally and in CI | See [Offline](#offline) |

### Custom operator

- **Service and export:** `test_extension_operator_needs_no_editor_or_compiler_change` loads the example extension (`examples/extension/qrs_roc.py`) by `QRS_OPERATOR_MODULES`. It then checks:
  - `ext.candle` is listed with its `table` output in the `features` family
  - a strategy merging its table with a built-in `qrs.merge` compiles without issues, runs, and records the operator's module
  - the exported bundle includes the module and reproduces the run's result hash
  - `compiler.py`, `graph.py` and the studio's sources never mention the operator
- **Browser:** `test_a_custom_table_operator_appears_in_the_palette_then_runs_and_exports` drags `ext.candle@1` from the palette's `features` group, connects it, sees "Valid.", runs it and exports a bundle that reproduces the run's result hash.
- **CLI and a fresh virtualenv:** CI's export job runs the EMA crossover beside the extension's candle table with `qrs run`, then its exported bundle, installed only from its hashed lock, and checks the two results match.

### Leakage

- **Train-only preprocessing:** `test_outliers_after_a_cutoff_leave_its_model_and_earlier_predictions_unchanged` replaces, for every fitted model, the features after its cutoff and the targets not yet known at it with 1e9. Every model fitted by then, and every prediction up to it, is unchanged, while at least one later model changes.
- **Label purging:** `test_rewriting_prices_after_each_cutoff_leaves_the_ridge_templates_models_and_decisions_unchanged` rewrites the ridge template's prices after each of its 7 cutoffs. The models fitted by then, and the predictions, signals and positions up to it, are unchanged. `test_the_model_port_refuses_training_rows_whose_targets_are_not_known_by_the_cutoff` refuses a model trained on a row whose target is unknown at its cutoff.
- **Causality harness:** `test_outputs_never_depend_on_later_sessions` and `test_outputs_become_valid_exactly_after_the_declared_warm_up` cover all 26 registered operators, including `qrs.fit_ridge`, `qrs.predict`, `qrs.as_of`, the feature and label operators and the extension's. For as-of joins, the harness also rewrites every observation published after the cut. Its samples are made to show leaks *(new)*:
  - the sample models have non-zero coefficients, and the sample table lacks a feature every 7th session from 120, so a prediction reading later features, or imputing from later rows, changes;
  - as-of joins are also cut between a sample value's release and its revision.
- **Leaky operators are caught:** `test_the_checks_catch_leaky_or_misdeclared_operators_of_each_new_port_type` runs the harness on twelve deliberately faulty test operators, and the checks fail for each:
  - a table that peeks a session ahead
  - a table and a label with understated warm-ups, and a label declaring a shorter horizon than it looks
  - a label that fills the targets it cannot know
  - a fit that learns from targets not yet known, and one whose first cutoff comes before enough targets are known
  - a prediction that uses each new model a session before its cutoff; one that reads the next session's features *(new)*; one that fills a missing feature with its mean over every session *(new)*
  - an as-of join that reads values published up to an hour after each close; one that uses each event's final revision from its first release *(new)*

  The three *(new)* operators passed the harness before its samples were changed.

### As-of joins

- **Nothing before publication:** `test_as_of_uses_what_was_published_by_each_close` joins a fixture that includes an early close. A value published exactly at a close counts for that session; one published an hour after an early close does not; and a UTC offset decides which side of a close a timestamp falls. Closes follow the exchange calendar across daylight saving (`test_closes_follow_the_exchange_calendar_across_daylight_saving`).
- **Revisions:** in the same test, a revision changes only the sessions from its publication. A revision of an older event, published after a newer event, never replaces it (`test_a_later_revision_of_an_older_event_never_replaces_a_newer_event` *(new)*). `test_observations_changed_after_publication_are_refused` refuses a dataset whose stored rows changed.
- **Maximum age:** `test_freshness_is_measured_from_the_publication_of_the_revision_used` checks that a value exactly the maximum age old is used. A second older, it is missing, and the older event is not carried forward. The fixture test also checks stale sessions at two days' freshness.
- **Run cutoffs:** `test_a_run_cutoff_reads_no_observation_published_after_the_last_close_before_it`.
- **Unknown closes:** a join onto bars with a row on a day their calendar has no session is refused before running, not when it runs (`test_a_join_onto_bars_with_a_day_their_calendar_has_no_session_is_refused_before_running` *(new)*).
- **Timestamps:** `test_imports_refuse_missing_or_naive_publication_times_whatever_the_evidence_class`.
- **Evidence class:**
  - `test_estimated_timestamps_cannot_be_point_in_time_and_are_kept_with_how_they_were_made` refuses estimated timestamps declared as point-in-time.
  - `test_a_run_is_as_weak_as_the_weakest_dataset_it_read` shows a run that read retrospective data as retrospective.
  - The studio shows the class the API returns: in a run's results, the compare view and each ranked trial. The browser tests check it in a run's results and in the ranking *(new assertions)*, for synthetic data only.

### Experiments and the holdout

These tests are in `tests/test_experiments.py`, except the studio's, which is in `tests/test_studio.py`.

- **Trials persist:**
  - `test_a_timed_out_trial_is_kept_as_failed_and_the_next_trial_runs`
  - `test_cancelling_an_experiment_stops_its_running_trial_and_cancels_the_queued_ones`, where cancelled trials keep that status
  - `test_an_experiment_cancelled_while_its_trials_are_being_queued_cancels_every_one` *(new)*
  - `test_a_finished_experiment_stays_finished_and_its_holdout_is_evaluated_only_through_it` *(new)*: a cancel after the last trial finished changes nothing
- **Validated first:** `test_every_trials_parameters_are_validated_before_any_is_queued` refuses an experiment with an invalid trial before anything is queued. Trials that would be the same strategy, such as periods of 10 and 10.0, are refused too *(new)*.
- **Bounded:** `test_experiments_over_200_trials_are_refused_before_anything_is_queued`:
  - A grid of 201 points is refused, as are a random search of 201 trials and more random trials than the space has points.
  - Each refusal is a 422 with its reason, and no run or experiment is created.
  - A grid of exactly 200 points is within the limit.
  - A random search over a space too large to draw from is refused, not a server error *(new)*.
- **Holdout prices:** `test_rewriting_holdout_prices_changes_no_trials_result` rewrites every price from the holdout start, and every trial keeps its result hash.
- **Holdout window:** `test_the_holdout_is_evaluated_on_request_over_its_window_only_and_repeats_are_flagged` checks:
  - the holdout window starts at the holdout start and counts only holdout sessions
  - its net P&L and starting equity are measured from the run's own equity at the last close before the holdout *(new)*
  - every evaluation is recorded, and the second is flagged as a repeat; so is an evaluation of a different trial (`test_a_holdout_evaluation_of_another_trial_is_still_a_repeat`)
- **Holdout baseline, against an independent ledger** *(new)*: `test_the_holdout_carries_in_its_position_and_counts_its_first_sessions_fills_and_costs` starts the holdout on a session when the trial sells the contract it held at the close before. It recomputes the run's position and equity from the bars and fills by cash flows (`conftest.cash_flow_ledger`, shared with `test_equity_reconciles_with_independent_cash_flow_accounting`) and checks every holdout figure against it: sessions, baseline, net P&L, commission, fills, maximum drawdown from the baseline, exposure, round trips from the position carried in, and the final position. `test_a_slice_measured_from_the_close_before_it_counts_its_first_sessions_move_and_fills` checks the same rules through `comparison.from_close_before` on a ledger worked by hand, where the first close is below the baseline. Whole runs keep their drawdowns, measured from their first close: `test_a_whole_runs_drawdown_is_measured_from_its_first_close` *(new)* uses a balance the simulator reads back a hair low.
- **Only through the experiment, once the trials are done:**
  - the holdout of a timed-out trial is refused, as is a trial the experiment lacks *(new)*;
  - a re-run of a holdout run is refused, since it would evaluate the holdout without recording it *(new)*;
  - the service refuses the holdout while any trial is queued or running, and records nothing; once every trial has finished it is evaluated (`test_the_holdout_is_refused_until_every_trial_has_finished` *(new)*). The studio offers it on the same condition.
- **No comparison across cutoffs** *(new)*: `test_a_trial_and_a_full_run_are_not_compared_over_one_window` shows that a trial and a full run of its strategy are refused by run comparison, whose window would otherwise include the holdout in the full run's metrics.
- **Ranking:** `test_trials_rank_by_the_objective_in_its_direction_and_experiments_list_by_strategy`, and `test_trials_with_the_same_objective_rank_in_trial_order` *(new)*.
- **Trials reproduce:** `test_a_grid_runs_each_point_as_a_trial_cut_off_at_the_holdout_and_ranks_them` shows that `qrs run --until` with a trial's frozen strategy and cutoff reproduces its result hash. `test_the_same_seed_reproduces_the_same_trials_and_result_hashes`.
- **In the studio:** `test_experiments_are_ranked_compared_cancelled_and_their_holdout_evaluated_once` creates an experiment, sees its trials ranked as the service ranks them, and compares two. It then evaluates the holdout once after a confirmation, sees it recorded, and cancels a second experiment. Holding requests open as a slow service would, it also checks *(new)*:
  - the holdout cannot be confirmed again while the first request is pending;
  - Back is disabled while an experiment is being created, which could otherwise switch the view to it with another experiment's confirmation still open;
  - after a cancel, no trial is left shown as running.

### Reproducibility

- **The ML template, three ways:** `test_app_cli_and_export_produce_identical_results[ridge_forecast-fixture]` runs it through the service's API, which the studio calls, then the CLI, then the exported bundle. All three give identical code hashes, runtime identity, dataset hashes, outputs (the model file included) and result hashes, and the template's expected fills. CI's export job runs its bundle in a fresh virtualenv from its hashed lock and matches the CLI. Locally, the same script gave:

  | Template | Fills | Net P&L | `code_sha256` | `result_sha256` (CLI = bundle) |
  | --- | --- | --- | --- | --- |
  | Ridge return forecast | 8 (4 round trips) | 1,767.50 | `bfd0909d…` | `721ab23d…` |

  The example extension's strategy (the EMA crossover beside the custom candle table) matched too, with result `277a3eab…`: the EMA crossover's own, since the table feeds no trade.

  In the browser, `test_the_ridge_template_is_in_the_gallery_and_runs` creates the template from the gallery, runs it and exports it from the studio. The bundle reproduces the studio run's code hash, result hash and outputs *(new assertion)*.

- **Models output and hashed:** `test_a_run_records_every_fitted_model_with_its_training_provenance` checks that `models/fit.json` is hashed in the run record. Each model carries its cutoff, training rows, horizon, features, means, scales, coefficients and intercept, which equal those fitted. `test_a_run_cutoff_keeps_exactly_the_models_fitted_before_it`.
- **Across platforms:**
  - `test_every_template_reproduces_its_expected_fills_and_metrics_on_its_fixture[ridge_forecast]` checks the ridge template's fills exactly and its metrics within the tolerance adopted in Stage 3, `|a − b| ≤ max(1e-9 × max(|a|, |b|), 1e-6)`. It passed on Ubuntu and on macOS 15 (Apple silicon) in every CI run listed under [Checks](#checks).
  - The ridge fit avoids BLAS: `test_a_model_runs_result_hash_does_not_depend_on_the_machines_blas` runs it under two other OpenBLAS kernels with the same result hash.
  - `test_every_ridge_prediction_is_at_least_1e_6_from_the_threshold` keeps a last-bit difference from flipping a trade.
- **Seeded experiments:** `test_the_same_seed_reproduces_the_same_trials_and_result_hashes`.

### Compatibility

- **Every existing test passes** (see [Checks](#checks)).
- **Hashes:** the Stage 2 demo keeps dataset `sha256:91a9b520…`, code `d8fad9fa…` and result `24357159…`, and its record has no `until`. Each Stage 3 template keeps its code and result hashes. No test pins these. They come from `qrs demo` followed by `qrs run` of the EMA crossover on its dataset, and from `qrs run` of each template on its fixture, compared with the values recorded in Stage 3:

  | Template | `code_sha256` | `result_sha256` |
  | --- | --- | --- |
  | Bollinger/RSI | `fff3fd9a…` | `9debb8e1…` |
  | EMA crossover | `2e0a0d84…` | `277a3eab…` |
  | SMA crossover | `c280fe54…` | `336b2b53…` |

- **Stores:** a store with the Stage 2 runs table opens with its runs unchanged, reading no predecessor, experiment or trial (`test_a_store_from_before_experiments_opens_with_its_runs_unchanged`).
- **Dataset IDs:** observation datasets are a new manifest kind, so bar dataset IDs are unchanged, as the demo's shows.
- **Operator versions:** every operator ID registered at the end of Stage 3 is still registered; Stage 4 only added operators, and all are at version 1.
- **New fields only when used:** the demo's record has no `until`. The Stage 3 templates' records list no model outputs, or their result hashes, which cover the outputs, would have changed. Runs in an older store read no experiment or trial. New runs outside an experiment are not separately tested for this.

### Offline

- **No network or LLM code:** nothing in `src/qrs` or the example extension imports an HTTP client or reads an API key, and the studio loads no external resources.
- **Locally, as root:** inside `unshare --net` with only loopback up, a connection to an outside address failed with "Network is unreachable". On this record's tree:
  - `pytest`: 391 passed
  - `pytest -m e2e`: 12 passed
  - `qrs demo`, then `qrs run` and `qrs export` of the EMA crossover on it, with the bundle run from the checkout's qrs: both reproduced dataset `91a9b520…`, code `d8fad9fa…` and result `24357159…`
  - `qrs run` of the ridge template on its fixture gave code `bfd0909d…` and result `721ab23d…`
- **CI:** the e2e job runs every browser test inside `unshare --net` as the runner user. They exercise the service, workers, imports, experiments, holdout evaluations and exported bundles. All 12 passed on every head listed under [Checks](#checks).

## Mutation checks

Each mutation below was applied to the final code, then the tests for its workstream were run and the change was reverted. A mutation counts as caught when a test fails. All 78 were caught, and so were 3 mutations of the studio caught by its browser test. Rows marked *(new)* were added for the review's fixes, or first survived and were then given a test.

**Contracts, run limits and cutoffs** (workstream 1), **features and labels** (workstream 2):

| Mutation | Caught by |
| --- | --- |
| The time limit never enforced | `test_a_run_past_its_time_limit_is_stopped_and_the_next_run_starts` |
| A timed-out run not failed with its reason | the same |
| A run cutoff keeps the cutoff's own session | `test_a_run_reads_no_bar_on_or_after_its_cutoff` |
| A label's horizon not added to its consumer's warm-up | `test_a_fit_nodes_effective_warm_up_is_its_first_cutoff` |
| An operator taking a label may output a series | `test_an_operator_that_takes_a_label_may_output_only_models` |
| A label may feed any input | `test_a_label_cannot_feed_a_series_input` |
| The model port accepts targets unknown at the cutoff | `test_the_model_port_refuses_training_rows_whose_targets_are_not_known_by_the_cutoff` |
| Past returns a session short | `test_outputs_become_valid_exactly_after_the_declared_warm_up[qrs.returns@1]` |
| A forward return a session early | `test_outputs_become_valid_exactly_after_the_declared_warm_up[qrs.forward_return@1]` |
| Merge overwrites an existing column | `test_merge_refuses_a_name_already_in_the_table_and_names_must_be_lower_case_identifiers` |

**Experiments** (workstream 5; `tests/test_experiments.py`):

| Mutation | Caught by |
| --- | --- |
| Trials not cut off at the holdout | `test_a_grid_runs_each_point_as_a_trial_cut_off_at_the_holdout_and_ranks_them` |
| The worker ignores a run's frozen cutoff | the same |
| The grid limit doubled | `test_experiments_over_200_trials_are_refused_before_anything_is_queued` |
| The random-search limit doubled | the same |
| Trials not compiled before queuing | `test_every_trials_parameters_are_validated_before_any_is_queued` |
| The seed ignored | `test_the_same_seed_reproduces_the_same_trials_and_result_hashes` |
| Holdout metrics over the whole run | `test_the_holdout_is_evaluated_on_request_over_its_window_only_and_repeats_are_flagged` |
| The holdout run cut off at the holdout too | the same |
| A holdout evaluation not recorded | the same |
| A repeat never flagged | the same |
| Only an evaluation of the same trial flagged as a repeat | `test_a_holdout_evaluation_of_another_trial_is_still_a_repeat` |
| Cancelling leaves the queued trials | `test_cancelling_an_experiment_stops_its_running_trial_and_cancels_the_queued_ones` |
| Cancelling leaves the running trial | the same |
| Ranking ignores the direction | `test_trials_rank_by_the_objective_in_its_direction_and_experiments_list_by_strategy` |
| Ties ranked latest trial first | `test_trials_with_the_same_objective_rank_in_trial_order` *(new)* |
| Dataset axes accepted | `test_searches_trials_could_not_share_the_holdout_split_of_are_refused` |
| Strategies with two backtests accepted | the same |
| Trials that are the same strategy (10 and 10.0) accepted *(new)* | the same |
| The holdout of a trial that did not succeed evaluated | `test_a_timed_out_trial_is_kept_as_failed_and_the_next_trial_runs` *(new assertion)* |
| A re-run drops its run's cutoff | `test_re_running_a_trial_keeps_its_parameters_and_cutoff` |
| The holdout measured from its first close, as before the review *(new)* | `test_the_holdout_carries_in_its_position_and_counts_its_first_sessions_fills_and_costs` |
| The holdout's first-session fills left out *(new)* | the same |
| Drawdowns not measured from the baseline *(new)* | `tests/test_comparison.py::test_a_slice_measured_from_the_close_before_it_counts_its_first_sessions_move_and_fills` |
| Whole runs' drawdowns measured from `initial_cash` *(new)* | `tests/test_simulation.py::test_a_whole_runs_drawdown_is_measured_from_its_first_close` |
| Round trips counted from the first session's closing position *(new)* | the same |
| A cancel during queuing not caught up *(new)* | `test_an_experiment_cancelled_while_its_trials_are_being_queued_cancels_every_one` |
| A late cancel relabels a finished experiment *(new)* | `test_a_finished_experiment_stays_finished_and_its_holdout_is_evaluated_only_through_it` |
| Holdout runs re-run *(new)* | the same |
| Runs compared across cutoffs *(new)* | `test_a_trial_and_a_full_run_are_not_compared_over_one_window` |
| A random space too large to draw from accepted *(new)* | `test_experiments_over_200_trials_are_refused_before_anything_is_queued` |
| The holdout evaluated while trials are unfinished *(new)* | `test_the_holdout_is_refused_until_every_trial_has_finished` |
| Older stores not given the experiment columns | `test_a_grid_runs_each_point_as_a_trial_cut_off_at_the_holdout_and_ranks_them` |

**The studio's experiment logic** (workstream 6; `apps/studio/src/experiment.test.ts`):

| Mutation | Caught by |
| --- | --- |
| Dataset references offered for a search | "offers every node parameter but dataset references" |
| Only numeric parameters offered | the same |
| Fractions accepted for an integer parameter | "types listed values as their parameter, and says why it cannot" |
| `true`/`false` kept as strings | the same |
| Empty items kept | the same |
| A grid counted as a sum, not a product | "counts a grid as the product of its value counts, and a random search as its trials" |
| A random search sent without its trials and seed | "builds the request a grid or a random search needs" |
| A grid sent with trials and a seed | the same |
| The holdout offered while trials run | "is offered only once the ranking can no longer change" |
| The chosen holdout trial ignored | the same |
| Following an experiment stops with its status, not its runs *(new)* | "goes on while any trial or holdout run is queued or running, whatever the experiment says" |
| A missing holdout start accepted | "says what an incomplete form lacks, and leaves the rest to the service" |

**The studio's experiment dialog** (workstream 6; `tests/test_studio.py::test_experiments_are_ranked_compared_cancelled_and_their_holdout_evaluated_once`, rebuilt for each):

| Mutation | Caught by the check that |
| --- | --- |
| The holdout offered again while a request is pending *(new)* | the "Evaluate holdout…" button is disabled while the first request is held |
| Back enabled while an experiment is being created *(new)* | Back is disabled while the create request is held |
| Following stops with the experiment's status *(new)* | no trial is left queued or running after a cancel |

**Walk-forward ridge** (workstream 3; `tests/test_models.py`, `tests/test_templates.py`, `tests/test_operator_contracts.py`):

| Mutation | Caught by |
| --- | --- |
| No purge: training rows up to the cutoff | `test_a_prediction_worked_by_hand` |
| One training row whose target is unknown at the cutoff | the same |
| Sample instead of population deviation | the same |
| No guard for constant features | `test_a_constant_feature_gets_no_weight_even_when_its_value_is_inexact_in_binary` |
| The ridge penalty ignored | `test_a_prediction_worked_by_hand` |
| Intercept zero | the same |
| A rolling window keeps one row more | `test_walk_forward_ridge_matches_an_exact_rational_calculation[rolling]` |
| A refit a session early | `test_walk_forward_ridge_matches_an_exact_rational_calculation[expanding]` |
| A model used a session before its cutoff | `test_a_prediction_worked_by_hand` |
| Missing features imputed as zero, not the training mean | `test_walk_forward_ridge_matches_an_exact_rational_calculation[expanding]` |
| The fit's warm-up a session late | `test_the_ridge_template_signals_nothing_before_its_first_model` |

**As-of joins** (workstream 4; `tests/test_observations.py`, `tests/test_operator_contracts.py`):

| Mutation | Caught by |
| --- | --- |
| A value published exactly at the close excluded | `test_as_of_uses_what_was_published_by_each_close` |
| An older event's later revision replaces a newer event | `test_a_later_revision_of_an_older_event_never_replaces_a_newer_event` *(new)* |
| The maximum age exclusive | `test_freshness_is_measured_from_the_publication_of_the_revision_used` |
| No maximum age | `test_as_of_uses_what_was_published_by_each_close` |
| The run cutoff ignored for observations | `test_a_run_cutoff_reads_no_observation_published_after_the_last_close_before_it` |
| Early closes ignored | `test_as_of_uses_what_was_published_by_each_close` |
| Naive timestamps read as UTC | `test_imports_refuse_missing_or_naive_publication_times_whatever_the_evidence_class` |
| Estimated timestamps allowed as point-in-time | `test_estimated_timestamps_cannot_be_point_in_time_and_are_kept_with_how_they_were_made` |
| No calendar check for as-of joins | `test_joins_onto_bars_without_a_calendar_and_datasets_of_the_wrong_kind_are_refused_before_running` |
| Days the calendar has no session not checked | `test_a_join_onto_bars_with_a_day_their_calendar_has_no_session_is_refused_before_running` *(new)* |
| Observations counted as sessions | `test_a_run_cutoff_reads_no_observation_published_after_the_last_close_before_it` |
| No dataset kind check | `test_joins_onto_bars_without_a_calendar_and_datasets_of_the_wrong_kind_are_refused_before_running` |
| A run takes its strongest evidence class | `test_a_run_is_as_weak_as_the_weakest_dataset_it_read` |

The as-of mutation survived because a review fix in workstream 4 made the fixture's revision of the newer week the later publication. With that change, picking the latest publication and picking the latest event's revision gave the same values. It was caught before that fix.

## Review

An independent review of the whole stack, in four parts, read the code and tests afresh and
probed them: temporal integrity, the experiment backend, studio concurrency, and architecture. It
found no leak in the built-in data path. Its findings, each fixed with a regression test that
fails without the fix:

| Severity | Finding | Fixed in |
| --- | --- | --- |
| Critical | Run comparison measured a trial (cut off at the holdout) and a full run over one window, so the full run's metrics included the holdout, unflagged | #16 |
| Critical | In the studio, an experiment created after the user had gone back to another switched the view with the other's state; an open confirmation then evaluated the new experiment's holdout while it ran | #17 |
| Important | Cancelling while trials were still being queued left the later ones to run under a cancelled experiment | #16 |
| Important | Re-running a holdout run evaluated the holdout again without recording it | #16 |
| Important | An as-of join on bars with a day their calendar has no session passed the data check and failed when run, so an experiment would queue every trial to fail | #15 |
| Important | The causality harness could not show a prediction reading later features or imputing from later rows (zero sample coefficients, no missing features), nor an as-of join reading a revision early (no cut between a release and its revision) | #15, #18 |
| Important | After a cancel the studio stopped following runs, leaving the stopping trial shown as running | #17 |
| Important | The holdout could be confirmed again while the first request was pending, recording a repeat | #17 |
| Important | The service evaluated the holdout while trials still ran; only the studio held it back | #16, #17 |
| Important (final review) | Measuring drawdowns from the starting equity, added for the holdout, also changed whole runs whose balance the simulator reads back a hair low (485,000 reads as 484,999.99999999994), and so their result hashes | #16 |
| Minor (final review) | A poll answered before a holdout request was recorded could hide the new evaluation until the experiment was reopened | #17 |
| Minor | A late cancel relabelled a finished experiment; values read alike (10 and 10.0) ran as separate trials; a huge random space failed with a server error; a missing trial was reported as "not succeeded"; an older poll could overwrite a newer answer; a list error never cleared; trial settings that differ were not flagged in the ranking; the confirmation did not take focus; one objective had no label; the cutoff file was read in two places; three statements in the decisions were stale | #15–#17 |

## Checks

On this record's tree, the whole stack after the review:

| Check | Result |
| --- | --- |
| `uv run pytest` | 391 passed, 12 deselected (the browser tests) |
| `uv run pytest -m e2e` | 12 passed |
| `npm test` / `npm run typecheck` / `npm run build` | 29 passed / clean / clean |
| `ruff check`, `ruff format --check` | Clean |
| Schema and generated types, `uv lock --check`, `runtime.lock` against a fresh export | No drift |
| Stage 2 demo and Stage 3 templates | Hashes unchanged (see [Compatibility](#compatibility)) |
| CI, all five jobs (Ubuntu and macOS 15, studio, export, offline browser tests), on each pull request's head | Workstream 4 `4c62f19`: [41332981697](https://github.com/JMBorrill/quant-research-studio/actions/runs/41332981697), [41332985404](https://github.com/JMBorrill/quant-research-studio/actions/runs/41332985404). Workstreams 5 (`0e4195f`) and 6 (`426a8a1`), and this record: see their pull requests (#16–#18) |

## Open items

- **Decided in review:** a holdout evaluation's metrics are measured from the last close before the holdout, carrying in the position held then and counting the first holdout session's fills and costs. This supersedes the plan's "as run comparison computes a window" (`docs/decisions.md` §7).
- **Known limitations, not changed:**
  - A holdout evaluation's run is queued and then recorded, in two steps. If recording failed (SQLite busy past its 5-second timeout), the run would exist unrecorded; until it is recorded, it could be re-run.
  - Cancelling an experiment while `qrs experiment` is still queuing its trials, in another process, can rarely leave a trial failed rather than cancelled.
  - Each poll of a running experiment rebuilds its summary from every succeeded trial's ledger: about 1–1.5 s at 200 trials, measured, which keeps one service core busy while it runs.
  - `qrs.predict` checks that a model's feature names match the table's, not how the features were made: a model fitted on 5-session returns named "change" accepts 1-session returns of that name.
  - An as-of join declares no warm-up, but values older than its maximum age leave gaps, so a model fed by one can start later than the declared warm-up; the ranking's common window may then begin before such a trial can trade. This is not leakage.
- **Not verified:**
  - Windows
  - Intel Macs and macOS before 15, which NautilusTrader 1.230.0 does not support
  - your own Mac
  - result hashes across platforms: the gate asks for exact fills and metrics within tolerance, and only those were compared on macOS
- **Risks carried forward** from the plan:
  - Trusted modules run unsandboxed.
  - `qrs run` has no time limit.
  - A long experiment occupies the single run queue.
  - The harness checks only registered operators.
  - The best of many trials is optimistic: the trial count is shown, and the holdout is the check.
- **The Stage 3 one-off browser-test failure** (`test_undo_and_redo_from_the_keyboard_save_each_step`) has not recurred in the Stage 4 CI runs checked; its cause remains unknown.
- **What the data shows:** fixture and synthetic data verify the mechanics only, not profitability.
