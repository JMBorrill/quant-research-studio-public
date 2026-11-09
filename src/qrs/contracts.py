"""Canonical contracts. These Pydantic models are the single source of truth: schemas/*.json and the
studio's TypeScript types are generated from them (`qrs schema`), never written by hand."""

import keyword
import math
import re
from datetime import date
from typing import Annotated, Any, Literal, get_args

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

# Names the compiler uses itself, so they cannot be node IDs (node IDs become Python variables).
RESERVED_NAMES = {"ctx", "Run"}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PortRef(Model):
    node: str
    port: str


class Edge(Model):
    source: PortRef
    target: PortRef


class Node(Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$", description="Unique in the strategy; also its variable name in generated Python.")
    operator: str = Field(description="Namespaced operator ID, e.g. qrs.ema.")
    version: int = Field(ge=1, description="Operator version; results depend on it.")
    params: dict[str, Any] = {}

    @field_validator("id")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if keyword.iskeyword(value) or value in RESERVED_NAMES:
            raise ValueError(f"'{value}' is a reserved name")
        return value


class Position(Model):
    x: float
    y: float


class TemplateRef(Model):
    id: str
    version: int
    sha256: str


class StrategyDocument(Model):
    """A versioned strategy graph. Only nodes and edges affect execution; name, layout and template
    provenance never reach the generated code, so they cannot change a result."""

    kind: Literal["qrs/strategy"] = "qrs/strategy"
    version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    nodes: list[Node] = []
    edges: list[Edge] = []
    layout: dict[str, Position] = Field(default={}, description="Canvas positions by node ID (editor only).")
    template: TemplateRef | None = None


class Issue(Model):
    node: str | None = None
    message: str


class PortInfo(Model):
    name: str
    type: str


class OperatorInfo(Model):
    id: str
    version: int
    title: str
    description: str
    family: str
    alignment: str
    inputs: list[PortInfo]
    outputs: list[PortInfo]
    params_schema: dict[str, Any]


class RunInfo(Model):
    id: str
    strategy_id: str
    revision: int
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
    code_sha256: str
    result_sha256: str | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    predecessor: str | None = Field(None, description="The run this one re-ran, if any.")
    experiment: str | None = Field(None, description="The experiment this run is a trial of, if any.")
    trial: int | None = Field(None, description="Its trial number in that experiment.")


class ContractSegment(Model):
    """One contract a continuous series is built from, and where it is used."""

    symbol: str = Field(pattern=r"^[A-Z0-9]{1,16}$", description="The contract's own symbol, such as its month code and year.")
    expiry: date = Field(description="The contract's last trading day.")
    first_session: date = Field(description="Its first session in the series.")
    last_session: date = Field(description="Its last session in the series, before its expiry.")
    adjustment: float = Field(allow_inf_nan=False, description="Added to the contract's own prices in the series (0 for an unadjusted series).")


class ContinuousSeries(Model):
    """How a continuous futures series was built from its contracts (Stage 5 amendment A3). Its prices
    are research data, not executable prices: a backtest refuses them."""

    roll_rule: str = Field(min_length=1, description="When the series moves to the next contract, as the provider states it.")
    adjustment: Literal["difference", "none"] = Field(
        description="difference: earlier contracts shifted by price differences at each roll; none: spliced as traded."
    )
    contracts: list[ContractSegment] = Field(min_length=1, description="Every contract, in order, covering every session exactly once.")

    @model_validator(mode="after")
    def _ordered(self):
        symbols = [c.symbol for c in self.contracts]
        if len(set(symbols)) < len(symbols):
            raise ValueError("each contract appears once")
        if self.adjustment == "difference" and len(self.contracts) > 1 and all(c.adjustment == 0 for c in self.contracts):
            raise ValueError("a difference-adjusted series of several contracts has adjustments; with none it is spliced")
        for earlier, later in zip(self.contracts, self.contracts[1:], strict=False):
            if later.expiry <= earlier.expiry:
                raise ValueError(f"contract {later.symbol} expires on {later.expiry}, not after {earlier.symbol} on {earlier.expiry}")
            if later.first_session <= earlier.last_session:
                raise ValueError(f"contract {later.symbol} starts on {later.first_session}, not after {earlier.symbol} ends on {earlier.last_session}")
        for c in self.contracts:
            if not c.first_session <= c.last_session < c.expiry:
                raise ValueError(f"contract {c.symbol} must be used from its first to its last session, before its expiry {c.expiry}")
            if self.adjustment == "none" and c.adjustment != 0:
                raise ValueError(f"an unadjusted series cannot adjust contract {c.symbol}")
        return self


class Instrument(Model):
    """One futures contract, or a continuous series of contracts (Stage 5). Prices are quoted in
    `currency` per unit of the underlying; one contract's value is price × multiplier."""

    symbol: str = Field(pattern=r"^[A-Z0-9]{1,16}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    multiplier: int = Field(gt=0)
    tick_size: float = Field(gt=0)
    margin_init: float = Field(ge=0, le=1, description="Initial margin as a fraction of notional value.")
    margin_maint: float = Field(ge=0, le=1, description="Maintenance margin as a fraction of notional value.")
    expiry: date | None = Field(None, description="Last day the contract may be held; data must end before it. None only for a continuous series.")
    continuous: ContinuousSeries | None = Field(None, description="Present only for a continuous series: how it was built from its contracts.")

    @model_validator(mode="after")
    def _expiry_or_continuous(self):
        if (self.expiry is None) == (self.continuous is None):
            raise ValueError("an instrument has an expiry, or is a continuous series with none")
        off_grid = [
            c.symbol
            for c in (self.continuous.contracts if self.continuous else [])
            if abs(c.adjustment / self.tick_size - round(c.adjustment / self.tick_size)) > 1e-6
        ]
        if off_grid:
            raise ValueError(f"adjustments must be multiples of the tick size {self.tick_size}: {', '.join(off_grid)}")
        return self


Bar = Literal["1D"]
# Weakest first. Exploratory: retrospective AI outputs, which a model may have produced knowing what
# happened next (Phase 4B decision 7).
EvidenceClass = Literal["synthetic", "exploratory", "retrospective", "point_in_time"]
EVIDENCE_CLASSES: tuple[str, ...] = get_args(EvidenceClass)
PriceUnit = Literal["currency", "cents"]


class ColumnMapping(Model):
    """The CSV header names holding each field."""

    date: str
    open: str
    high: str
    low: str
    close: str


class ImportDeclaration(Model):
    """What the user declares about a CSV file of daily bars before it is imported."""

    name: str = Field(min_length=1, max_length=200)
    columns: ColumnMapping
    date_format: str = Field(pattern="%", description="strptime format of the date column, e.g. %Y-%m-%d.")
    price_unit: PriceUnit = Field(description="currency: prices in the instrument's currency; cents: hundredths of it, divided by 100.")
    calendar: str | None = Field(
        description="The venue's exchange calendar, a pandas_market_calendars name such as CBOT_Agriculture; null expects every weekday."
    )
    instrument: Instrument
    evidence_class: EvidenceClass
    source: str = Field(min_length=1)
    redistributable: bool


class ImportReport(Model):
    """How an imported dataset was read from its file, and the expected sessions the file lacks."""

    source_sha256: str = Field(description="SHA-256 of the imported file's bytes.")
    columns: ColumnMapping
    date_format: str
    price_unit: PriceUnit
    calendar: str = Field(description="Where the expected sessions came from: an exchange calendar and its library version, or 'weekdays (approximate)'.")
    missing_sessions: list[date] = Field(description="Expected sessions between the first and last rows that the file lacks; reported, never filled.")


class Fill(Model):
    """One executed order, as listed in a backtest's fills."""

    session: date
    side: Literal["BUY", "SELL"]
    quantity: int
    price: float
    commission: float
    slippage: float


class TemplateFixture(Model):
    """Bars to check a template on, stored next to it as <template id>.csv, and the fills they give.
    A template that reads features also has documents, <template id>.documents.csv, and the
    extraction that turns them into features, replayed from <template id>.responses.jsonl (Phase 4B)."""

    declaration: ImportDeclaration
    fills: list[Fill]
    documents: "DocumentsDeclaration | None" = None
    extraction: "Extraction | None" = None

    @model_validator(mode="after")
    def _documents_with_extraction(self):
        if (self.documents is None) != (self.extraction is None):
            raise ValueError("A template fixture has documents and their extraction together, or neither.")
        return self


class Template(Model):
    """A versioned starting strategy, stored as qrs/templates/<id>.json. Instantiating it copies the
    strategy; parameter bounds come from the operators, so a template never restates them."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1)
    title: str
    description: str
    required_bar: Bar = Field(description="The kind of dataset the strategy needs: a dataset manifest's bar.")
    conditions: list[str] = Field(min_length=1, description="The market conditions the strategy is designed for and the assumptions its results rest on.")
    fixture: TemplateFixture
    strategy: StrategyDocument


class DatasetManifest(Model):
    """Describes an immutable daily OHLC dataset. Its canonical JSON hash is the dataset ID, so any
    change to data or metadata produces a different dataset."""

    kind: Literal["qrs/dataset"] = "qrs/dataset"
    version: Literal[1] = 1
    name: str
    instrument: Instrument
    bar: Bar = Field("1D", description="One row per trading session, indexed by session date.")
    evidence_class: EvidenceClass
    source: str = Field(description="Where the data came from, e.g. the synthetic generator and its seed.")
    redistributable: bool = Field(description="Whether exports may include the data bytes.")
    rows: int
    start: date
    end: date
    data_sha256: str
    import_report: ImportReport | None = Field(None, description="Present only on datasets imported from a file.")


class ObservationColumns(Model):
    """The CSV header names holding each observation field (Stage 4)."""

    event: str = Field(description="The date the value is about, such as the month a figure measures.")
    published: str = Field(description="When the value was published: ISO 8601 with an explicit UTC offset, e.g. 2024-11-26T16:30:00-05:00.")
    revision: str | None = Field(None, description="An optional label for each revision, such as 'second estimate'.")
    estimated: str | None = Field(None, description="An optional true/false column marking publication timestamps reconstructed after the fact.")


class ObservationsDeclaration(Model):
    """What the user declares about a CSV file of observations before it is imported."""

    kind: Literal["qrs/observations"] = Field(description="Tells an observations declaration from a bars one.")
    name: str = Field(min_length=1, max_length=200)
    columns: ObservationColumns
    event_format: str = Field(pattern="%", description="strptime format of the event column, e.g. %Y-%m-%d.")
    values: dict[str, str] = Field(min_length=1, description='Each value column\'s header and its unit, e.g. {"rate": "percent"}.')
    estimates: str | None = Field(None, description="How the estimated publication timestamps were made; required when any row is estimated.")
    evidence_class: EvidenceClass = Field(description="point_in_time requires every timestamp to be the one recorded at publication.")
    source: str = Field(min_length=1)
    redistributable: bool


class ObservationsManifest(Model):
    """Describes an immutable observations dataset: values about event dates, each row with the
    moment it was published. Its canonical JSON hash is the dataset ID, as for bars."""

    kind: Literal["qrs/observations"] = "qrs/observations"
    version: Literal[1] = 1
    name: str
    values: dict[str, str] = Field(description="Value columns and their units.")
    evidence_class: EvidenceClass
    source: str
    redistributable: bool
    estimates: str | None = Field(None, description="How estimated publication timestamps were made; present only when some are.")
    rows: int
    estimated_rows: int
    data_sha256: str
    source_sha256: str | None = Field(None, description="SHA-256 of the imported file's bytes; present only on imported datasets.")
    columns: ObservationColumns | None = Field(None, description="Present only on imported datasets.")
    event_format: str | None = Field(None, description="Present only on imported datasets.")
    derivation: "Derivation | None" = Field(None, description="How the dataset was extracted from documents; present only on feature datasets.")

    @model_validator(mode="after")
    def _imported_or_derived(self):
        imported = (self.source_sha256, self.columns, self.event_format)
        if self.derivation is None and None in imported:
            raise ValueError("An imported observations dataset records its file's SHA-256, columns and event format.")
        if self.derivation is not None and imported != (None, None, None):
            raise ValueError("A feature dataset records its derivation, not an imported file.")
        return self


class DocumentColumns(Model):
    """The CSV header names holding each document field (Phase 4B)."""

    id: str = Field(description="The document's identifier, shared by all its versions.")
    published: str = Field(description="When this version was published: ISO 8601 with an explicit UTC offset.")
    text: str
    title: str | None = None
    event: str | None = Field(None, description="The date the document is about; by default the UTC date of publication.")
    revision: str | None = Field(None, description="An optional label for each version, such as 'corrected'.")
    estimated: str | None = Field(None, description="An optional true/false column marking publication timestamps reconstructed after the fact.")
    url: str | None = Field(None, description="Where the document came from: kept as metadata, never fetched.")


class DocumentsDeclaration(Model):
    """What the user declares about a CSV file of documents, such as news or reports, before it is imported."""

    kind: Literal["qrs/documents"] = Field(description="Tells a documents declaration from a bars or observations one.")
    name: str = Field(min_length=1, max_length=200)
    columns: DocumentColumns
    event_format: str | None = Field(None, pattern="%", description="strptime format of the event column; required when there is one.")
    estimates: str | None = Field(None, description="How the estimated publication timestamps were made; required when any row is estimated.")
    evidence_class: EvidenceClass = Field(description="point_in_time requires every timestamp to be the one recorded at publication.")
    source: str = Field(min_length=1)
    redistributable: bool
    hosted_processing: bool = Field(description="Whether the text may be sent to a hosted model provider. There is no default: it must be declared.")


class DocumentsManifest(Model):
    """Describes an immutable documents dataset: one row per version of each document, with the
    moment it was published. Its canonical JSON hash is the dataset ID. No operator reads one, so a
    strategy never sees raw text; extraction turns it into observations (Phase 4B)."""

    kind: Literal["qrs/documents"] = "qrs/documents"
    version: Literal[1] = 1
    name: str
    evidence_class: EvidenceClass
    source: str
    redistributable: bool
    hosted_processing: bool
    estimates: str | None = Field(None, description="How estimated publication timestamps were made; present only when some are.")
    rows: int = Field(description="Document versions.")
    documents: int
    estimated_rows: int
    data_sha256: str
    source_sha256: str = Field(description="SHA-256 of the imported file's bytes.")
    columns: DocumentColumns
    event_format: str | None = None


class OutputField(Model):
    """One number an extraction asks a model for, per document (Phase 4B)."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="A lower-case identifier: the feature column's name.")
    description: str = Field(min_length=1, description="What the number means, as the model is told it.")
    unit: str = Field(min_length=1)
    minimum: float = Field(allow_inf_nan=False)
    maximum: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def _bounds_ordered(self):
        if not self.minimum < self.maximum:
            raise ValueError(f"{self.name}: minimum {self.minimum} must be below maximum {self.maximum}")
        return self


class ProviderRequest(Model):
    """Exactly what is asked of a model provider. The SHA-256 of its canonical JSON keys the response
    store, so the same request is never asked twice; the capture label is the only way to ask again."""

    provider: str
    model: str
    instructions: str
    prompt: str
    answer_schema: dict[str, Any] = Field(description="The JSON schema sent with the request.")
    max_output_tokens: int = Field(gt=0, strict=True)
    options: dict[str, str | int | float | bool]
    capture: str


class ResponseRecord(Model):
    """A provider's response to one request, kept once and never changed (Phase 4B)."""

    request: ProviderRequest
    raw_base64: str = Field(description="The exact response body.")
    text: str | None = Field(description="The answer text, if the provider gave one.")
    response_id: str | None
    models: list[str] = Field(description="Every model identifier the provider reported.")
    finish: Literal["end", "truncated", "refused"]
    stop_reason: str = Field(description="As the provider gave it, with a refusal's category.")
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: float = Field(ge=0, allow_inf_nan=False, description="At the extraction's declared prices per million tokens.")
    captured_at: AwareDatetime = Field(description="When the response arrived.")
    latency_seconds: float = Field(ge=0, allow_inf_nan=False, description="From sending the request to receiving the response.")


PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
PROMPT_FIELDS = ("title", "text")
OBSERVATION_COLUMNS = ("event", "published", "revision", "estimated", "document")


class Budget(Model):
    """The most a recording may ask and spend (Phase 4B)."""

    max_requests: int = Field(ge=0, description="Distinct requests the whole extraction may make, those already stored included.")
    max_cost: float = Field(
        ge=0, allow_inf_nan=False, description="What the whole extraction may cost: its stored responses, plus the upper bound of those missing."
    )
    input_price: float = Field(ge=0, allow_inf_nan=False, description="Per million input tokens.")
    output_price: float = Field(ge=0, allow_inf_nan=False, description="Per million output tokens.")


class Extraction(Model):
    """How to turn a documents dataset into a feature dataset through a model's structured answers
    (Phase 4B). `qrs extract` replays it from stored responses; with --record it first asks the
    provider for the missing ones, within the budget."""

    kind: Literal["qrs/extraction"] = "qrs/extraction"
    version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200, description="The feature dataset's name.")
    documents: str = Field(pattern=r"^sha256:[0-9a-f]{64}$", description="The documents dataset ID.")
    provider: Literal["fixture", "ollama", "anthropic"]
    model: str = Field(min_length=1)
    endpoint: str | None = Field(None, description="A local provider's address, or the fixture's answers file; none for a hosted provider.")
    instructions: str = Field(min_length=1, description="The system text.")
    prompt: str = Field(min_length=1, description="Sent for each document version, with {title} and {text} in place: no other placeholder.")
    fields: list[OutputField] = Field(min_length=1, max_length=16)
    max_output_tokens: int = Field(ge=1, description="Including any thinking.")
    options: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Decoding settings the provider accepts, sent as given.")
    latency_seconds: float = Field(
        ge=0, allow_inf_nan=False, description="Declared processing latency: a row is usable this long after its document at the earliest."
    )
    capture: str = Field(min_length=1, max_length=100, description="Change it to ask the provider again: the response store is keyed by it.")
    dedupe: bool = Field(False, description="Drop later copies of identical text (whitespace aside), keeping the earliest publication.")
    budget: Budget
    redistributable: bool = Field(description="Whether exports may include the features and their responses; also needs the documents to be redistributable.")

    @field_validator("prompt")
    @classmethod
    def _placeholders(cls, prompt: str) -> str:
        names = PLACEHOLDER.findall(prompt)
        unknown = sorted(set(names) - set(PROMPT_FIELDS))
        if unknown:
            raise ValueError(f"the prompt may hold only {{title}} and {{text}}, not {', '.join('{' + n + '}' for n in unknown)}")
        if "text" not in names:
            raise ValueError("the prompt must hold {text}")
        return prompt

    @field_validator("options")
    @classmethod
    def _finite_options(cls, options: dict) -> dict:
        bad = sorted(name for name, value in options.items() if isinstance(value, float) and not math.isfinite(value))
        if bad:
            raise ValueError(f"options must be finite: {', '.join(bad)}")
        return options

    @field_validator("fields")
    @classmethod
    def _field_names(cls, fields: list[OutputField]) -> list[OutputField]:
        names = [f.name for f in fields]
        if len(set(names)) < len(names):
            raise ValueError(f"field names must be unique: {names}")
        clashing = sorted(set(names) & set(OBSERVATION_COLUMNS))
        if clashing:
            raise ValueError(f"field names cannot be {', '.join(clashing)}: observations use them")
        return fields


class Derivation(Model):
    """How a feature dataset was extracted from documents (Phase 4B). Its files, extraction.json,
    responses.jsonl and documents.json, sit beside the data and are checked against these hashes
    when it is loaded."""

    documents: str = Field(description="The documents dataset ID: the SHA-256 of documents.json, its manifest, beside the data.")
    extraction_sha256: str = Field(description="Of extraction.json: the extraction document.")
    responses_sha256: str = Field(
        description="Of responses.jsonl: each row's document version, publication time and URL, and its stored response, with its rendered request."
    )
    provider: str
    model: str = Field(description="The model requested.")
    reported_models: list[str] = Field(description="Every model identifier the responses reported.")
    hosted: bool = Field(description="Whether document text was sent to a hosted provider.")
    latency_seconds: float = Field(description="The declared processing latency.")
    capture: str
    requests: int = Field(description="Distinct requests the rows needed.")
    retrospective_rows: int = Field(description="Rows whose response was captured after they could be used.")
    duplicates: int = Field(description="Document versions dropped as copies of an earlier one's text.")
    invalid_rows: dict[str, int] = Field(description="Rows with no values, by reason.")
    input_tokens: int
    output_tokens: int
    cost: float = Field(description="Of the responses used, at the prices declared when each was recorded.")


ObservationsManifest.model_rebuild()
TemplateFixture.model_rebuild()
Template.model_rebuild()


# The backtest metrics (qrs.analytics.metrics), any of which can rank an experiment's trials.
Objective = Literal[
    "sessions",
    "initial_cash",
    "final_equity",
    "net_pnl",
    "gross_pnl",
    "commission",
    "slippage",
    "return_pct",
    "max_drawdown",
    "max_drawdown_pct",
    "max_drawdown_sessions",
    "exposure_pct",
    "fills",
    "round_trips",
    "contracts_traded",
    "final_position",
]


class SearchAxis(Model):
    """One node parameter of the base strategy, and the values an experiment tries for it."""

    node: str
    param: str
    values: list[Any] = Field(min_length=1)


class Experiment(Model):
    """A search over the parameters of one strategy revision (Stage 4). Every trial is an ordinary run
    of that revision with one point of the search, cut off at the holdout start, so no trial reads
    holdout data. A grid tries every point; a random search draws `trials` distinct points with
    `seed`. Either way an experiment has at most 200 trials."""

    kind: Literal["qrs/experiment"] = "qrs/experiment"
    version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    strategy_id: str
    revision: int = Field(ge=1, description="The base strategy revision.")
    search: Literal["grid", "random"]
    axes: list[SearchAxis] = Field(min_length=1)
    trials: int | None = Field(None, ge=1, description="Random search: how many distinct points to draw.")
    seed: int | None = Field(None, ge=0, description="Random search: the seed, kept with the experiment so its trials can be drawn again.")
    objective: Objective = Field(description="The backtest metric trials are ranked by.")
    direction: Literal["maximize", "minimize"]
    holdout_start: date = Field(description="Trials read only the sessions before this date; the holdout is every session from it.")
    exploratory: bool = Field(
        False,
        description=(
            "Exploratory analysis, which may read exploratory data (Phase 4B). Its ranking and holdout evaluations are labelled "
            "exploratory and never counted as verified. A verified experiment (the default) reading exploratory data is refused."
        ),
    )

    @model_validator(mode="after")
    def _random_needs_trials_and_seed(self):
        random = self.search == "random"
        if (self.trials is not None) != random or (self.seed is not None) != random:
            raise ValueError("a random search needs trials and seed; a grid takes neither")
        return self


# Analyses (Stage 5): declarative research computations over datasets and run outputs.

DATASET_ID = r"^sha256:[0-9a-f]{64}$"
RUN_ID = r"^[0-9a-f]{12}$"


class BarsInput(Model):
    """A bars dataset's session returns: `percent`, the close's change as a fraction, or
    `price_change`, the close's change times the multiplier, in currency per contract."""

    kind: Literal["bars"]
    dataset: str = Field(pattern=DATASET_ID)
    measure: Literal["percent", "price_change"] = "percent"


class ObservationsInput(Model):
    """An observations column, joined at each close of the `on` bars as the As-of operator joins it:
    the latest event published by then, in its latest revision, if no older than max_age_days."""

    kind: Literal["observations"]
    dataset: str = Field(pattern=DATASET_ID)
    column: str = Field(min_length=1)
    on: str = Field(pattern=DATASET_ID, description="The bars dataset whose sessions and closes the values are joined at.")
    max_age_days: int = Field(31, ge=1, le=3660)


class EquityInput(Model):
    """A succeeded run's backtest equity, as session returns: the change in equity as a fraction."""

    kind: Literal["equity"]
    run: str = Field(pattern=RUN_ID)


class RecordInput(Model):
    """A column of a table a succeeded run saved with a record node."""

    kind: Literal["record"]
    run: str = Field(pattern=RUN_ID)
    node: str = Field(min_length=1)
    column: str = Field(min_length=1)


class FillsInput(Model):
    """A succeeded run's backtest fills, as one value per session of its equity: the quantity bought
    less the quantity sold, or the commission or slippage paid; 0 on sessions without a fill."""

    kind: Literal["fills"]
    run: str = Field(pattern=RUN_ID)
    column: Literal["quantity", "commission", "slippage"] = "quantity"


class ModelInput(Model):
    """A term of the models a run's fit node fitted: a feature's coefficient (on the standardised
    feature, as fitted) or the intercept, at the session at whose close each model was fitted."""

    kind: Literal["model"]
    run: str = Field(pattern=RUN_ID)
    node: str = Field(min_length=1)
    term: str = Field(min_length=1, description="A feature the model was fitted on, or 'intercept'.")


class ExperimentInput(Model):
    """A finished experiment: every trial's parameters and status, and the session returns of its
    succeeded trials over their common window, as its ranking compares them, best first."""

    kind: Literal["experiment"]
    experiment: str = Field(pattern=RUN_ID)


class TrialInput(Model):
    """A symbolic reference, only in a hypothesis's specification: the equity of an experiment's
    trial at `rank` in its ranking, from its holdout evaluation. It is resolved when the hypothesis
    is evaluated: the holdout evaluation is queued then, and read as an equity input."""

    kind: Literal["trial"]
    experiment: str = Field(pattern=RUN_ID)
    rank: int = Field(1, ge=1)


AnalysisInput = Annotated[
    BarsInput | ObservationsInput | EquityInput | RecordInput | FillsInput | ModelInput | ExperimentInput | TrialInput, Field(discriminator="kind")
]


class AnalysisDocument(Model):
    """A research computation (Stage 5): a registered method and version, its parameters, and its
    named inputs, read only before `until`, and before every run's own cutoff."""

    kind: Literal["qrs/analysis"] = "qrs/analysis"
    version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    method: str
    method_version: int = Field(ge=1)
    params: dict[str, Any] = {}
    inputs: dict[str, AnalysisInput]
    until: date | None = Field(None, description="Read nothing on or after this date.")
    since: date | None = Field(None, description="Leave out of the sample every session before this date; earlier data may still be read for alignment.")
    project: str | None = Field(
        None, pattern=RUN_ID, description="The project the analysis belongs to; its until is capped at the project's confirmation start."
    )


class AnalysisInfo(Model):
    id: str
    name: str
    method: str
    project: str | None = None
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
    result_sha256: str | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class MethodInfo(Model):
    id: str
    version: int
    title: str
    description: str
    inputs: dict[str, list[str]] = Field(description="Each input's name and the input kinds it accepts.")
    outputs: dict[str, Literal["rows", "aggregates"]] = Field(description="Each table or figure, and whether it holds rows of its inputs or only aggregates.")
    tests: Literal["directional", "joint"] | None = Field(None, description="What it tests: a directional estimate, a joint restriction, or nothing.")
    params_schema: dict[str, Any]


class ProjectDocument(Model):
    """A research project (Stage 5): a question, the datasets in its scope, and the confirmation
    start, from which their sessions are the test period. Analyses in the project read only before
    it; only its families' evaluations read from it."""

    kind: Literal["qrs/project"] = "qrs/project"
    version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1)
    datasets: list[str] = Field(min_length=1, description="The datasets in scope: every dataset the project's families may evaluate.")
    confirmation_start: date

    @field_validator("datasets")
    @classmethod
    def _dataset_ids(cls, ids: list[str]) -> list[str]:
        if any(not re.fullmatch(DATASET_ID, i) for i in ids) or len(set(ids)) < len(ids):
            raise ValueError("datasets must be distinct dataset IDs")
        return ids


