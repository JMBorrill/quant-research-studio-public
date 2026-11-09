# Stage 4 plan: Phase 4B, LLM and hybrid data

**Status:** approved, with decisions D1–D11 as recorded below; nothing here is implemented yet.
Starts from `main` at `5624dda`, where Phase 4A is verified
([stage-4-verification.md](stage-4-verification.md)).

**Basis:**
- **The Addendum's Phase 4B scope:** one LLM adapter; captured news and report extraction; response
  replay; news and macro templates; a fixture adapter to exercise interchangeability.
- **Its exit criteria:**
  - offline replay makes zero provider calls
  - late documents, revised values and missing responses are handled explicitly
  - market, Python, ML and LLM nodes combine without changing the graph schema
- **Its ML and LLM controls:**
  - Capture exact document versions, prompts, provider and model identity, decoding settings,
    response bytes, the structured parse, cost, and request and response hashes.
  - Replay stored responses offline: temperature zero or a seed is no replay guarantee. A missing
    response fails replay, and refreshing a response makes a new dataset.
  - Retrospective use of an LLM is exploratory unless its eligibility is defensible.
  - Record processing latency and when a response could be used.
  - Retrieved text is untrusted data, with no tool execution.
- **The Addendum's adapter rule:** an LLM provider accepts a structured request and returns
  validated output plus model identity, usage and the raw response. Credentials stay in adapters,
  and backtests consume captured outputs, with no live provider calls.
- **The audit:**
  - one narrow Python provider interface
  - no training labels or realised returns in prompts
  - ordinary transforms run with no model calls or API keys
