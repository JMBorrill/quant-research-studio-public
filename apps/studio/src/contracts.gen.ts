/* Generated from schemas/qrs.schema.json by npm run gen:types. Do not edit. */

export interface QuantResearchStudioContracts {
  [k: string]: unknown;
}
/**
 * A research computation (Stage 5): a registered method and version, its parameters, and its
 * named inputs, read only before `until`, and before every run's own cutoff.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "AnalysisDocument".
 */
export interface AnalysisDocument {
  inputs: {
    [k: string]:
      | BarsInput
      | ObservationsInput
      | EquityInput
      | RecordInput
      | FillsInput
      | ModelInput
      | ExperimentInput
      | TrialInput;
  };
  kind?: "qrs/analysis";
  method: string;
  method_version: number;
  name: string;
  params?: {
    [k: string]: unknown;
  };
  /**
   * The project the analysis belongs to; its until is capped at the project's confirmation start.
   */
  project?: string | null;
  /**
   * Leave out of the sample every session before this date; earlier data may still be read for alignment.
   */
  since?: string | null;
  /**
   * Read nothing on or after this date.
   */
  until?: string | null;
  version?: 1;
}
/**
 * A bars dataset's session returns: `percent`, the close's change as a fraction, or
 * `price_change`, the close's change times the multiplier, in currency per contract.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "BarsInput".
 */
export interface BarsInput {
  dataset: string;
  kind: "bars";
  measure?: "percent" | "price_change";
}
/**
 * An observations column, joined at each close of the `on` bars as the As-of operator joins it:
 * the latest event published by then, in its latest revision, if no older than max_age_days.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ObservationsInput".
 */
export interface ObservationsInput {
  column: string;
  dataset: string;
  kind: "observations";
  max_age_days?: number;
  /**
   * The bars dataset whose sessions and closes the values are joined at.
   */
  on: string;
}
/**
 * A succeeded run's backtest equity, as session returns: the change in equity as a fraction.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "EquityInput".
 */
export interface EquityInput {
  kind: "equity";
  run: string;
}
/**
 * A column of a table a succeeded run saved with a record node.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "RecordInput".
 */
export interface RecordInput {
  column: string;
  kind: "record";
  node: string;
  run: string;
}
/**
 * A succeeded run's backtest fills, as one value per session of its equity: the quantity bought
 * less the quantity sold, or the commission or slippage paid; 0 on sessions without a fill.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "FillsInput".
 */
export interface FillsInput {
  column?: "quantity" | "commission" | "slippage";
  kind: "fills";
  run: string;
}
/**
 * A term of the models a run's fit node fitted: a feature's coefficient (on the standardised
 * feature, as fitted) or the intercept, at the session at whose close each model was fitted.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ModelInput".
 */
export interface ModelInput {
  kind: "model";
  node: string;
  run: string;
  /**
   * A feature the model was fitted on, or 'intercept'.
   */
  term: string;
}
/**
 * A finished experiment: every trial's parameters and status, and the session returns of its
 * succeeded trials over their common window, as its ranking compares them, best first.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ExperimentInput".
 */
export interface ExperimentInput {
  experiment: string;
  kind: "experiment";
}
/**
 * A symbolic reference, only in a hypothesis's specification: the equity of an experiment's
 * trial at `rank` in its ranking, from its holdout evaluation. It is resolved when the hypothesis
 * is evaluated: the holdout evaluation is queued then, and read as an equity input.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "TrialInput".
 */
