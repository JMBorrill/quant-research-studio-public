# Phase 4B verification

Recorded 1–2 November 2026 on Linux x86_64 with Python 3.12.3, NautilusTrader 1.230.0, pandas 3.1.0,
NumPy 2.5.4, pandas_market_calendars 5.5.0, the Anthropic SDK 2.3.0 (the optional `hosted` extra),
Node 22 and Chromium (Playwright 1.63.0). It covers the exit gate of the
[Phase 4B plan](stage-4b-plan.md), merged in #19. The results below come from running the tests
and commands named. GitHub-hosted Actions has had no minutes since 1 November and will not until
1 December, so the final verification is local: `scripts/local-gate.sh` (see [Local gate](#local-gate)).
What only hosted CI or a Mac can show is marked outstanding.

The code verified is the stack of pull requests after each workstream's review, the full-stack
audit, a final independent review and the final integration (a guard against silent Ollama prompt
truncation, and the store's checksums documented as consistency, not authenticity), each reviewed
independently. Their changes were propagated up the stack by merging each branch into the next,
so every head is a fast-forward of what was reviewed:

| Workstream | Pull request | Head |
| --- | --- | --- |
| 1. Documents datasets | #20 | `2c36e5c` |
| 2. Provider interface, response store, fixture adapter | #21 | `29ad891` |
| 3. Extraction and feature datasets | #22 | `52e975d` |
| 4. Ollama and Anthropic adapters | #23 | `9cbf366` |
| 5. Aggregate operator and hybrid templates | #24 | `f85cb7e` |
| 6. Verified and exploratory experiments, studio | #25 | `58876bb` |
| 7. This record, the local gate, and CI offline | #26 | this pull request's head |

Each workstream's review findings and fixes are in its pull request. The audit's are under
[Audit](#audit), and the final review's under [Final review](#final-review). Checks marked *(new)*
were added by this verification, the audit or the final review.

**Hosted CI and macOS:**
- **Workstreams 1–4:** every CI job passed on Ubuntu and macOS 15, on their heads before the audit.
- **From 21:21 UTC on 1 November:** the account's Actions minutes are used up until 1 December.
  Every job of every run fails within seconds with no runner assigned (runs 41351562036 and later),
  so no code from the repository runs there.
- **No Mac** is attached to this session, so macOS 15 could not be run locally either.
- **Outstanding until then:**
  - macOS 15 for the changes since workstream 4
  - the hosted offline CI step
  - hosted CI on every head after the audit

  Everything else, including offline execution as a non-root user, was run locally.

## Exit gate

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | **Replay never initialises a provider** | Met on Linux, including in a network namespace like CI's. Outstanding: macOS, and hosted CI's namespace | See [Replay](#replay) |
| 2 | **Explicit recording and bounded cost** | Met | See [Recording and cost](#recording-and-cost) |
| 3 | **Strict validation** | Met | See [Validation](#validation) |
| 4 | **Late documents, revisions and missing responses** | Met | See [Availability](#availability) |
| 5 | **Provenance and immutability** | Met for feature datasets. For store records, partly: see [Provenance](#provenance) | See [Provenance](#provenance) |
| 6 | **Evidence** | Met | See [Evidence](#evidence) |
| 7 | **Verified and exploratory analysis** | Met | See [Experiments](#experiments) |
| 8 | **Cross-experiment tracking** | Met | See [Experiments](#experiments) |
| 9 | **Security** | Met | See [Security](#security) |
| 10 | **Rights** | Met | `test_exports_include_features_and_their_responses_only_when_both_extraction_and_documents_allow` |
| 11 | **Interchangeability** | Met | `test_every_provider_gives_the_same_rows_for_the_same_answers`: one extraction through the fixture, and through Ollama and Anthropic against loopback test servers returning the same answers. The rows are identical; only provenance differs |
| 12 | **Hybrid composition** | Met | `test_market_python_ml_and_llm_nodes_combine_with_no_change_to_the_graph_schema`: the macro template (RSI and returns, ridge fit/predict on growth and tone extracted by the fixture provider, as of each close) with the example extension's candle table merged into its features. It validates, compiles, runs and exports (bundle = CLI). The strategy schema's hash is pinned, and equals `main`'s |
| 13 | **Templates** | Met on Linux; macOS outstanding | See [Templates](#templates) |
| 14 | **Holdout isolated** | Met | `test_no_holdout_period_document_reaches_a_trial` *(strengthened)*: every story published from the trials' last close on is reworded and answered the opposite way, so its document, request and response all differ. No trial's result hash changes, and the full run's does |
| 15 | **Backward compatibility** | Met | See [Compatibility](#compatibility) |
| 16 | **Offline** | Met locally, as a non-root user and through the local gate; the hosted CI step is outstanding | See [Offline](#offline) |

### Replay

- **No provider:**
  - `test_each_templates_features_replay_offline_with_no_provider_to_the_same_dataset` replays each template's extraction from its committed responses in a fresh process. Every adapter module is made unimportable, and connections and name resolution are refused. The test pins the IDs: `news_trend` `sha256:1eb32108…` and `macro_ridge` `sha256:0d401edc…`.
  - `test_a_recording_replays_to_the_same_dataset_with_no_provider_imported_or_network` does the same for a recording, then its replay: same ID.
  - The final review's stricter probe replayed both templates with sockets and name resolution refused, and with the adapters, the SDK, `httpx`, `http.client` and `urllib.request` all unimportable, through `templates.prepare` and through `qrs extract`. It gave the pinned IDs.
- **Recording again:** `test_recording_a_templates_extraction_again_gives_the_same_rows`.
- **Anthropic without the SDK:** an extraction recorded through the Anthropic adapter replays in a fresh process with the SDK blocked and no network (`test_without_the_sdk_only_recording_through_anthropic_fails_and_its_responses_still_replay`).
- **In a network namespace:** these tests ran inside one locally (see [Offline](#offline)).
- **Same ID on Ubuntu and macOS:**
  - The request hash is pinned (`test_the_request_hash_is_stable_across_processes_and_platforms`) and passed on macOS 15 in CI.
  - The templates' feature IDs are pinned too; on macOS they are outstanding.
  - The audit found nothing platform-dependent in them:
    - floats are written with `repr`
    - every timestamp carries its offset and is converted to UTC
    - replay reads no clock
    - the Parquet-based IDs of Phase 4A were already stable on macOS

### Recording and cost

| Requirement | Evidence |
| --- | --- |
| Without `--record`, no provider is called | `test_qrs_extract_replays_by_default_and_records_only_when_asked`; the `no_provider` fixture fails the tests that use it (replay with a response missing, the budget refusal, option and consent checks) if an adapter is asked for |
| Over-budget extractions refused before any call (requests and cost bound) | `test_a_recording_over_its_budget_is_refused_before_any_call`; `test_the_request_budget_counts_the_responses_already_stored` *(new)* |
| A response over its token bound stops the recording, counting against the budget | `test_a_response_reporting_more_tokens_than_its_bound_stops_the_recording_and_counts_against_the_budget`; `test_either_token_count_over_its_bound_stops_the_recording` *(new)* |
| A stopped recording publishes nothing, and running it again asks only for what is missing | `test_a_recording_stopped_by_an_error_publishes_nothing_and_resumes_where_it_stopped`, `test_stored_responses_are_reused_so_recording_again_asks_nothing` |

The budget bounds the whole extraction: stored responses at their recorded cost, plus the bound of
those still missing.

### Validation

- **Before any call:** every extraction-document refusal of decision 3 is made before any call:
  - `test_an_extraction_that_breaks_a_rule_is_refused_as_it_is_read`
  - `test_provider_rules_are_refused_before_any_call_in_replay_and_recording`
  - `test_the_documents_must_be_a_documents_dataset_unchanged_since_publication`

  Replay makes every refusal that needs no provider. The Ollama endpoint and model checks, which ask
  the local server, are made at the start of a recording, before its first request.
- **Prompts cut silently** *(new)*: Ollama cuts a prompt longer than its context window
  (`num_ctx`) and still reports a normal finish. An extraction through it must declare `num_ctx`
  at least its largest request's bound (UTF-8 bytes, 1,024 tokens of framing, and the output
  limit), or it is refused before any call, in replay as in recording
  (`test_ollama_must_be_given_a_context_window_that_holds_every_request_whole`: refused without
  it and one token short, recorded at exactly the bound). Ollama also lowers a `num_ctx` above the
  model's context length to that length, so a recording reads that length from Ollama before each
  request, and stops before any chat if `num_ctx` exceeds it or Ollama does not report it
  (`test_ollama_is_refused_a_context_window_beyond_the_models_before_any_chat`; the other Ollama
  recordings run at exactly the model's length).
- **Invalid responses:** each invalid kind is stored, gives missing values with its reason, and is
  counted in the manifest, end to end. The kinds are not JSON, out of bounds, truncated, no answer,
  wrong fields, not a number *(new)* and refused *(new)*:
  - `test_invalid_responses_leave_missing_values_with_their_reasons`
  - parser cases in `test_an_answer_is_refused_unless_it_is_exactly_the_declared_numbers` and
    `test_only_a_normal_truncated_or_refused_finish_is_understood`

### Availability

- **Usable time:** a row is usable from its document's publication plus the larger of the declared
  and measured latency, never earlier than its document's earlier versions:
  - `test_feature_rows_hold_their_values_availability_and_document`
  - `test_a_measured_latency_longer_than_the_declared_one_delays_each_row`
  - `test_a_correction_is_usable_after_the_version_it_corrects_however_slowly_that_was_answered`
  - `test_at_each_close_a_strategy_sees_only_rows_usable_by_then`
- **Shared responses:** versions that render the same request share its stored response, each usable
  from its own publication. The response may predate any of them but the first, a copy that
  deduplication drops included. Capture times are read as UTC, whatever their offset:
  - `test_a_response_shared_with_a_later_identical_version_is_not_taken_for_an_altered_store` *(new)*
  - `test_a_response_shared_with_a_version_dropped_as_a_copy_is_not_taken_for_an_altered_store` *(new)*
  - `test_capture_times_with_any_offset_are_read_as_utc` *(new)*
- **The aggregate:** it counts each document once, in its latest version usable by the close. A
  later revision never changes an earlier close, and a deduplicated copy is not counted. A latest
  version with no value counts as none. Once a document has a version kept, deduplication keeps its
  later versions:
  - `tests/test_hybrid.py`, worked by hand
  - `test_deduplication_drops_later_copies_from_other_documents_but_never_a_documents_own_revision`
  - `test_a_kept_documents_later_version_is_kept_even_when_it_copies_another` *(new)*
- **The fixtures:**
  - **News:** a story after the close, a stale story, a correction, a syndicated copy, an invalid
    and a refused answer, and a story usable exactly 3 days before a close because of its latency.
  - **Macro:** a report after the close and one within the latency before it, revisions, two
    out-of-bounds answers and a wire copy.
  - Each one decides a session in the references (`test_the_news_fixture_exercises_each_rule`,
    `test_the_macro_fixture_covers_late_copied_and_invalid_reports_with_predictions_clear_of_its_threshold`).
- **Missing responses:** replay with any response missing fails before publishing, naming the
  documents (`test_replay_without_stored_responses_fails_naming_the_documents_and_publishes_nothing`).

### Provenance

- **Feature datasets:** every one holds the items in the plan's
  [What is recorded](stage-4b-plan.md#what-is-recorded-and-where) table, so an exported feature
  dataset keeps its provenance without its documents:
  - `extraction.json`: instructions, prompt template and budget
  - `responses.jsonl` *(extended)*: each row's document version, that document's publication time
    and URL, and its stored record (request, response bytes, models, tokens, cost, capture time,
    latency, status)
  - `documents.json` *(new)*: the documents dataset's manifest, with its source, file SHA-256,
    column mapping and rights, and no text
  - in the manifest: models, latency, capture, counts, tokens, cost and rights
  - checked by `test_a_feature_dataset_carries_its_extraction_responses_and_provenance`
- **On load:** the data, the manifest and each of those files are checked against the manifest's
  hashes, and `documents.json` against the documents ID. A changed or missing file is refused
  (`test_a_changed_or_missing_provenance_file_is_refused`, now covering `documents.json`).
- **The response store:**
  - Records are written once, read-only, by linking a complete file into place. They are never
    replaced, including by a writer that stores the same request between the check and the link,
    which the test simulates (`test_a_record_is_stored_once_read_only_and_never_replaced`,
    `test_a_record_stored_by_another_writer_first_is_never_replaced`).
  - A record that does not parse, or answers another request, is refused when read
    (`test_a_corrupted_record_or_one_answering_another_request_is_refused`).
- **Limit:** the store is trusted local state (decision 11). Its checksums establish consistency,
  not authenticity:
  - **What they establish:** a record's name, its request's hash, shows which request it answers,
    and a published feature dataset's hashes show its files are unchanged.
  - **What they do not establish:** that a record came from the provider named, or was captured
    when it says. Someone with write access to `.qrs` can write a consistent record with any
    answer or capture time, and it is read as stored. A capture time decides whether rows are
    retrospective, so an edited one could lift a dataset's class. Capture times are attested only
    by the machine that recorded them.
  - Once a feature dataset is published, it holds a copy of every record it was built from.

### Evidence

- **Classes:**
  - Retrospective AI outputs on real documents are `exploratory`; on synthetic documents they stay `synthetic`.
  - Only responses captured by the time they could be used lift the class.
  - Every case is in `test_only_responses_captured_by_the_time_they_could_be_used_lift_the_exploratory_class`: synthetic, exploratory *(new)*, retrospective and point-in-time documents captured in time, and synthetic, retrospective and point-in-time ones captured late.
- **Runs:** a run reading an exploratory dataset is never shown as better than exploratory:
  - its record (`test_a_strategy_reads_features_as_of_each_close_and_shows_their_evidence_class`; `test_a_run_is_as_weak_as_the_weakest_dataset_it_read`)
  - the experiment ranking
  - run comparison *(new assertion, in `test_an_exploratory_experiment_is_labelled_and_its_holdout_kept_apart_from_verified_ones`)*

### Experiments

| Requirement | Evidence |
| --- | --- |
| A verified experiment reading exploratory features, or exploratory bars, is refused before anything is recorded or queued | `test_a_verified_experiment_reading_exploratory_data_is_refused_before_anything_is_queued`, `test_a_verified_experiment_on_exploratory_bars_is_refused_too` |
| An exploratory experiment's ranking and holdout evaluations are labelled and listed apart | `test_an_exploratory_experiment_is_labelled_and_its_holdout_kept_apart_from_verified_ones`; in the studio, `test_exploratory_features_are_labelled_refused_from_verified_experiments_and_their_holdouts_kept_apart` |
| Repeats across experiments, of either kind, with earlier counts; both kinds listed in every experiment on the window | `test_holdout_evaluations_are_tracked_across_experiments_by_their_window` |
| Older stores open with their windows filled and their repeat flags kept | `test_a_store_from_before_phase_4b_fills_each_experiments_window_and_keeps_its_repeats`. The audit also built a store with the Phase 4A code (`62c9d96`) and opened it with this stack. Windows were filled, flags kept, rankings and comparisons opened, and a new evaluation counted the earlier three |

### Security

| Requirement | Evidence |
| --- | --- |
| A sentinel credential reaches no file under `.qrs`, log, run record or export | `test_a_credential_reaches_no_file_record_log_or_export` |
| An Ollama endpoint resolving outside loopback is refused before connecting; proxies ignored; the checked address is the one connected to | `test_an_ollama_endpoint_that_is_not_loopback_is_refused_before_connecting`, `test_an_ollama_host_resolving_to_any_address_off_this_machine_is_refused`, `test_ollama_requests_ignore_proxy_settings_and_go_straight_to_loopback`, `test_ollama_connects_to_the_address_it_checked_not_the_name` |
| Hosted extraction without `hosted_processing` refused before any call | `test_hosted_extraction_needs_the_documents_consent_before_any_call` |
| No request body contains tools | `test_ollama_is_asked_for_the_schema_on_its_chat_api_with_no_tools`, `test_anthropic_is_asked_for_the_schema_with_no_tools_fallbacks_or_sampling` |
| Runs, experiments and exports import no provider module | `test_runs_experiments_and_exports_import_no_provider_module` *(strengthened)*. From a fresh process, it runs a CLI run, an export and its bundle, and through the service a worker run, an experiment's trials and a holdout evaluation. An import guard in every process must record no attempt to import an adapter or the SDK, and a worker that imports one is caught. Also `test_importing_the_provider_interface_imports_no_adapter` |
| Without the extra, only recording through `anthropic` fails | `test_without_the_sdk_only_recording_through_anthropic_fails_and_its_responses_still_replay` |

### Templates

- **Fills and metrics:** both hybrid templates reproduce their expected fills and metrics
  (`test_every_template_reproduces_its_expected_fills_and_metrics_on_its_fixture`).
- **References:** exact references derive the fills from the documents, the answers, the declared
  latency and the exchange calendar, without the importer, the extraction or the as-of operators.
  Neighbouring parameters change them.
- **Parity:** app, CLI and export agree (`test_app_cli_and_export_produce_identical_results`, and
  the CI export job, run locally).
- **Thresholds:** every macro prediction is at least 1e-6 from its threshold.
- **Missing sentiment:** it blocks entries while exits stay active.
- **On this tree:**

  | Template | Fills | `code_sha256` | `result_sha256` |
  | --- | --- | --- | --- |
  | News and technical trend | 8 | `0da0d312…` | `c0bb9d7e…` |
  | Economic report and ML | 18 | `de7e133c…` | `9ac34d3a…` |

  The code hashes moved with the audit's provenance fix, which changed the feature dataset IDs the
  strategies name. The results did not change.
- **On macOS 15:** exact fills and metrics within tolerance are outstanding.

### Compatibility

- **Existing tests:** every one passes (see [Checks](#checks)). One assertion in an existing test
  changed:
  - `tests/test_import.py::test_uploads_over_the_limit_are_refused`
  - The CSV field limit is now 10 MiB, process-wide, for long documents.
  - A 10 MB upload of one field is still refused (422), but because its header lacks the required
    columns, and its message no longer echoes the field.
- **Hashes:** recomputed on this tree with `qrs demo`, `qrs run` and `qrs export`, the bundle run from its own `run.py`. They match the Phase 4A values, and the audit recomputed them independently:

  | Strategy | Dataset | `code_sha256` | `result_sha256` |
  | --- | --- | --- | --- |
  | Stage 2 demo (EMA crossover; CLI = bundle; no `until`) | `91a9b520…` | `d8fad9fa…` | `24357159…` |
  | Bollinger/RSI | | `fff3fd9a…` | `9debb8e1…` |
  | EMA crossover | | `2e0a0d84…` | `277a3eab…` |
  | SMA crossover | | `c280fe54…` | `336b2b53…` |
  | Ridge return forecast | | `bfd0909d…` | `721ab23d…` |

- **Lock and contracts:**
  - `runtime.lock` is byte-identical to `main`'s, and `uv export --frozen` reproduces it offline. The Anthropic SDK is only an extra.
  - The strategy schema's hash is pinned (`STRATEGY_SCHEMA_SHA256`).
  - Comparing `schemas/qrs.schema.json` with `main`'s shows additions only:
    - seven new definitions
    - `exploratory` added to the evidence-class enum
    - the import fields of an observations manifest (`columns`, `event_format`, `source_sha256`) made optional, so that a derived feature dataset can omit them, with a validator requiring either those or a derivation
  - Imported observation and bars IDs are pinned unchanged (`test_existing_observation_and_bars_ids_are_unchanged`).
  - The only operator added is `qrs.as_of_aggregate@1`, and no operator version changed.
- **Stores:** Phase 4A stores open with each experiment's window filled and its repeat flags kept, as above.

### Offline

- **Through the local gate:** the whole Python suite and the browser tests ran inside
  `unshare -rn`, a network namespace with only loopback up. See [Local gate](#local-gate).
- **As a non-root user, as CI does:**
  - **Setup:** inside `unshare --net` with only loopback up, as the user `ubuntu`, with no
    credential in the environment. A connection to an outside address failed with "Network is
    unreachable".
  - **On workstream 7's code after the final integration (`b4b3399`):**
    - `pytest`: 595 passed. This is the whole Python suite, adapters, replay and the
      Anthropic SDK tests included, with the extra installed.
    - `pytest -m e2e`: 13 passed.
    - The hash check reproduced every hash.
  - **Deviation from CI:** `uv` was copied to a directory the user can read, as CI's tool cache is,
    because the lock test runs `uv export`.
- **Hosted CI:**
  - The whole Python suite runs in the same kind of namespace, as the runner user, with `PATH` kept
    so the lock test finds `uv`. In Phase 4B the e2e job ran it before the browser tests; since #27
    the Python job runs it, and the e2e job runs only the browser tests.
  - The audit checked the step's quoting, `PATH` and `HOME` for an `ubuntu-latest` runner, and its
    30-minute limit, which the Python job keeps. It has not run (see Hosted CI and macOS above).

## Local gate

`scripts/local-gate.sh` *(new)* runs on one machine what CI checks, stopping at the first failure:
- **Locks:** `uv sync --locked`, `npm ci`, and `runtime.lock` against `uv.lock` (a test).
- **Code:** ruff, and the committed schema and studio types against the contracts.
- **Studio:** Vitest, typecheck and build.
- **Tests:** the whole Python suite and the browser tests, offline in a network namespace where
  Linux provides one. Elsewhere they run online, and the gate says so.
- **Export parity:** `scripts/check-exports.sh` *(new)*. This is the check the CI export job ran
  inline; the job now calls the script, so CI and the gate share one copy.
- **Historical hashes:** `scripts/check-hashes.py` *(new)* recomputes the Stage 2 demo's dataset,
  code and result (CLI and bundle) and each bars-only template's code and result. It compares them
  with the full values recorded at Phase 4A (`5624dda`, checked there), results on Linux x86_64
  only. A changed hash fails it.

On workstream 7's code after the final integration (`b4b3399`) the gate passed in 12 minutes:

| Step | Result |
| --- | --- |
| Locks, lint, schema and types | Clean |
| Studio | 33 passed; typecheck and build clean |
| Python suite, offline | 595 passed, 13 deselected |
| Browser tests, offline | 13 passed |
| Export parity | All 7 bundles reproduce their CLI results: the 4 bars-only templates', the 2 hybrid templates' with their features, and the extension's |
| Historical hashes | Dataset, code and result unchanged |

## Mutation checks

Each new rule was broken once, and the suite run on the affected tests. Every mutation below
failed the suite. On workstream 7's code after the final integration (`b4b3399`), 43 of them
were run in one pass, and all were caught. The one that recomputes repeat flags was run on
workstream 6 (#25).

| Rule | Mutation | Caught by |
| --- | --- | --- |
| Availability | Rows usable at publication (no latency) | `test_feature_rows_hold_their_values_availability_and_document` |
| Availability | A document's latency allowed to fall | `test_a_correction_is_usable_after_the_version_it_corrects_however_slowly_that_was_answered` |
| Availability *(new)* | A shared response checked against its own row's publication | `test_a_response_shared_with_a_later_identical_version_is_not_taken_for_an_altered_store` |
| Availability *(new)* | A shared response checked against kept versions only | `test_a_response_shared_with_a_version_dropped_as_a_copy_is_not_taken_for_an_altered_store` |
| Replay *(new)* | Capture times not read as UTC | `test_capture_times_with_any_offset_are_read_as_utc` |
| Retrospective classification | No capture ever retrospective | `test_feature_rows_hold_their_values_availability_and_document` |
| Validation | Bounds not checked | `test_an_answer_is_refused_unless_it_is_exactly_the_declared_numbers` |
| Validation | A boolean read as a number | the same |
| Budget | Request count not checked | `test_a_recording_over_its_budget_is_refused_before_any_call` |
| Budget *(new)* | Only missing requests counted | `test_the_request_budget_counts_the_responses_already_stored` |
| Budget | Cost bound not checked | `test_a_recording_over_its_budget_is_refused_before_any_call` |
| Budget | A response over its token bound does not stop the recording | `test_either_token_count_over_its_bound_stops_the_recording`, `test_a_response_reporting_more_tokens_than_its_bound_stops_the_recording_and_counts_against_the_budget` |
| Budget *(new)* | Only input, or only output, tokens checked | `test_either_token_count_over_its_bound_stops_the_recording` |
| Replay without a provider | Replay asks for the adapter | `test_replay_without_stored_responses_fails_naming_the_documents_and_publishes_nothing` |
| Context window *(new)* | No guard; the bound itself refused; the output limit left out; a missing `num_ctx` accepted | `test_ollama_must_be_given_a_context_window_that_holds_every_request_whole` |
| Context window *(new)* | The model's context length not checked; a `num_ctx` equal to it refused; an unreported one accepted; Ollama asked for it with the wrong field; one architecture's key read | `test_ollama_is_refused_a_context_window_beyond_the_models_before_any_chat`, and the other Ollama recordings |
| Context window *(new)* | `shift` not sent, sent as `0`, or sent as `true` | `test_ollama_is_asked_for_the_schema_on_its_chat_api_with_no_tools` |
| No provider in runs *(new)* | A worker imports an adapter | `test_runs_experiments_and_exports_import_no_provider_module` |
| Provenance *(new)* | No document publication time per line | `test_a_feature_dataset_carries_its_extraction_responses_and_provenance` |
| Provenance *(new)* | `documents.json` not checked | `test_a_changed_or_missing_provenance_file_is_refused` |
| Deduplication *(new)* | A kept document's later version dropped as a copy | `test_a_kept_documents_later_version_is_kept_even_when_it_copies_another` |
| Evidence | The weakest-class helper returns the strongest | `test_feature_rows_hold_their_values_availability_and_document` |
| Evidence | A run takes its strongest dataset's class | `test_a_run_is_as_weak_as_the_weakest_dataset_it_read`, `test_an_exploratory_experiment_is_labelled_and_its_holdout_kept_apart_from_verified_ones` |
| Refusal of verified experiments | Never refused | `test_a_verified_experiment_reading_exploratory_data_is_refused_before_anything_is_queued` |
| Refusal of verified experiments | Exploratory bars skipped | `test_a_verified_experiment_on_exploratory_bars_is_refused_too` |
| Cross-experiment repeats | Repeats within one experiment only | `test_holdout_evaluations_are_tracked_across_experiments_by_their_window` |
| Cross-experiment repeats | Repeats of the same kind only | the same |
| Cross-experiment repeats | The holdout start ignored | the same |
| Cross-experiment repeats | An experiment's own evaluations only through its window | `test_a_store_from_before_phase_4b_fills_each_experiments_window_and_keeps_its_repeats` |
| Older stores | Untraced experiments filled with no bars | the same |
| Older stores | Repeat flags recomputed | the same |
| Holdout windows *(new)* | Any dataset traced back from the backtest taken for its bars | `test_the_bars_traded_are_traced_back_through_operators_that_pass_bars_on` |
| Aggregate | An invalid latest version replaced by an older valid one; invalid rows counted; each document's first version in the window taken | `test_a_latest_version_with_no_value_counts_as_none_not_as_its_earlier_version`, `tests/test_hybrid.py` |

The workstream reviews ran further mutations, each recorded in its pull request:
- proxies, connecting by name and SDK retries (#23)
- window edges, revision dating and `until` (#24)
- nine availability and deduplication rules (#22)

The audit ran its own on availability and the budget, all caught.

## Audit

An independent audit of the whole stack (`62c9d96..ed58e93`) read the plan, code and tests afresh,
ran every suite online and offline, and probed with its own scripts:
- replay with every network and provider module blocked
- an import audit hook in every process of runs, trials, holdout evaluations, exports and bundles
- tampering
- holdout rewrites
- a real Phase 4A store
- independent hashes

It found no critical defect. Its findings and what was done:

| Severity | Finding | Done |
| --- | --- | --- |
| Material | Versions rendering the same request share a response; captured promptly after the first, it predated the later version, which was refused as an altered store. This blocked timely capture, the only route to non-exploratory AI features, on ordinary data | Fixed in #22, with a test |
| Material | A store record edited consistently (answer or capture time) is accepted, and an edited capture time could lift a dataset's class | Documented as the store's trust model (decision 11, #21); narrowed here. Not preventable without authenticated records; see [Open items](#open-items) |
| Material | Exported feature datasets lacked their documents' publication times, URLs and manifest | Fixed in #22: `responses.jsonl` lines and `documents.json`, checked on load |
| Minor | Deduplication could drop a kept document's correction, leaving its superseded version counting | Fixed in #22, with a test |
| Minor | Test gaps: stored requests in the request limit; input-only and output-only token stops; wrong fields and non-numbers end to end; exploratory documents; the provider-import test ran no worker, trial, holdout or bundle; the holdout test rewrote answers only | Tests added or strengthened in #22, #23 and #24 |
| Minor | Decision 9 said the earlier-evaluation counts never change; they do when an older experiment left unfilled is filled later | Corrected in #25 |
| Minor | The committed responses were read in the locale's encoding | UTF-8 in #24 |
| Minor | The CI step's failure skipped the browser tests; the e2e job's comment was stale | Fixed here |
| Minor | Inaccurate or overstated statements in this record | Corrected throughout |
| Minor, not changed | Ollama can silently cut a prompt longer than `num_ctx` and report a normal finish; bundles carry the adapters' source and the templates' fixture files as package data, unused; no `.gitattributes`, so a CRLF checkout (Windows, unsupported) would change the documents ID; the CI offline step repeats the Ubuntu suite | See [Open items](#open-items). The Ollama cut is now guarded: see [Final integration](#final-integration) |

## Final review

A final independent review of the whole stack (`38cc08f`) read the plan, code, tests and this
record afresh, and probed with its own scripts:
- a stricter replay
- the macro template's holdout rewritten
- the news template's latency boundary
- a real Phase 4A store
- capture-time offsets
- 30 targeted mutations

It found no critical or material defect, and confirmed each area: leakage and temporal integrity,
provenance, consent and security, budget, experiments and holdout tracking, and compatibility,
historical hashes and `runtime.lock` included. Its findings and what was done:

| Severity | Finding | Done |
| --- | --- | --- |
| Minor | What remained of the audit's material finding: a response shared with a version that deduplication drops (a copy of an earlier document's text) was refused as an altered store, as its capture predated every kept version | Fixed in #22: the check compares a response with the earliest version that renders its request, dropped copies included. Regression test |
| Minor | Replay crashed on a stored capture time with an offset other than UTC, which the record contract accepts | Fixed in #22: capture times are read as UTC. Regression test |
| Minor | Untested: tracing the traded bars counted any dataset parameter on the way | Test strengthened in #25; the mutation now fails |
| Minor | Code quality: an unused constant (#21); the version label formatted three times (#22); the templates' reference deduplicated by the rule before the audit (#24) | Fixed in each |
| Minor | The CI browser-test step also ran after a failed setup | Fixed here: it runs only if the offline suite step ran |
| Minor | Inaccurate statements: the macro fixture has two out-of-bounds revisions, not one; the replay test refuses connections rather than disabling sockets; the cited probe ran before the pinned IDs changed; the `no_provider` fixture's reach; the exploratory documents' late capture | Corrected in decision 13 (#24), the macro test (now checking both revisions) and this record |
| Minor | Ollama can silently cut a prompt longer than `num_ctx` (documented, not guarded) | Guarded in the final integration (#23): see below |
| Minor, not changed | `templates.prepare`, used by tests, CI and the gate, makes `qrs.templates` import the extraction pipeline, so the service loads it (no adapter) at start | See [Open items](#open-items) |

## Final integration

The final integration guarded Ollama against silent prompt truncation, documented what the store's
checksums establish, and had the integrated stack reviewed independently once more.

- **Every request whole** (#23, `f84d2d3`): an extraction through Ollama must declare a `num_ctx`
  that holds every request whole, or it is refused before any call, in replay as in recording
  (see [Validation](#validation)).
- **Within the model's context length** (#23, `ea16b96`): Ollama's source (`llm/server.go`) lowers
  a `num_ctx` above the model's context length to that length with only a log warning, so a
  `num_ctx` that passed the check above could still be cut. Before each request a recording now
  reads the model's context length (`POST /api/show`) and stops before any chat if `num_ctx`
  exceeds it or Ollama does not report it.
- **Refused by Ollama too** (#23, `ebeffad`, `9cbf366`): each chat sends `"shift": false`, which
  Ollama's `ChatRequest` accepts. A current runner without context shift answers a prompt longer
  than `num_ctx` with HTTP 400 ("the prompt is longer than the context length…") instead of
  cutting it, and the error stops the recording. It changes only the HTTP body, not the request,
  its key or any hash.
  - **Reviewed independently:** no critical or material defect. The reviewer traced the field
    through Ollama's history:
    - Versions before it ignore it, as Ollama's JSON binding ignores unknown fields.
    - Some later versions accept it but still cut the prompt while chat truncation is on, their
      default. The answer then stops at its length and is recorded as truncated, with no values.
  - **Decision 11** now says so (`9cbf366`): in every version, the two checks above are what keep
    each prompt whole.
- **The store's checksums** (#21, `8a5899b`): decision 11 now says they establish consistency, not
  authenticity or when a response was captured (see [Provenance](#provenance)).

**The review.** An independent reviewer, given the stack at `de40066` and then the second guard,
read every change since the final review, checked the guards against Ollama's source, and ran:
- the suite, the browser tests, lint, the schema and types, and the studio, on both heads
- each changed lower branch's suite
- 14 mutations of the two guards
- probes of real-sized documents against an 8,192-token model

It found no critical defect. Its findings and what was done:

| Severity | Finding | Done |
| --- | --- | --- |
| Material, at `de40066` | Ollama lowers a `num_ctx` above the model's context length, so a 40 KB report on an 8,192-token model with `num_ctx` 42,000 passed the first guard and would have been cut | Already fixed by `ea16b96` (#23), found independently while integrating. The reviewer confirmed it against Ollama's source: the key read is the one Ollama clamps on, equality is not clamped, and a missing, malformed or failed answer stops the recording |
| Minor | Untested: reading the context length under the model's own architecture (the stand-in always reported `llama`) | Fixed in #23: the stand-in reports a `qwen2` model; the mutation now fails |
| Minor | Decision 11's list of rules checked in replay omitted the context window; the README did not say Ollama needs `num_ctx` | Fixed in #23 |
| Minor | Not documented: the largest document a model can take, and that raising `num_ctx` changes every request's key, so answers are asked again and those captured late are retrospective | Fixed in decision 11 (#23): it gives the limit (6,912 bytes of instructions, prompt and schema on an 8,192-token model with a 256-token output limit) and advises declaring `num_ctx` once, as the model's context length, as the README does |
| Minor | A stopped recording said "run it again to continue", which repeats the refusal until its cause is fixed | Fixed in #22: "once the cause is fixed, run it again" |
| Minor | Decision 11 said write-once records guard against corruption, but only the request is hashed | Fixed in #21: they guard against overwriting and partial writes, not edits |
| Minor | This record was stale for the final integration | Updated here |
| Minor | The guarantee assumes a chat template of at most 1,024 tokens. Raising the framing allowance later would make existing Ollama extractions fail to replay (published datasets are unaffected). Sending `"shift": false` would make current Ollama refuse rather than cut, as a second line of defence | The assumption is in decision 11. `"shift": false` is sent since `ebeffad` (#23), reviewed independently. The framing allowance is left for your decision: see [Open items](#open-items) |
| Minor, not changed | Responses recorded before `ea16b96` with an oversized `num_ctx` would replay unchecked | None exist outside tests: Phase 4B is unmerged |

The plan (`docs/stage-4b-plan.md`) is left as merged in #19.

## Checks

On the final stack, locally (hosted CI unavailable; see above):

| Check | Result |
| --- | --- |
| `scripts/local-gate.sh` on workstream 7's code (`b4b3399`) | Passed in 12 minutes: see [Local gate](#local-gate) |
| `pytest` and `pytest -m e2e`, offline as a non-root user, on workstream 7's code (`b4b3399`) | 595 and 13 passed, with no network and no credential; every hash reproduced. See [Offline](#offline) |
| `pytest` on each lower branch's head, in its own environment | #20 `2c36e5c`: 406; #21 `29ad891`: 450; #22 `52e975d`: 510; #23 `ebeffad`: 543; #24 `f92b230`: 589; #25 `f65bb28`: 595. All passed |
| The final wording change (`9cbf366`) | It changes a code comment and decision 11, nothing else, in #23–#26. The adapter and provider tests pass on it (77) |
| Mutation pass on workstream 7's code (`b4b3399`) | 43 mutations of the new rules, all caught, in one pass. See [Mutation checks](#mutation-checks) |
| `qrs schema` and `npm run gen:types` on each branch | No diff |
| `runtime.lock` | Identical to `main`'s |
| Ancestry | Each branch descends from the one below it and from the head reviewed before it; workstream 1 from `main` (`62c9d96`) |
| Hosted CI | Workstreams 1–4 passed every job, on Ubuntu and macOS 15, before the audit. Since then no runner: outstanding until 1 December |
| macOS 15 | Outstanding: no Mac attached, no hosted runner |

## Open items

- **Outstanding until hosted runners return (1 December) or a Mac is available:**
  - **macOS 15:** the templates' fills, metrics and pinned feature IDs. On a Mac,
    `scripts/local-gate.sh` runs the same checks online, with result hashes compared on Linux only.
  - **Hosted CI:** the offline CI step, and every head after the audit.
  - **#27:** it makes hosted CI cheaper. It was merged after Phase 4B, verified locally: its export
    job's time limit joined `scripts/check-exports.sh`, and the e2e job's whole-suite step went, as
    its Python job runs that suite offline. Its workflows have not run on GitHub.
- **Outstanding, needing real services:** smoke tests of a recording through a real Ollama with a
  pulled model, and through the Anthropic API with a key, each then replayed offline. The adapters
  are tested against loopback stand-ins that follow the documented APIs, and the Ollama safeguards
  against Ollama's source, not against a running server.
- **For your decision:**
  - **The trusted store:** the response store is trusted local state. Its checksums establish
    consistency, not authenticity or capture time. Whether rows are retrospective rests on capture
    times the recording machine attests, which someone with write access to `.qrs` could edit.
    Making that tamper-proof would need authenticated records, such as signatures or
    provider-signed timestamps, which no adopted decision covers.
  - **The framing allowance:** the 1,024 tokens of framing in each request's bound are now part of
    what replay checks, so raising them later would make existing Ollama extractions fail to
    replay until `num_ctx` is raised, which changes their requests. Published feature datasets are
    unaffected.
- **Known limitations, not changed:**
  - **Recording:**
    - It is sequential, with no batching or concurrency.
    - A stopped recording keeps what it stored and resumes when run again.
  - **Ollama (decision 11):**
    - Its token counts can be understated when it caches a prompt prefix.
    - A document whose request's bound exceeds the model's context length cannot be extracted
      through it, so it is refused rather than cut. The bound counts a token per byte, so it also
      refuses some documents that would fit: an English text of about 10 KB, some 2,300 tokens,
      needs a window of over 11,000.
    - The guarantee assumes a chat template of at most 1,024 tokens. A model's usual template adds
      tens; a custom Modelfile template could add more.
    - A loopback port that forwards elsewhere, such as an SSH tunnel, cannot be detected.
  - **Packaging and fixtures:**
    - Bundles include the adapters' source and the templates' fixture files as package data. Nothing imports them.
    - The CSV field limit is process-wide.
    - `templates.prepare` refuses a store that already holds a different record for one of its fixture's requests. Only tests, CI and the gate call it, but because it lives in `qrs.templates`, the service imports the extraction pipeline (no adapter) when it starts.
  - **Holdout tracking:**
    - An experiment from a Phase 4A store whose strategy can't be traced now is left unfilled until a later open loads the operator it needs. Until then it tracks only its own evaluations.
    - Moving the holdout start, or re-importing the same bars under a new ID, starts a new window with no recorded looks.
- **Not verified:**
  - Windows
  - macOS before 15
- **The intermittent browser test:**
  - `test_a_custom_table_operator_appears_in_the_palette_then_runs_and_exports` dropped an edge after a drag twice in CI, on runs for #19 and workstream 2. Both passed when re-run.
  - It predates Phase 4B and passed 6 of 6 times locally, and 8 of 8 under CPU load.
  - Its cause is unknown.
- **What the data shows:** the fixtures are synthetic. They verify the mechanics, not that news or reports predict prices.