- **The review of the draft plan:** seven workstreams, and decisions D1–D11 as recorded under
  [Decision record](#decision-record). The review changed three of the draft's recommendations:
  - **D2:** the local adapter uses Ollama's native `/api/chat`.
  - **D3:** retrospective AI outputs are exploratory unless their point-in-time validity is
    independently justified; a provider's declared knowledge cutoff is no such justification.
  - **D11:** contaminated evaluations are refused from verified results, not just flagged.

  It also refined three: D1 names Ollama, D5 counts holdout evaluations across experiments, and D8
  makes the budget required. The decision record marks each change.
- **An independent review of this plan** found the problems corrected here: proxies and
  forwarding models in the local adapter, numeric bounds in the hosted schema, replay depending on a
  provider, and acceptance criteria placed in a workstream that could not yet prove them.

## Starting point

Phase 4A is complete, with all nine exit criteria met. It has 391 Python, 12 browser and 29 Studio
tests, and CI is green on Ubuntu, macOS 15 and offline. Phase 4B builds on it as follows. Each row
is unchanged unless its last column says otherwise.

| Phase 4B need | Phase 4A contract | Change |
|---|---|---|
| Immutable documents and features | Content-addressed datasets (`datasets.publish`, `verified`); manifests serialised without unset fields; the kind discriminator | A `qrs/documents` kind. LLM features are `qrs/observations` |
| When an LLM feature may be used | Observation rows (event, publication time in UTC, revision, estimate flag); `qrs.as_of`; `Run.observations`' cutoff at the last close | None. A feature row's publication time is when its response could be used |
| Import | `importer.parse_declaration`, with row-numbered rejections | A third declaration kind |
| Strategy data checks | Dataset parameters declare their kind (`x-qrs-dataset`). `graph.check_data` refuses wrong kinds, and bars without a calendar | None |
| Missing sentiment | Nullable signals: a missing input makes a signal missing, and a position policy treats a missing signal as none | None. Missing sentiment blocks entries, and an exit wired separately stays active |
| Causality | The harness, which also cuts as-of operators between a release and its revision | Covers the new aggregate operator |
| ML and Python | `qrs.fit_ridge`, `qrs.predict`, model provenance, extension operators | None |
| Evidence | A run's class is the weakest among the datasets it read | One more class, `exploratory` |
| Holdout | Trials cut off at the holdout start; recorded evaluations; repeats flagged within an experiment | Verified and exploratory analysis; repeats tracked across experiments |
| Exports and rights | A bundle includes a dataset's files only if it is redistributable | None. A feature dataset carries its responses, so the rule covers them |
| Templates | Versioned registry, fixtures, expected fills | Instantiation by dataset kind; fixtures with documents |

**Phase 4A limitations that matter here:**
- **Gaps:** as-of values older than their maximum age leave gaps after the declared warm-up. Sparse
  news makes this more common. It is not leakage.
- **Feature checks:** `qrs.predict` checks feature names, not how the features were made.
- **Queue and summaries:** the single run queue, and the summary cost at 200 trials, are unchanged.

## Adopted decisions

### 1. No provider inference in historical backtests

- **Where providers are called:** only in `qrs extract --record`, which writes an immutable feature
  dataset. Strategies read it through as-of operators, like any observations.
- **What never imports a provider module:** the runtime, compiler, worker, experiments and exports.
  Backtests, experiments and exported bundles therefore run as now, with no key and no network.
- **Replay never initialises a provider.** The adapter registry names modules, which are imported
  only when recording. Replay neither imports nor constructs an adapter.

### 2. Documents datasets (`qrs/documents`)

- **Storage:** `documents.parquet`, one row per document version:
  - document ID
  - an optional revision label
  - event date, by default the UTC date of publication
  - publication time, in UTC
  - an estimate flag
  - optional title
  - text and its SHA-256
  - an optional source URL: metadata only, never fetched
- **Import:** from CSV (D7), through `qrs import` and `POST /api/datasets/import`. Rows are refused
  with their numbers, whatever the evidence class, for:
  - a missing, naive or out-of-range publication time
  - empty text
  - a repeated document version
- **Manifest:**
  - evidence class and source
  - `redistributable`
  - `hosted_processing`: whether the text may be sent to a hosted provider. It is required, with
    no default (D10).
  - how any estimated timestamps were made
  - the imported file's SHA-256, and the column mapping
- **Estimated timestamps** cannot be `point_in_time`, as for observations.
- **No strategy reads raw text:** no operator takes a documents dataset, and the existing kind check
  refuses one wired to a dataset parameter.

### 3. The extraction document (`qrs/extraction`, version 1)

A JSON document, run by `qrs extract EXTRACTION.json`.

**Fields:**
- `documents`: the documents dataset ID.
- **Provider:**
  - `provider`: `fixture`, `ollama` or `anthropic`
  - `model`
  - `endpoint`: the Ollama server's URL, or the fixture's answers file. It is absent for
    `anthropic`.
- **Prompt:**
  - `instructions`: the system text.
  - `prompt`: a template whose only placeholders are `{title}` and `{text}`.

  Nothing else can reach a prompt, not dates, market data, labels or returns. Extraction reads only
  a documents dataset.
- `fields`: one to 16 numeric outputs, each with a name, description, unit and inclusive bounds.
  The JSON schema sent to the provider, and used for validation, is built from them.
- **Decoding:**
  - `max_output_tokens`.
  - `options`: provider settings, sent as given. They are Ollama's `temperature`, `seed` and
    `num_ctx`, and Anthropic's `effort`.
- **Timing:** `latency_seconds`, the declared processing latency (D6).
- `capture`: a label. Changing it is the only way to ask the provider again.
- `dedupe`: drop later copies of identical normalised text, keeping the earliest publication (D9).
- `budget`, required:
  - the maximum requests
  - the maximum cost
  - the prices per million input and output tokens (D8)
- `redistributable`: whether the features may be exported. It takes effect only if the documents
  are redistributable too.

**Strict validation:** every refusal below happens before any call. Those that need an adapter
(resolving the Ollama endpoint, listing its models, finding the Anthropic SDK) are made only with
`--record`; the rest are made by replay too, from the registry's description of each provider,
which imports no adapter. Replay therefore needs neither the SDK nor the network.
- unknown keys
- a prompt without `{text}`, or with any other placeholder
- field names that are not unique lower-case identifiers, or that clash with an observation column
  (`event`, `published`, `revision`, `estimated`, `document`)
- bounds that are not finite, or with a minimum not below the maximum
- options the provider does not accept, or of the wrong type
- `max_output_tokens` outside the provider's limit
- a negative latency, budget or price
- with `--record`, an Ollama endpoint whose host does not resolve only to loopback addresses, or
  a model Ollama does not list locally or lists as running remotely
- an endpoint given for `anthropic`
- a documents dataset that is missing, of the wrong kind, or fails its checksum
- `anthropic` for documents without `hosted_processing`

### 4. The response store, `.qrs/responses/`

- **Key:** the SHA-256 of the canonical request:
  - provider and model
  - instructions and rendered prompt
  - the answer schema exactly as sent: every field a required number, no other properties, and
    no numeric bounds, which the hosted structured-output mode does not accept; bounds are given
    in each field's description and enforced by the validator
  - `max_output_tokens` and options
  - capture label

  Credentials, the endpoint and the budget are not part of it.
- **Record:**
  - the request
  - the exact response bytes
  - the provider's response ID
  - the model identifiers the provider reported (for Ollama, the model's digest too)
  - the stop reason, and a refusal's category
  - input and output tokens, and the cost at the declared prices
  - when the response was captured, and the measured latency
- **Immutable:**
  - Each record is written once, by atomic rename. A different record for an existing key is
    refused.
  - A record that fails to parse is refused when read.
- **What is stored:** invalid and refused responses are stored, since they are what the provider
  returned. Transport errors store nothing.

### 5. Explicit recording and bounded cost

- **Replay is the default:** `qrs extract` builds the dataset from stored responses only.
  - If any request has no stored response, it fails before publishing and names the documents.
  - It never calls, imports or constructs a provider.
- **Recording is explicit:** only `qrs extract --record` calls a provider.
  1. **Estimate:** compute an upper bound on the missing requests' cost. Input tokens are taken as
     each rendered request's UTF-8 bytes plus a fixed allowance, and output tokens as
     `max_output_tokens`.
  2. **Refuse:** if the bound exceeds the budget, refuse before any call.
  3. **Call:** ask for each missing request in turn, storing each response. Stop at any of these:
     - before a request whose bound would exceed what remains
     - at the first transport error
     - at a response that reports more tokens than its bound
  4. **Build:** replay.
- **A stopped recording publishes nothing.** Running it again reuses every stored response, at no
  cost.
- **One build path:** replay builds every dataset, so a recorded and a replayed extraction give the
  same dataset ID.
- **Each recording is its own capture:** capture times and measured latencies are part of the
  responses. Recording the same extraction twice, into an empty store, gives the same values but a
  different dataset ID. A template's recorded responses are therefore committed with it.

### 6. Feature datasets are observations

`qrs extract` publishes a `qrs/observations` dataset, adding to the existing format.

- **Rows:** one per extracted document version. Each holds:
  - the version's event date and revision
  - the document ID, in a new optional column, which is never a value column: `qrs.as_of` and the
    aggregate join only declared values
  - the fields' values
  - the publication time and estimate flag below
- **Availability:** a row can be used from the document's publication time plus the larger of the
  declared and the measured latency. That is its publication time.
- **Retrospective rows:** a row is retrospective if its response was captured after it could be
  used, as in any extraction of past documents. It is estimated if it is retrospective, or if its
  document's timestamp is estimated.
- **Strict response validation:** a response is valid only if all of these hold:
  - the provider reports a normal finish (Ollama `stop`, Anthropic `end_turn`), not a truncation or
    a refusal
  - its text is exactly one JSON object, with no other text and no duplicate keys
  - it has exactly the declared fields
  - each field is a finite JSON number within its bounds

  Otherwise every value in the row is missing, and its reason is recorded: `truncated`, `refused`,
  `not_json`, `wrong_fields`, `not_a_number`, `out_of_bounds` or `no_answer`. A session that depends
  on the row has no value, rather than an older one.
- **What the dataset holds:**
  - `observations.parquet`
  - `extraction.json`: the extraction document, with its instructions and prompt template
  - `responses.jsonl`: for each row, its stored record, with the request's rendered prompt, and its
    validation status and reason
- **Manifest:**
  - The existing fields, with units from the field declarations.
  - An optional `derivation`:
    - the documents dataset ID, and the SHA-256s of `extraction.json` and `responses.jsonl`
    - provider and requested model, and every model identifier the responses reported
    - whether text went to a hosted provider
    - latency and capture label
    - counts of requests, retrospective rows, dropped duplicates, and invalid rows by reason
    - tokens and cost
- **Existing manifests are unchanged:** the import-only fields (`columns`, `event_format`,
  `source_sha256`) become optional, present only on imported datasets. Existing manifests serialise
  byte for byte as before, so their IDs are unchanged.

### 7. Evidence classes: `exploratory`

- **The new class:** `exploratory`, between `synthetic` and `retrospective`. The order becomes
  `synthetic`, `exploratory`, `retrospective`, `point_in_time`.
- **A feature dataset's class:** the weaker of its documents' class and, if any row is
  retrospective, `exploratory`. Retrospective AI outputs on real documents are therefore
  `exploratory`. On synthetic documents they are `synthetic`, as there is no real future for a
  model to know.
- **Independent justification:** only QRS's own capture record lifts AI outputs out of
  `exploratory`, by showing that every response existed by the time its row could be used.
  - A model maker's stated knowledge cutoff is not independent, so Phase 4B accepts no declaration
    in its place.
  - Point-in-time features therefore need point-in-time documents with responses captured as they
    arrive, which nothing in Phase 4B schedules.
- **Declared classes:** imports may declare `exploratory`, for AI outputs made elsewhere.
- **Runs:** a run's class stays the weakest among the datasets it read. A run that reads an
  exploratory dataset is never shown as better than exploratory.

### 8. Verified and exploratory analysis

- **Verified is the default:** `Experiment` gains `exploratory: bool = False`. A verified experiment
  whose base strategy reads any `exploratory` dataset is refused before queuing. The refusal names
  the dataset and says how to run the experiment as exploratory.
- **The check is made once,** when the experiment is created:
  - experiments already refuse search axes over dataset parameters, so every trial reads the same
    datasets
  - a dataset's class cannot change in place
- **Exploratory experiments:**
  - Their trials run like any others.
  - Their ranking is labelled exploratory analysis.
  - Their holdout evaluations are recorded as exploratory.
- **Kept apart:** verified holdout results never include an exploratory evaluation. The API and
  studio list exploratory evaluations separately, and label them.
- **Older experiments** read back as verified, and behave as before.

### 9. Holdout evaluations tracked across experiments

- **A holdout window:** the bars dataset the backtest trades, plus the holdout start.
- **Recorded with every evaluation:** its window and its kind (verified or exploratory).
- **Repeats:** an evaluation is a repeat if its window was evaluated before, in any experiment and
  of either kind. Phase 4A counted repeats within one experiment only.
- **Earlier evaluations:** each record states how many there were of its window, verified and
  exploratory.
- **Experiment summaries:** each lists every evaluation of its window across experiments, with
  verified and exploratory evaluations apart, each with its experiment, trial and metrics.
- **Older stores:** each existing evaluation gets its window from its experiment's base revision,
  and is marked verified, when the store is opened. Its recorded repeat flag is kept.

### 10. Providers (`src/qrs/providers/`)

- **Interface:** one function per adapter, `complete(request, endpoint) -> Reply`. A `Reply` holds:
  - the raw bytes and the text
  - the response ID and the reported model identifiers
  - the stop reason
  - the tokens

  The registry records each adapter's module, whether it is hosted, the options it accepts and its
  output-token limit. Parsing and validation are one function, shared by every adapter.
- **`fixture`:** answers from a JSON file keyed by the SHA-256 of the rendered prompt: like any
  provider, it sees only the request. It is offline and deterministic, and the templates and CI
  use it.
  - Unknown text gets no answer, and is stored as invalid.
  - It reports the answers file's SHA-256 as its model.
- **`ollama`:** Ollama's chat API, `POST {endpoint}/api/chat`, through the standard library's
  `urllib`. It adds no dependency, and works with any server that implements this API.
  - **Request:** `stream: false`; the field schema as `format`; `num_predict` set from
    `max_output_tokens`; the allowed options.
  - **Recorded:**
    - the reported model, `done_reason`, and the prompt and output token counts
    - the model's digest, from Ollama's model list, read once per recording. A re-pulled tag is
      therefore a different model.
  - **Loopback only:** the endpoint's host must resolve only to loopback addresses. The adapter
    connects to the address it checked, never through a proxy (it ignores `http_proxy` and the
    like), so neither a proxy nor DNS rebinding can send text elsewhere.
  - **Local models only:** the model must be listed by Ollama with a digest, and not as a remote
    or cloud model, which Ollama forwards to a hosted service.
- **`anthropic` (optional):** the Messages API through Anthropic's official Python SDK, installed as
  the optional extra `qrs[hosted]`. Without it, only `anthropic` extraction is refused, with an
  install hint.
  - **Request:**
    - `max_tokens` from `max_output_tokens`
    - the instructions as the system text, and the rendered prompt as the user message
    - structured outputs (`output_config.format`) constrained to the answer schema
    - an optional `effort`
    - no tools
    - no server-side fallback to another model, so that each dataset has one requested model
    - SDK retries off, so that a transport error stops the recording, as for Ollama
  - **No sampling settings:** the current API rejects them, so stored responses are the only
    replay.
  - **Recorded:** the response ID, `model`, `stop_reason`, a refusal's category, input and output
    tokens, and the raw response body.
  - **Examples:** they name `claude-opus-5-5`. It always thinks, and thinking counts towards
    `max_output_tokens`.
  - **Output limit:** 16,000 tokens per request, so that requests need no streaming.
- **Nothing but parsing:** no tool or function calling, no browsing, no code execution. A response
  is parsed as JSON numbers and nothing else, so injected document text can at most move a value
  within its bounds.

### 11. Security

- **Credentials:**
  - Only the Anthropic SDK reads them, from its standard sources such as `ANTHROPIC_API_KEY`, and
    only while recording.
  - They never appear in extraction documents, the response store, manifests, run records, logs or
    exports.
- **Extraction is CLI-only (D4):** the service never holds a key, and the studio shows feature
  datasets read-only.
- **Consent:** hosted transmission needs `hosted_processing` on the documents dataset, and every
  provider call needs `--record`.
- **Fixed endpoint:** the hosted endpoint is the SDK's. An extraction document cannot redirect it.
- **Logs:** they name the document, request hash, status and tokens, never the text or a key.

### 12. Rights

- **Feature datasets:** one is redistributable only if both its extraction and its documents say so.
  - When it is, exports include its rows, extraction document and responses. These hold rendered
    prompts, and so document text.
  - Otherwise, the bundle lists it as not included, as now.
- **Documents datasets:** never exported, since no strategy references them.
- **Hosted providers:** whether a provider's retention and terms suit a licence is the user's call,
  recorded per documents dataset by `hosted_processing`.

### 13. Hybrid operators and templates

- **`qrs.as_of_aggregate` (version 1):** bars to a series. At each close it:
  1. takes each document's latest version published by then, or each event's for datasets with no
     document column
  2. keeps those published within the trailing window
  3. ignores missing values
  4. returns their mean, sum or count, or a missing value if fewer than `min_count` remain

  Its parameters are the dataset, column, window in days, statistic and `min_count`. It uses the
  `as_of` alignment and passes the harness.
- **Instantiation by kind:** each of a template's dataset parameters takes the chosen dataset of the
  kind it declares, so one call can supply both bars and features. A single dataset, as today, still
  fills every parameter of its kind. Existing template files are unchanged.
- **News and technical trend (`news_trend`):**
  - **Entry** needs all three of:
    - a positive 3-day mean sentiment (`qrs.as_of_aggregate`, `min_count` 1)
    - MACD 12/26/9 crossing above its signal
    - the close above SMA 200

    Positions are long/flat.
  - **Exit:** MACD crossing below its signal.
  - **Missing or stale sentiment:** the entry is missing, so no new position opens, while the exit
    stays active.
- **Economic report and ML (`macro_ridge`):**
  - **Features:** report features (first releases and revisions) through `qrs.as_of` per field,
    merged with returns and RSI.
  - **Model:** `qrs.fit_ridge` on a forward-return label, then `qrs.predict`.
  - **Entry:** a fixed threshold above the fixture's round-trip costs, with long/flat positions.
  - **Surprises:** its conditions state that it computes none, since that would need dated
    consensus.
- **Fixtures:** both ship with synthetic, redistributable fixtures:
  - calendar bars
  - documents with late publications, a revision, a duplicate and an invalid answer
  - the fixture adapter's answers
  - the recorded responses
  - expected fills

## What is recorded, and where

| Requirement | Where |
|---|---|
| Immutable responses | Write-once store records; each feature dataset's `responses.jsonl`, hashed in its manifest |
| Source provenance | Documents manifest (source, file SHA-256, column mapping); per-document source URL; each feature row's document ID and revision; the derivation's documents dataset ID |
| Model identifiers | The requested model in each request and in the derivation; the reported model (and the Ollama digest) in each record; every reported model in the derivation |
| Prompts | `extraction.json` (instructions and template) in the feature dataset; each record's rendered request |
| Availability times | Each document's publication time; each feature row's publication time; each record's capture time and measured latency; the declared latency |
| Rights | Documents' `redistributable` and `hosted_processing`; the feature dataset's effective `redistributable`; the derivation's record of hosted transmission |
| Cost | Each record's tokens and cost; the derivation's totals; the budget in `extraction.json` |

## Compatibility guarantees

- **Existing artefacts behave as before:** strategies, datasets, runs, experiments, stores and
  exports open unchanged.
  - The demo keeps dataset `91a9b520`, code `d8fad9fa` and result `24357159`.
  - Each template keeps its code and result hashes:
    - Bollinger/RSI `fff3fd9a`/`9debb8e1`
    - EMA crossover `2e0a0d84`/`277a3eab`
    - SMA crossover `c280fe54`/`336b2b53`
    - ridge forecast `bfd0909d`/`721ab23d`
- **The strategy document is unchanged:** `qrs/strategy` version 1, with the same schema. New nodes
  are operators.
- **Contracts change only by addition:**
  - new kinds
  - optional fields, left out when unset
  - one more evidence class
  - `Experiment.exploratory`, which defaults to false
  - operator `qrs.as_of_aggregate` version 1

  No operator version changes.
- **`runtime.lock` is unchanged:** the Ollama adapter uses the standard library, and the Anthropic
  SDK is an optional extra, which the lock's export leaves out. Run records' lock hashes are
  therefore unaffected; comparisons still show that the QRS version differs, as after any release.
- **Store:** new columns are added, and filled, when an older store is opened (decision 9).

## Exit gate

1. **Replay never initialises a provider:**
   - Each template's extraction replays from its committed responses with adapter modules made
     unimportable and sockets disabled, and in CI's network namespace.
   - It gives the same dataset ID on Ubuntu and on macOS.
   - Recording and then replaying give the same ID.
   - An extraction recorded through the Anthropic adapter replays with the SDK uninstalled.
2. **Explicit recording and bounded cost:**
   - Without `--record`, no provider is called.
   - An extraction whose upper bound exceeds its budget is refused before any call.
   - The recorded request count never exceeds the budget.
   - Recorded cost never exceeds it while responses stay within their bounds; a response that
     exceeds its bound stops the recording.
   - A stopped recording publishes nothing, and running it again costs nothing for the responses
     already stored.
3. **Strict validation:**
   - Every extraction-document refusal in decision 3 happens before any call.
   - Each invalid kind of response in decision 6 is stored, gives missing values with its reason,
     and is counted in the manifest.
4. **Late documents, revisions and missing responses:**
   - A document is not used at a close if it was published after the close, or within the
     latency before it.
   - A later revision never changes an earlier session's value.
   - The aggregate counts each document once, in its latest version published by the close, and a
     deduplicated copy is not counted.
   - Replay with any response missing fails before publishing, naming the documents.
5. **Provenance and immutability:**
   - Every feature dataset holds everything in [What is recorded](#what-is-recorded-and-where).
   - Store records cannot be overwritten.
   - A tampered feature dataset or record is refused.
6. **Evidence:**
   - Retrospective AI outputs on real documents are `exploratory`; on synthetic documents they are
     `synthetic`.
   - Only responses captured by the time they could be used lift that class.
   - A run that reads an exploratory dataset is never shown as better than exploratory, in its
     results, comparisons or rankings.
7. **Verified and exploratory analysis:**
   - A verified experiment that would read an exploratory dataset is refused before anything is
     queued.
   - An exploratory experiment's ranking and holdout evaluations are labelled as exploratory,
     listed apart, and never among verified results.
8. **Cross-experiment tracking:**
   - Evaluating a holdout window already evaluated in another experiment is flagged as a repeat.
   - It is recorded with the counts of earlier verified and exploratory evaluations.
   - Every experiment on the window lists both kinds.
   - Older stores open with their evaluations' windows filled in.
9. **Security:**
   - A sentinel credential appears in no file under `.qrs`, no log, no run record and no export.
   - An Ollama endpoint that resolves outside loopback is refused before connecting.
   - Hosted extraction without `hosted_processing` is refused before any call.
   - No request body contains tools.
   - Strategy runs, experiments and exports import no provider module.
   - Without the optional extra, only recording through `anthropic` fails.
10. **Rights:** exports include a feature dataset and its responses only when both its extraction
    and its documents allow it.
11. **Interchangeability:**
    - One extraction is run through the fixture, Ollama and Anthropic adapters. The last two run
      against loopback test servers returning the same answers.
    - All three give identical rows; only provenance differs.
12. **Hybrid composition:** a strategy combining market indicators, an extension (Python) operator,
    fit/predict and LLM features validates, compiles, runs and exports, with no change to the
    strategy schema, editor or compiler.
13. **Templates:**
    - Both match their expected fills, with app, CLI and export parity.
    - On macOS 15, fills are exact and metrics are within tolerance.
    - Every macro fixture prediction is at least 1e-6 from its threshold.
    - Missing sentiment blocks entries while exits stay active.
14. **Holdout isolated:** rewriting every holdout-period document and its features changes no
    trial's result hash.
15. **Backward compatibility:**
    - Every existing test passes.
    - The hashes above, and `runtime.lock`, are unchanged.
    - Older stores and experiments open and behave as before.
16. **Offline:** every test, the adapters' and the browser tests included, passes with no network
    access and no key. CI runs the whole Python suite, with the optional extra, offline as well as
    the browser tests.

## Workstreams

Each workstream ships as its own pull request with the listed tests, and CI stays green on Ubuntu,
macOS 15 and offline. After a contract change, the schema and generated types are regenerated
(`uv run qrs schema`, then `npm run gen:types`).

### 1. Documents datasets

**Depends on:** nothing.

- **Contracts:** `DocumentsDeclaration` and `DocumentsManifest` (decision 2).
- **Import:** the third kind in `parse_declaration`, reusing the existing reader and its
  rejections.
- **Storage:** `documents.parquet`, listed by `list_manifests`.
- **Docs:** `docs/decisions.md` §6 and the README's import section.

**Acceptance:**
- **Refusals:** each refused case is refused by row, for each evidence class.
- **Estimated timestamps:** refused as `point_in_time`; otherwise kept, with how they were made.
- **Text:** multi-line, quoted and non-ASCII text round-trips byte for byte, and each row's SHA-256
  matches its text.
- **IDs:**
  - The same file gives the same dataset ID.
  - A tampered dataset is refused.
  - Existing dataset IDs are unchanged.
- **Strategies cannot read documents:**
  - A documents dataset wired to a dataset parameter is refused before running.
  - The studio's pickers do not list documents datasets.

### 2. Provider interface, response store and fixture adapter

**Depends on:** nothing.

- **`src/qrs/providers/`:**
  - the interface, and a registry that imports adapter modules only on demand
  - the fixture adapter
  - the strict response validator
  - the cost bound and cost
- **`src/qrs/responses.py`:** request hashing and the write-once store.

**Acceptance:**
- **Request hash:**
  - It is the same across processes and on both platforms.
  - It changes with each hashed field.
  - The endpoint, credentials and budget do not change it.
- **Store:**
  - A different record for an existing key is refused.
  - Two concurrent writers leave one complete record.
  - A corrupted record is refused when read.
- **Validator:** a hand-worked case for each invalid reason in decision 6, including:
  - duplicate keys
  - a number given as a string
  - a boolean
  - `NaN`
  - a value exactly on a bound, which is valid
- **Cost:**
  - The bound is at least the actual tokens on the fixtures, non-ASCII text included.
  - Cost is checked against a hand calculation.
- **Fixture adapter:** it is deterministic, and a prompt it has no answer for gives `no_answer`.
- **Lazy loading:** importing `qrs.providers` imports no adapter module.

### 3. Extraction and feature datasets

**Depends on:** 1 and 2.

- **Contract:** `Extraction`, with its strict validation (decision 3).
- **Command:** `qrs extract [--record]`:
  - rendering and deduplication
  - budgets
  - replay, and recording
  - publication, with provenance and availability (decisions 4–6)
- **Gates:** hosted consent (decision 11) and the rights rule (decision 12). They are tested with a
  stub hosted adapter.
- **Evidence:** the `exploratory` class, and its place in the weakest-class order (decision 7).
- **Docs:** `docs/decisions.md`.

**Acceptance:**
- **Exit gates 1 to 5 and 10, with the fixture adapter on test documents.** The templates' replay
  (gate 1) and the aggregate (gate 4) are proved in workstream 5.
- **Availability:** a hand-worked fixture checks:
  - each row's time, as publication plus the larger latency
  - retrospective and estimate flags
  - revisions
  - that deduplication keeps the earliest copy
- **Evidence:** a table of the class each documents class gives with responses captured before and
  after their rows could be used. Exploratory, synthetic and point-in-time outcomes are each
  covered.
- **Feature manifests:**
  - Existing observation IDs are unchanged.
  - `qrs.as_of` reads a feature dataset with no change, and refuses `document` as a column.
- **Rights:** a feature dataset is redistributable only as decision 12 allows.

### 4. Ollama and Anthropic adapters

**Depends on:** 3.

- `ollama` and `anthropic` (decision 10).
- **The optional extra:** `qrs[hosted]`, installed in CI for its tests and left out of
  `runtime.lock`.
- **Docs:** the README's extraction section, with the security and rights rules.

**Acceptance (against loopback test servers built on `http.server`):**
- **Ollama:**
  - The request body has the schema as `format`, `stream: false` and no tools.
  - The reported model, digest and token counts are recorded.
  - A `length` finish is `truncated`.
  - HTTP errors and timeouts store nothing and stop the recording.
  - A host that resolves outside loopback is refused before connecting.
  - With `http_proxy` and `HTTP_PROXY` set, requests still go straight to the loopback server.
  - A model Ollama lists as remote, or does not list, is refused before any call.
- **Anthropic** (the test server is set through the SDK's base-URL environment variable):
  - The request carries the answer schema, without numeric bounds, and `max_tokens`, with no tools
    and no fallback.
  - The response ID, model, stop reason and tokens are recorded.
  - A refusal is stored as `refused`, with its category.
  - A missing extra refuses only recording through `anthropic`; its stored responses still replay.
- **Exit gates 9 and 11, and `runtime.lock` unchanged.** Gate 11 declares a latency above any
  loopback latency, so that the rows' times agree.
- **CI:** the Python jobs install the optional extra.

### 5. Aggregate operator and hybrid templates

**Depends on:** 3.

- `qrs.as_of_aggregate`, instantiation by kind, `news_trend` and `macro_ridge` (decision 13).
- **Template fixtures:** `TemplateFixture` gains optional documents, extraction and recorded
  responses. The tests, service checks and CI export job that loop over every template prepare
  each template's features from them.
- **The gallery request:** it takes one dataset per kind; a single `dataset_id` keeps working.

**Acceptance:**
- **`qrs.as_of_aggregate`:** a hand-worked fixture covers:
  - the window's edges, and a publication exactly at the close
  - revisions
  - datasets without a document column
  - `min_count` and missing values

  It passes the harness, and a leaky variant is caught: one that dates a revision from its
  document's first publication.
- **Instantiation:** existing templates instantiate identically, with the same hashes.
- **Exit gates 12, 13 and 14,** gate 1 for the templates' extractions, and gate 4's aggregate
  rules. The composition strategy uses the example extension's table operator in the macro
  strategy.

### 6. Verified and exploratory experiments, cross-experiment tracking, studio

**Depends on:** 3 and 5.

- **Experiments:**
  - `Experiment.exploratory`, and the refusal (decision 8)
  - holdout windows, kinds, repeats and counts across experiments, with the store migration
    (decision 9)
- **Studio:**
  - the `exploratory` class
  - a feature dataset's provenance, in its details
  - a gallery picker for each dataset kind a template needs
  - the experiment form's exploratory option, and the refusal's message
  - exploratory rankings labelled, and the window's verified and exploratory evaluations listed
    apart
- **Docs:** `docs/decisions.md` §7, and the README.

**Acceptance:**
- **Exit gates 7 and 8**, including a store from before this change.
- **Playwright,** with features prepared by `qrs extract` in the test's setup from test-only bars
  and documents declared `retrospective`. The template fixtures stay synthetic; these test data are
  not, so their features, and runs that read them, are exploratory:
  1. Instantiate the news template with chosen bars and features.
  2. See the run's exploratory class and the features' provenance.
  3. See a verified experiment refused.
  4. Run it as exploratory, evaluate its holdout, and see the evaluation labelled and listed apart.
  5. See a second experiment's evaluation of the same window flagged as a repeat.
- **Vitest:** covers the per-kind pickers and the separation of the two kinds of evaluation.

### 7. Exit-gate verification

**Depends on:** 1–6.

- **Record:** `docs/stage-4b-verification.md`, giving each exit criterion's evidence.
- **CI:** the offline job runs the whole Python suite, with the optional extra, as well as the
  browser tests.
- **Mutation checks:** one for each new rule: availability, retrospective classification,
  validation, budget stops, replay without a provider, refusal of verified experiments, and
  cross-experiment repeats.

**Acceptance:** every exit criterion is met with recorded evidence, or listed as a gap with its
reason.

## Order

1. **First, in parallel:** documents datasets (1), and the provider interface and store (2).
2. **Then:** extraction (3).
3. **Then, in parallel:** the adapters (4), and the operator and templates (5).
4. **Then:** experiments and studio (6).
5. **Last:** verification (7).

## Risks

- **Model knowledge:**
  - Retrospective AI outputs on real data are always exploratory in Phase 4B, so most historical
    LLM research will be exploratory. This is intended.
  - Contemporaneous capture is the only route to verified results. Nothing schedules it yet.
- **Imported AI features:** an observations import declared `retrospective` or `point_in_time` can
  hold AI outputs made elsewhere. QRS cannot detect this; it rests on the importer's declaration.
- **Prompt injection:** bounded numeric outputs, and no tools, limit what injected text can do. It
  can still bias a value within its bounds. Every response is kept for audit.
- **Nondeterminism:**
  - Only stored responses replay.
  - Recording again gives a new dataset.
  - Model tags and aliases can change, which is why reported identifiers and Ollama digests are
    recorded per response.
- **Cost:**
  - The upper bound can refuse an extraction that would have fitted.
  - Prices are user-declared and can be stale.
  - Thinking tokens count as output, so a low `max_output_tokens` gives `truncated` rows.
- **Rights and personal data:**
  - Documents can hold personal data, and are stored unencrypted in `.qrs`.
  - Hosted transmission needs explicit consent, and exports follow the rights rule.
  - Some hosted models need 30-day retention. Whether that suits a licence is the user's call.
- **Storage:** the store keeps one file per request, and documents keep their full text. Nothing is
  cleaned up in Phase 4B.
- **Selection bias:** choosing among prompts, models or capture labels by their backtests is
  optimistic. Cross-experiment tracking counts holdout looks, but not choices made outside
  experiments.
- **Repeats can be sidestepped:** moving the holdout start, or re-importing the same bars under
  another name (a new dataset ID), starts a new holdout window with no recorded looks.
- **Local forwarding:** a server on loopback that forwards requests elsewhere, other than Ollama's
  own remote models, cannot be detected.
- **Unrecorded spend:** a transport error after the provider has billed a request leaves that
  spend unrecorded, at most one request per recording. The framing allowance in the cost bound is
  an estimate; a response that exceeds its bound stops the recording.
- **Timestamps:** news timestamps are often first-seen times. Estimate flags make the claim
  explicit, not true.
- **Sparse features:** they leave gaps, as Phase 4A's as-of joins do. `min_count` makes this
  explicit.
- **Ollama versions:** JSON-schema `format` needs a recent Ollama. A server that ignores it gives
  answers that validation marks invalid.
- **Hosted refusals:** safety classifiers can refuse financial or news content. Refusals are
  counted, and remove entries rather than adding them.
- **CSV for long text:** quoting errors are likely. Rejections give row numbers.
- **Carried forward from Phase 4A:**
  - trusted modules run unsandboxed
  - the harness covers only registered operators
  - the single run queue
  - the `sudo` the offline CI step relies on

## Decision record

| # | Decision | Adopted | Changed from the draft |
|---|---|---|---|
| D1 | Adapters | Fixture, Ollama and an optional Anthropic adapter. The Anthropic SDK is an optional extra, kept out of `runtime.lock`. Server-side fallbacks and SDK retries are off. Replay needs neither | Names Ollama |
| D2 | Local protocol | Ollama's chat API (`/api/chat`), loopback only | Was an OpenAI-compatible endpoint |
| D3 | Evidence | `exploratory`, between `synthetic` and `retrospective`. Retrospective AI outputs are exploratory unless QRS's own capture record shows them point-in-time | The model's stated knowledge cutoff no longer lifts the class, and is not recorded |
| D4 | Where extraction runs | CLI only | — |
| D5 | Holdout tracking | Across experiments, by bars dataset and holdout start, counting both kinds | Repeats now span experiments |
| D6 | Latency | Required. A row is usable after the larger of the declared and measured latency | — |
| D7 | Documents format | CSV | — |
| D8 | Prices and budget | Declared in each extraction. The budget is required | Budget made required |
| D9 | Deduplication | Exact match on normalised text, keeping the earliest | — |
| D10 | Hosted consent | `hosted_processing`, required with no default | — |
| D11 | Contaminated evaluations | Refused from verified experiments. Exploratory experiments are separate and labelled | Was flag only |

## Not in Phase 4B

- **Trading and code:**
  - live trading and brokers
  - running generated or untrusted code
  - tool or function calling
- **Network:**
  - browsing or fetching: source URLs are metadata
  - scheduling document capture as documents arrive
- **Extraction outside the CLI:** in the studio or the service.
- **Provider features:**
  - batch APIs
  - prompt caching
  - embeddings and retrieval
  - fine-tuning
  - more providers
- **Studio import:** importing documents in the studio. They are imported through the CLI and API
  only.
- **Other justifications for point-in-time AI outputs,** such as a model's stated knowledge cutoff.
- **Document processing:**
  - fuzzy deduplication and entity linking
  - dated consensus and surprise features
- **Response store cleanup.**
- **Still excluded:**
  - corn data (Phase 5)
  - multiple assets and intraday data