class Hypothesis(Model):
    """One registered hypothesis: its statement and the analysis that tests it, over the test period
    from the project's confirmation start (to its `until`, if any). It is supported only when its
    adjusted p-value is below the family's level with the estimate in the registered direction."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    statement: str = Field(min_length=1)
    analysis: AnalysisDocument
    direction: Literal["greater", "less", "two_sided"]


class FamilyDocument(Model):
    """Hypotheses registered together as one frozen unit (Stage 5): they are tested together and
    their p-values adjusted together. Confirmatory registration is refused if the test period of any
    dataset in the project's scope has been read (amendment A2)."""

    kind: Literal["qrs/family"] = "qrs/family"
    version: Literal[1] = 1
    project: str = Field(pattern=RUN_ID)
    name: str = Field(min_length=1, max_length=200)
    confirmatory: bool
    alpha: float = Field(0.05, gt=0, lt=1, description="The family's significance level.")
    correction: Literal["holm", "bh", "by"] = Field("holm", description="Holm; Benjamini–Hochberg; or Benjamini–Yekutieli, where dependence may be negative.")
    hypotheses: list[Hypothesis] = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct_hypotheses(self):
        ids = [h.id for h in self.hypotheses]
        if len(set(ids)) < len(ids):
            raise ValueError(f"hypothesis IDs must be distinct: {ids}")
        return self