export interface TrialInput {
  experiment: string;
  kind: "trial";
  rank?: number;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "AnalysisInfo".
 */
export interface AnalysisInfo {
  created_at: string;
  error?: string | null;
  finished_at?: string | null;
  id: string;
  method: string;
  name: string;
  project?: string | null;
  result_sha256?: string | null;
  started_at?: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
}
/**
 * The most a recording may ask and spend (Phase 4B).
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "Budget".
 */
export interface Budget {
  /**
   * Per million input tokens.
   */
  input_price: number;
  /**
   * What the whole extraction may cost: its stored responses, plus the upper bound of those missing.
   */
  max_cost: number;
  /**
   * Distinct requests the whole extraction may make, those already stored included.
   */
  max_requests: number;
  /**
   * Per million output tokens.
   */
  output_price: number;
}
/**
 * The CSV header names holding each field.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ColumnMapping".
 */
export interface ColumnMapping {
  close: string;
  date: string;
  high: string;
  low: string;
  open: string;
}
/**
 * How a continuous futures series was built from its contracts (Stage 5 amendment A3). Its prices
 * are research data, not executable prices: a backtest refuses them.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ContinuousSeries".
 */
export interface ContinuousSeries {
  /**
   * difference: earlier contracts shifted by price differences at each roll; none: spliced as traded.
   */
  adjustment: "difference" | "none";
  /**
   * Every contract, in order, covering every session exactly once.
   *
   * @minItems 1
   */
  contracts: [ContractSegment, ...ContractSegment[]];
  /**
   * When the series moves to the next contract, as the provider states it.
   */
  roll_rule: string;
}
/**
 * One contract a continuous series is built from, and where it is used.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ContractSegment".
 */
export interface ContractSegment {
  /**
   * Added to the contract's own prices in the series (0 for an unadjusted series).
   */
  adjustment: number;
  /**
   * The contract's last trading day.
   */
  expiry: string;
  /**
   * Its first session in the series.
   */
  first_session: string;
  /**
   * Its last session in the series, before its expiry.
   */
  last_session: string;
  /**
   * The contract's own symbol, such as its month code and year.
   */
  symbol: string;
}
/**
 * Describes an immutable daily OHLC dataset. Its canonical JSON hash is the dataset ID, so any
 * change to data or metadata produces a different dataset.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "DatasetManifest".
 */
export interface DatasetManifest {
  /**
   * One row per trading session, indexed by session date.
   */
  bar?: "1D";
  data_sha256: string;
  end: string;
  evidence_class: "synthetic" | "exploratory" | "retrospective" | "point_in_time";
  /**
   * Present only on datasets imported from a file.
   */
  import_report?: ImportReport | null;
  instrument: Instrument;
  kind?: "qrs/dataset";
  name: string;
  /**
   * Whether exports may include the data bytes.
   */
  redistributable: boolean;
  rows: number;
  /**
   * Where the data came from, e.g. the synthetic generator and its seed.
   */
  source: string;
  start: string;
  version?: 1;
}
/**
 * How an imported dataset was read from its file, and the expected sessions the file lacks.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ImportReport".
 */
export interface ImportReport {
  /**
   * Where the expected sessions came from: an exchange calendar and its library version, or 'weekdays (approximate)'.
   */
  calendar: string;
  columns: ColumnMapping;
  date_format: string;
  /**
   * Expected sessions between the first and last rows that the file lacks; reported, never filled.
   */
  missing_sessions: string[];
  price_unit: "currency" | "cents";
  /**
   * SHA-256 of the imported file's bytes.
   */
  source_sha256: string;
}
/**
 * One futures contract, or a continuous series of contracts (Stage 5). Prices are quoted in
 * `currency` per unit of the underlying; one contract's value is price × multiplier.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "Instrument".
 */
export interface Instrument {
  /**
   * Present only for a continuous series: how it was built from its contracts.
   */
  continuous?: ContinuousSeries | null;
  currency: string;
  /**
   * Last day the contract may be held; data must end before it. None only for a continuous series.
   */
  expiry?: string | null;
  /**
   * Initial margin as a fraction of notional value.
   */
  margin_init: number;
  /**
   * Maintenance margin as a fraction of notional value.
   */
  margin_maint: number;
  multiplier: number;
  symbol: string;
  tick_size: number;
}
/**
 * How a feature dataset was extracted from documents (Phase 4B). Its files, extraction.json,
 * responses.jsonl and documents.json, sit beside the data and are checked against these hashes
 * when it is loaded.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "Derivation".
 */
export interface Derivation {
  capture: string;
  /**
   * Of the responses used, at the prices declared when each was recorded.
   */
  cost: number;
  /**
   * The documents dataset ID: the SHA-256 of documents.json, its manifest, beside the data.
   */
  documents: string;
  /**
   * Document versions dropped as copies of an earlier one's text.
   */
  duplicates: number;
  /**
   * Of extraction.json: the extraction document.
   */
  extraction_sha256: string;
  /**
   * Whether document text was sent to a hosted provider.
   */
  hosted: boolean;
  input_tokens: number;
  /**
   * Rows with no values, by reason.
   */
  invalid_rows: {
    [k: string]: number;
  };
  /**
   * The declared processing latency.
   */
  latency_seconds: number;
  /**
   * The model requested.
   */
  model: string;
  output_tokens: number;
  provider: string;
  /**
   * Every model identifier the responses reported.
   */
  reported_models: string[];
  /**
   * Distinct requests the rows needed.
   */
  requests: number;
  /**
   * Of responses.jsonl: each row's document version, publication time and URL, and its stored response, with its rendered request.
   */
  responses_sha256: string;
  /**
   * Rows whose response was captured after they could be used.
   */
  retrospective_rows: number;
}
/**
 * The CSV header names holding each document field (Phase 4B).
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "DocumentColumns".
 */
export interface DocumentColumns {
  /**
   * An optional true/false column marking publication timestamps reconstructed after the fact.
   */
  estimated?: string | null;
  /**
   * The date the document is about; by default the UTC date of publication.
   */
  event?: string | null;
  /**
   * The document's identifier, shared by all its versions.
   */
  id: string;
  /**
   * When this version was published: ISO 8601 with an explicit UTC offset.
   */
  published: string;
  /**
   * An optional label for each version, such as 'corrected'.
   */
  revision?: string | null;
  text: string;
  title?: string | null;
  /**
   * Where the document came from: kept as metadata, never fetched.
   */
  url?: string | null;
}
/**
 * What the user declares about a CSV file of documents, such as news or reports, before it is imported.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "DocumentsDeclaration".
 */
export interface DocumentsDeclaration {
  columns: DocumentColumns;
  /**
   * How the estimated publication timestamps were made; required when any row is estimated.
   */
  estimates?: string | null;
  /**
   * strptime format of the event column; required when there is one.
   */
  event_format?: string | null;
  /**
   * point_in_time requires every timestamp to be the one recorded at publication.
   */
  evidence_class: "synthetic" | "exploratory" | "retrospective" | "point_in_time";
  /**
   * Whether the text may be sent to a hosted model provider. There is no default: it must be declared.
   */
  hosted_processing: boolean;
  /**
   * Tells a documents declaration from a bars or observations one.
   */
  kind: "qrs/documents";
  name: string;
  redistributable: boolean;
  source: string;
}
/**
 * Describes an immutable documents dataset: one row per version of each document, with the
 * moment it was published. Its canonical JSON hash is the dataset ID. No operator reads one, so a
 * strategy never sees raw text; extraction turns it into observations (Phase 4B).
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "DocumentsManifest".
 */
export interface DocumentsManifest {
  columns: DocumentColumns;
  data_sha256: string;
  documents: number;
  estimated_rows: number;
  /**
   * How estimated publication timestamps were made; present only when some are.
   */
  estimates?: string | null;
  event_format?: string | null;
  evidence_class: "synthetic" | "exploratory" | "retrospective" | "point_in_time";
  hosted_processing: boolean;
  kind?: "qrs/documents";
  name: string;
  redistributable: boolean;
  /**
   * Document versions.
   */
  rows: number;
  source: string;
  /**
   * SHA-256 of the imported file's bytes.
   */
  source_sha256: string;
  version?: 1;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "Edge".
 */
export interface Edge {
  source: PortRef;
  target: PortRef;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "PortRef".
 */
export interface PortRef {
  node: string;
  port: string;
}
/**
 * A search over the parameters of one strategy revision (Stage 4). Every trial is an ordinary run
 * of that revision with one point of the search, cut off at the holdout start, so no trial reads
 * holdout data. A grid tries every point; a random search draws `trials` distinct points with
 * `seed`. Either way an experiment has at most 200 trials.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "Experiment".
 */
export interface Experiment {
  /**
   * @minItems 1
   */
  axes: [SearchAxis, ...SearchAxis[]];
  direction: "maximize" | "minimize";
  /**
   * Exploratory analysis, which may read exploratory data (Phase 4B). Its ranking and holdout evaluations are labelled exploratory and never counted as verified. A verified experiment (the default) reading exploratory data is refused.
   */
  exploratory?: boolean;
  /**
   * Trials read only the sessions before this date; the holdout is every session from it.
   */
  holdout_start: string;
  kind?: "qrs/experiment";
  name: string;
  /**
   * The backtest metric trials are ranked by.
   */
  objective:
    | "sessions"
    | "initial_cash"
    | "final_equity"
    | "net_pnl"
    | "gross_pnl"
    | "commission"
    | "slippage"
    | "return_pct"
    | "max_drawdown"
    | "max_drawdown_pct"
    | "max_drawdown_sessions"
    | "exposure_pct"
    | "fills"
    | "round_trips"
    | "contracts_traded"
    | "final_position";
  /**
   * The base strategy revision.
   */
  revision: number;
  search: "grid" | "random";
  /**
   * Random search: the seed, kept with the experiment so its trials can be drawn again.
   */
  seed?: number | null;
  strategy_id: string;
  /**
   * Random search: how many distinct points to draw.
   */
  trials?: number | null;
  version?: 1;
}
/**
 * One node parameter of the base strategy, and the values an experiment tries for it.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "SearchAxis".
 */
export interface SearchAxis {
  node: string;
  param: string;
  /**
   * @minItems 1
   */
  values: [unknown, ...unknown[]];
}
/**
 * How to turn a documents dataset into a feature dataset through a model's structured answers
 * (Phase 4B). `qrs extract` replays it from stored responses; with --record it first asks the
 * provider for the missing ones, within the budget.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "Extraction".
 */
export interface Extraction {
  budget: Budget;
  /**
   * Change it to ask the provider again: the response store is keyed by it.
   */
  capture: string;
  /**
   * Drop later copies of identical text (whitespace aside), keeping the earliest publication.
   */
  dedupe?: boolean;
  /**
   * The documents dataset ID.
   */
  documents: string;
  /**
   * A local provider's address, or the fixture's answers file; none for a hosted provider.
   */
  endpoint?: string | null;
  /**
   * @minItems 1
   * @maxItems 16
   */
  fields:
    | [OutputField]
    | [OutputField, OutputField]
    | [OutputField, OutputField, OutputField]
    | [OutputField, OutputField, OutputField, OutputField]
    | [OutputField, OutputField, OutputField, OutputField, OutputField]
    | [OutputField, OutputField, OutputField, OutputField, OutputField, OutputField]
    | [OutputField, OutputField, OutputField, OutputField, OutputField, OutputField, OutputField]
    | [OutputField, OutputField, OutputField, OutputField, OutputField, OutputField, OutputField, OutputField]
    | [
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField
      ]
    | [
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField
      ]
    | [
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField
      ]
    | [
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField
      ]
    | [
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField
      ]
    | [
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField
      ]
    | [
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField
      ]
    | [
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField,
        OutputField
      ];
  /**
   * The system text.
   */
  instructions: string;
  kind?: "qrs/extraction";
  /**
   * Declared processing latency: a row is usable this long after its document at the earliest.
   */
  latency_seconds: number;
  /**
   * Including any thinking.
   */
  max_output_tokens: number;
  model: string;
  /**
   * The feature dataset's name.
   */
  name: string;
  /**
   * Decoding settings the provider accepts, sent as given.
   */
  options?: {
    [k: string]: string | number | boolean;
  };
  /**
   * Sent for each document version, with {title} and {text} in place: no other placeholder.
   */
  prompt: string;
  provider: "fixture" | "ollama" | "anthropic";
  /**
   * Whether exports may include the features and their responses; also needs the documents to be redistributable.
   */
  redistributable: boolean;
  version?: 1;
}
/**
 * One number an extraction asks a model for, per document (Phase 4B).
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "OutputField".
 */
export interface OutputField {
  /**
   * What the number means, as the model is told it.
   */
  description: string;
  maximum: number;
  minimum: number;
  /**
   * A lower-case identifier: the feature column's name.
   */
  name: string;
  unit: string;
}
/**
 * A family's hypotheses, evaluations, adjusted p-values and conclusions.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "FamilyBlock".
 */
export interface FamilyBlock {
  family: string;
  kind: "family";
}
/**
 * Hypotheses registered together as one frozen unit (Stage 5): they are tested together and
 * their p-values adjusted together. Confirmatory registration is refused if the test period of any
 * dataset in the project's scope has been read (amendment A2).
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "FamilyDocument".
 */
export interface FamilyDocument {
  /**
   * The family's significance level.
   */
  alpha?: number;
  confirmatory: boolean;
  /**
   * Holm; Benjamini–Hochberg; or Benjamini–Yekutieli, where dependence may be negative.
   */
  correction?: "holm" | "bh" | "by";
  /**
   * @minItems 1
   */
  hypotheses: [Hypothesis, ...Hypothesis[]];
  kind?: "qrs/family";
  name: string;
  project: string;
  version?: 1;
}
/**
 * One registered hypothesis: its statement and the analysis that tests it, over the test period
 * from the project's confirmation start (to its `until`, if any). It is supported only when its
 * adjusted p-value is below the family's level with the estimate in the registered direction.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "Hypothesis".
 */
export interface Hypothesis {
  analysis: AnalysisDocument;
  direction: "greater" | "less" | "two_sided";
  id: string;
  statement: string;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "FigureBlock".
 */
export interface FigureBlock {
  analysis: string;
  kind: "figure";
  name: string;
}
/**
 * One executed order, as listed in a backtest's fills.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "Fill".
 */
export interface Fill {
  commission: number;
  price: number;
  quantity: number;
  session: string;
  side: "BUY" | "SELL";
  slippage: number;
}
/**
 * What the user declares about a CSV file of daily bars before it is imported.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ImportDeclaration".
 */
export interface ImportDeclaration {
  /**
   * The venue's exchange calendar, a pandas_market_calendars name such as CBOT_Agriculture; null expects every weekday.
   */
  calendar: string | null;
  columns: ColumnMapping;
  /**
   * strptime format of the date column, e.g. %Y-%m-%d.
   */
  date_format: string;
  evidence_class: "synthetic" | "exploratory" | "retrospective" | "point_in_time";
  instrument: Instrument;
  name: string;
  /**
   * currency: prices in the instrument's currency; cents: hundredths of it, divided by 100.
   */
  price_unit: "currency" | "cents";
  redistributable: boolean;
  source: string;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "Issue".
 */
export interface Issue {
  message: string;
  node?: string | null;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "MethodInfo".
 */
export interface MethodInfo {
  description: string;
  id: string;
  /**
   * Each input's name and the input kinds it accepts.
   */
  inputs: {
    [k: string]: string[];
  };
  /**
   * Each table or figure, and whether it holds rows of its inputs or only aggregates.
   */
  outputs: {
    [k: string]: "rows" | "aggregates";
  };
  params_schema: {
    [k: string]: unknown;
  };
  /**
   * What it tests: a directional estimate, a joint restriction, or nothing.
   */
  tests?: ("directional" | "joint") | null;
  title: string;
  version: number;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "Node".
 */
export interface Node {
  /**
   * Unique in the strategy; also its variable name in generated Python.
   */
  id: string;
  /**
   * Namespaced operator ID, e.g. qrs.ema.
   */
  operator: string;
  params?: {
    [k: string]: unknown;
  };
  /**
   * Operator version; results depend on it.
   */
  version: number;
}
/**
 * The CSV header names holding each observation field (Stage 4).
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ObservationColumns".
 */
export interface ObservationColumns {
  /**
   * An optional true/false column marking publication timestamps reconstructed after the fact.
   */
  estimated?: string | null;
  /**
   * The date the value is about, such as the month a figure measures.
   */
  event: string;
  /**
   * When the value was published: ISO 8601 with an explicit UTC offset, e.g. 2024-11-26T16:30:00-05:00.
   */
  published: string;
  /**
   * An optional label for each revision, such as 'second estimate'.
   */
  revision?: string | null;
}
/**
 * What the user declares about a CSV file of observations before it is imported.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ObservationsDeclaration".
 */
export interface ObservationsDeclaration {
  columns: ObservationColumns;
  /**
   * How the estimated publication timestamps were made; required when any row is estimated.
   */
  estimates?: string | null;
  /**
   * strptime format of the event column, e.g. %Y-%m-%d.
   */
  event_format: string;
  /**
   * point_in_time requires every timestamp to be the one recorded at publication.
   */
  evidence_class: "synthetic" | "exploratory" | "retrospective" | "point_in_time";
  /**
   * Tells an observations declaration from a bars one.
   */
  kind: "qrs/observations";
  name: string;
  redistributable: boolean;
  source: string;
  /**
   * Each value column's header and its unit, e.g. {"rate": "percent"}.
   */
  values: {
    [k: string]: string;
  };
}
/**
 * Describes an immutable observations dataset: values about event dates, each row with the
 * moment it was published. Its canonical JSON hash is the dataset ID, as for bars.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ObservationsManifest".
 */
export interface ObservationsManifest {
  /**
   * Present only on imported datasets.
   */
  columns?: ObservationColumns | null;
  data_sha256: string;
  /**
   * How the dataset was extracted from documents; present only on feature datasets.
   */
  derivation?: Derivation | null;
  estimated_rows: number;
  /**
   * How estimated publication timestamps were made; present only when some are.
   */
  estimates?: string | null;
  /**
   * Present only on imported datasets.
   */
  event_format?: string | null;
  evidence_class: "synthetic" | "exploratory" | "retrospective" | "point_in_time";
  kind?: "qrs/observations";
  name: string;
  redistributable: boolean;
  rows: number;
  source: string;
  /**
   * SHA-256 of the imported file's bytes; present only on imported datasets.
   */
  source_sha256?: string | null;
  /**
   * Value columns and their units.
   */
  values: {
    [k: string]: string;
  };
  version?: 1;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "OperatorInfo".
 */
export interface OperatorInfo {
  alignment: string;
  description: string;
  family: string;
  id: string;
  inputs: PortInfo[];
  outputs: PortInfo[];
  params_schema: {
    [k: string]: unknown;
  };
  title: string;
  version: number;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "PortInfo".
 */
export interface PortInfo {
  name: string;
  type: string;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "Position".
 */
export interface Position {
  x: number;
  y: number;
}
/**
 * A research project (Stage 5): a question, the datasets in its scope, and the confirmation
 * start, from which their sessions are the test period. Analyses in the project read only before
 * it; only its families' evaluations read from it.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ProjectDocument".
 */
export interface ProjectDocument {
  confirmation_start: string;
  /**
   * The datasets in scope: every dataset the project's families may evaluate.
   *
   * @minItems 1
   */
  datasets: [string, ...string[]];
  kind?: "qrs/project";
  name: string;
  question: string;
  version?: 1;
}
/**
 * A report (Stage 5): a project's findings, generated from stored artefacts, never written by
 * hand. Building it fixes its evidence at a point in the store's history.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ReportDocument".
 */
export interface ReportDocument {
  kind?: "qrs/report";
  project: string;
  /**
   * @minItems 1
   */
  sections: [ReportSection, ...ReportSection[]];
  title: string;
  version?: 1;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ReportSection".
 */
export interface ReportSection {
  blocks?: (TextBlock | FigureBlock | TableBlock | ValuesBlock | FamilyBlock)[];
  heading: string;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "TextBlock".
 */
export interface TextBlock {
  kind: "text";
  /**
   * Plain paragraphs, separated by blank lines; shown as text, never as markup.
   */
  text: string;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "TableBlock".
 */
export interface TableBlock {
  analysis: string;
  kind: "table";
  name: string;
}
/**
 * An analysis's result: its values, warnings and assumptions.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "ValuesBlock".
 */
export interface ValuesBlock {
  analysis: string;
  kind: "values";
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "RunInfo".
 */
export interface RunInfo {
  code_sha256: string;
  created_at: string;
  error?: string | null;
  /**
   * The experiment this run is a trial of, if any.
   */
  experiment?: string | null;
  finished_at?: string | null;
  id: string;
  /**
   * The run this one re-ran, if any.
   */
  predecessor?: string | null;
  result_sha256?: string | null;
  revision: number;
  started_at?: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
  strategy_id: string;
  /**
   * Its trial number in that experiment.
   */
  trial?: number | null;
}
/**
 * A versioned strategy graph. Only nodes and edges affect execution; name, layout and template
 * provenance never reach the generated code, so they cannot change a result.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "StrategyDocument".
 */
export interface StrategyDocument {
  edges?: Edge[];
  kind?: "qrs/strategy";
  /**
   * Canvas positions by node ID (editor only).
   */
  layout?: {
    [k: string]: Position;
  };
  name: string;
  nodes?: Node[];
  template?: TemplateRef | null;
  version?: 1;
}
/**
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "TemplateRef".
 */
export interface TemplateRef {
  id: string;
  sha256: string;
  version: number;
}
/**
 * A versioned starting strategy, stored as qrs/templates/<id>.json. Instantiating it copies the
 * strategy; parameter bounds come from the operators, so a template never restates them.
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "Template".
 */
export interface Template {
  /**
   * The market conditions the strategy is designed for and the assumptions its results rest on.
   *
   * @minItems 1
   */
  conditions: [string, ...string[]];
  description: string;
  fixture: TemplateFixture;
  id: string;
  /**
   * The kind of dataset the strategy needs: a dataset manifest's bar.
   */
  required_bar: "1D";
  strategy: StrategyDocument;
  title: string;
  version: number;
}
/**
 * Bars to check a template on, stored next to it as <template id>.csv, and the fills they give.
 * A template that reads features also has documents, <template id>.documents.csv, and the
 * extraction that turns them into features, replayed from <template id>.responses.jsonl (Phase 4B).
 *
 * This interface was referenced by `QuantResearchStudioContracts`'s JSON-Schema
 * via the `definition` "TemplateFixture".
 */
export interface TemplateFixture {
  declaration: ImportDeclaration;
  documents?: DocumentsDeclaration | null;
  extraction?: Extraction | null;
  fills: Fill[];
}