class TextBlock(Model):
    kind: Literal["text"]
    text: str = Field(description="Plain paragraphs, separated by blank lines; shown as text, never as markup.")


class FigureBlock(Model):
    kind: Literal["figure"]
    analysis: str = Field(pattern=RUN_ID)
    name: str


class TableBlock(Model):
    kind: Literal["table"]
    analysis: str = Field(pattern=RUN_ID)
    name: str


class ValuesBlock(Model):
    """An analysis's result: its values, warnings and assumptions."""

    kind: Literal["values"]
    analysis: str = Field(pattern=RUN_ID)


class FamilyBlock(Model):
    """A family's hypotheses, evaluations, adjusted p-values and conclusions."""

    kind: Literal["family"]
    family: str = Field(pattern=RUN_ID)


ReportBlock = Annotated[TextBlock | FigureBlock | TableBlock | ValuesBlock | FamilyBlock, Field(discriminator="kind")]


class ReportSection(Model):
    heading: str = Field(min_length=1, max_length=200)
    blocks: list[ReportBlock] = []


class ReportDocument(Model):
    """A report (Stage 5): a project's findings, generated from stored artefacts, never written by
    hand. Building it fixes its evidence at a point in the store's history."""

    kind: Literal["qrs/report"] = "qrs/report"
    version: Literal[1] = 1
    project: str = Field(pattern=RUN_ID)
    title: str = Field(min_length=1, max_length=200)
    sections: list[ReportSection] = Field(min_length=1)
