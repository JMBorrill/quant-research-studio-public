"""Immutable, content-addressed datasets.

A dataset is a directory <data_dir>/datasets/<hex>/ holding manifest.json and one data file:
bars.parquet for daily bars, observations.parquet for observations (Stage 4), or documents.parquet
for documents (Phase 4B). The manifest
records the data file's SHA-256, and the dataset ID is "sha256:" + the hash of the manifest's
canonical JSON. Loading re-verifies both hashes, so a dataset changed in place fails instead of
silently altering a past result. Files are published atomically and made read-only."""

import os
import shutil
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated

import numpy as np
import pandas as pd
import pandas_market_calendars
from pydantic import Field, TypeAdapter

from qrs.contracts import (
    EVIDENCE_CLASSES,
    DatasetManifest,
    DocumentsDeclaration,
    DocumentsManifest,
    ImportReport,
    Instrument,
    ObservationsDeclaration,
    ObservationsManifest,
)
from qrs.hashing import canonical_json, sha256_file, sha256_hex
from qrs.ports import Bars

COLUMNS = ["open", "high", "low", "close"]
DATASET_ID_PREFIX = "sha256:"
BARS, OBSERVATIONS, DOCUMENTS = "qrs/dataset", "qrs/observations", "qrs/documents"  # manifest kinds
KINDS = {BARS: "bars", OBSERVATIONS: "observations", DOCUMENTS: "documents"}
DATA_FILES = {BARS: "bars.parquet", OBSERVATIONS: "observations.parquet", DOCUMENTS: "documents.parquet"}
OBSERVATION_FIELDS = ["event", "published", "revision", "estimated"]  # then one column per value
DOCUMENT_FIELDS = ["document", "revision", "event", "published", "estimated", "title", "text", "text_sha256", "url"]
# How an import report names the exchange calendar its sessions were checked against.
CALENDAR_LIBRARY = " (pandas_market_calendars "
Manifest = DatasetManifest | ObservationsManifest | DocumentsManifest
MANIFEST = TypeAdapter(Annotated[Manifest, Field(discriminator="kind")])


class DatasetError(ValueError):
    pass


def session_problems(frame: pd.DataFrame, instrument: Instrument) -> dict[str, np.ndarray]:
    """The positions of the sessions breaking each per-session rule, keyed by the rule. Shared by
    check_bars and the CSV importer, which reports them by row. Missing or non-finite prices break
    none of these rules; they are reported separately."""
    index, values = frame.index, frame[COLUMNS].to_numpy()
    values = np.where(np.isfinite(values), values, np.nan)
    o, h, low, c = values.T
    ticks = values / instrument.tick_size
    rules = {
        "sessions must be dates without a time of day": index != index.normalize(),
        "sessions must be unique and in increasing order": np.r_[False, index[1:] <= index[:-1]],
        "prices must be positive": (values <= 0).any(axis=1),
        "high must be >= open and close, and low <= open and close": (h < np.maximum(o, c)) | (low > np.minimum(o, c)),
        f"prices must be multiples of the tick size {instrument.tick_size}": (np.abs(ticks - np.round(ticks)) > 1e-6).any(axis=1),
    }
    return {rule: np.flatnonzero(broken) for rule, broken in rules.items() if broken.any()}


def check_bars(frame: pd.DataFrame, instrument: Instrument) -> None:
    """Raise DatasetError listing every way `frame` is not valid canonical daily bars."""
    problems = []
    index = frame.index
    if list(frame.columns) != COLUMNS:
        problems.append(f"columns must be exactly {COLUMNS}, got {list(frame.columns)}")
    elif not all(pd.api.types.is_float_dtype(frame[c]) for c in COLUMNS):
        problems.append("prices must be floating-point numbers")
    if not isinstance(index, pd.DatetimeIndex) or index.tz is not None:
        problems.append("index must be timezone-naive session dates")
    if len(frame) < 2:
        problems.append("at least two sessions are required")
    if problems:
        raise DatasetError("Invalid bars: " + "; ".join(problems) + ".")

    missing = ~np.isfinite(frame[COLUMNS].to_numpy()).all(axis=1)
    if missing.any():
        problems.append(f"{int(missing.sum())} session(s) have missing or non-finite prices")
    for rule, positions in session_problems(frame, instrument).items():
        problems.append(f"{rule} ({len(positions)} session(s), first {index[positions[0]].date()})")
    if instrument.continuous is None and index.max().date() >= instrument.expiry:
        problems.append(f"data runs to {index.max().date()} but the contract expires {instrument.expiry}")
    if instrument.continuous is not None:
        problems += continuous_problems(index, instrument.continuous, frame)
    if problems:
        raise DatasetError("Invalid bars: " + "; ".join(problems) + ".")


def continuous_problems(sessions: pd.DatetimeIndex, series, frame: pd.DataFrame) -> list[str]:
    """Why a continuous series' contracts do not cover its sessions exactly once, in order, each from
    and to one of its sessions, or would have had prices of zero or less as traded (the series'
    prices less their adjustment). A boundary off the sessions would misstate the roll session."""
    covered = np.zeros(len(sessions), dtype=int)
    negative = []
    known = {s.date() for s in sessions}
    off = [c.symbol for c in series.contracts if c.first_session not in known or c.last_session not in known]
    for contract in series.contracts:
        used = (sessions >= pd.Timestamp(contract.first_session)) & (sessions <= pd.Timestamp(contract.last_session))
        covered += used
        if (frame.loc[used, COLUMNS] - contract.adjustment <= 0).any(axis=None):
            negative.append(contract.symbol)
    problems = []
    if (covered == 0).any():
        problems.append(f"{int((covered == 0).sum())} session(s) belong to no contract, first {sessions[covered == 0][0].date()}")
    if (covered > 1).any():
        problems.append(f"{int((covered > 1).sum())} session(s) belong to more than one contract")
    if negative:
        problems.append(f"the adjustments leave contracts priced at zero or less as traded: {', '.join(negative)}")
    if off:
        problems.append(f"contracts must start and end on sessions of the series: {', '.join(off)}")
    first, last = series.contracts[0].first_session, series.contracts[-1].last_session
    if first != sessions[0].date() or last != sessions[-1].date():
        problems.append(f"the contracts cover {first} to {last}, but the sessions run from {sessions[0].date()} to {sessions[-1].date()}")
    return problems


def roll_sessions(bars: Bars) -> list[str]:
    """The sessions on which a continuous series moves to its next contract; none for a contract."""
    series = bars.instrument.continuous
    return [c.first_session.isoformat() for c in series.contracts[1:]] if series else []


def save(
    data_dir: Path,
    frame: pd.DataFrame,
    *,
    name: str,
    instrument: Instrument,
    evidence_class: str,
    source: str,
    redistributable: bool,
    import_report: ImportReport | None = None,
) -> str:
    """Validate and publish bars; return the dataset ID. Saving identical content again is a no-op."""
    frame = frame.rename_axis("session")
    check_bars(frame, instrument)
    if instrument.continuous is not None and evidence_class == "point_in_time":
        # Adjusted levels embed later roll gaps, so no continuous series was known as it stands then.
        raise DatasetError("A continuous series is retrospective at best: its adjusted prices embed later rolls.")
    return publish(
        data_dir,
        DATA_FILES[BARS],
        frame.to_parquet,
        lambda data_sha256: DatasetManifest(
            name=name,
            instrument=instrument,
            evidence_class=evidence_class,
            source=source,
            redistributable=redistributable,
            rows=len(frame),
            start=frame.index[0].date(),
            end=frame.index[-1].date(),
            data_sha256=data_sha256,
            import_report=import_report,
        ),
    )


def save_observations(data_dir: Path, frame: pd.DataFrame, declaration: ObservationsDeclaration, source_sha256: str) -> str:
    """Publish observations checked by the importer; return the dataset ID."""
    return publish(
        data_dir,
        DATA_FILES[OBSERVATIONS],
        frame.to_parquet,
        lambda data_sha256: ObservationsManifest(
            name=declaration.name,
            values=declaration.values,
            evidence_class=declaration.evidence_class,
            source=declaration.source,
            redistributable=declaration.redistributable,
            estimates=declaration.estimates,
            rows=len(frame),
            estimated_rows=int(frame["estimated"].sum()),
            data_sha256=data_sha256,
            source_sha256=source_sha256,
            columns=declaration.columns,
            event_format=declaration.event_format,
        ),
    )


def save_documents(data_dir: Path, frame: pd.DataFrame, declaration: DocumentsDeclaration, source_sha256: str) -> str:
    """Publish documents checked by the importer; return the dataset ID."""
    return publish(
        data_dir,
        DATA_FILES[DOCUMENTS],
        frame.to_parquet,
        lambda data_sha256: DocumentsManifest(
            name=declaration.name,
            evidence_class=declaration.evidence_class,
            source=declaration.source,
            redistributable=declaration.redistributable,
            hosted_processing=declaration.hosted_processing,
            estimates=declaration.estimates,
            rows=len(frame),
            documents=frame["document"].nunique(),
            estimated_rows=int(frame["estimated"].sum()),
            data_sha256=data_sha256,
            source_sha256=source_sha256,
            columns=declaration.columns,
            event_format=declaration.event_format,
        ),
    )


def save_features(data_dir: Path, frame: pd.DataFrame, files: dict[str, bytes], manifest_for: Callable[[str], ObservationsManifest]) -> str:
    """Publish a feature dataset: observations, with extraction.json, responses.jsonl and
    documents.json beside them, whose hashes its manifest's derivation records; return the dataset ID."""

    def write(path: Path) -> None:
        frame.to_parquet(path)
        for name, content in files.items():
            (path.parent / name).write_bytes(content)

    return publish(data_dir, DATA_FILES[OBSERVATIONS], write, manifest_for)


def publish(data_dir: Path, data_file: str, write: Callable[[Path], None], manifest_for: Callable[[str], Manifest]) -> str:
    """Write a dataset's data file, then its manifest given the file's SHA-256, into a new directory
    published atomically and read-only; return the dataset ID."""
    root = data_dir / "datasets"
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=root, prefix=".tmp-"))
    try:
        write(tmp / data_file)
        manifest = manifest_for(sha256_file(tmp / data_file))
        # Leaving out absent optional fields keeps the IDs of datasets that predate them.
        manifest_bytes = canonical_json(manifest.model_dump(mode="json", exclude_none=True))
        (tmp / "manifest.json").write_bytes(manifest_bytes)
        digest = sha256_hex(manifest_bytes)
        final = root / digest
        for f in tmp.iterdir():
            f.chmod(0o444)
        try:
            os.rename(tmp, final)
        except OSError:
            if not final.exists():
                raise
            shutil.rmtree(tmp)  # already published, perhaps by a concurrent save of the same content
        return DATASET_ID_PREFIX + digest
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def directory(data_dir: Path, dataset_id: str) -> Path:
    if not dataset_id.startswith(DATASET_ID_PREFIX):
        raise DatasetError(f"Dataset ID must start with '{DATASET_ID_PREFIX}': {dataset_id!r}")
    return data_dir / "datasets" / dataset_id.removeprefix(DATASET_ID_PREFIX)


def read_manifest(data_dir: Path, dataset_id: str) -> Manifest:
    path = directory(data_dir, dataset_id) / "manifest.json"
    if not path.exists():
        raise DatasetError(f"Dataset {dataset_id} not found in {data_dir}.")
    raw = path.read_bytes()
    if DATASET_ID_PREFIX + sha256_hex(raw) != dataset_id:
        raise DatasetError(f"Dataset {dataset_id} manifest was changed after publication.")
    return MANIFEST.validate_json(raw)


def verified(data_dir: Path, dataset_id: str, kind: str) -> tuple[Manifest, pd.DataFrame]:
    """A dataset's manifest and data, which must be of the given kind and unchanged since publication."""
    manifest = read_manifest(data_dir, dataset_id)
    if manifest.kind != kind:
        raise DatasetError(f"Dataset {dataset_id} holds {KINDS[manifest.kind]}, not {KINDS[kind]}.")
    data = directory(data_dir, dataset_id) / DATA_FILES[kind]
    if sha256_file(data) != manifest.data_sha256:
        raise DatasetError(f"Dataset {dataset_id} data was changed after publication (checksum mismatch).")
    derivation = getattr(manifest, "derivation", None)
    if derivation is not None:
        digests = {
            "extraction.json": derivation.extraction_sha256,
            "responses.jsonl": derivation.responses_sha256,
            "documents.json": derivation.documents.removeprefix(DATASET_ID_PREFIX),
        }
        for name, digest in digests.items():
            path = directory(data_dir, dataset_id) / name
            if not path.exists() or sha256_file(path) != digest:
                raise DatasetError(f"Dataset {dataset_id} {name} is missing or was changed after publication.")
    return manifest, pd.read_parquet(data)


def load(data_dir: Path, dataset_id: str) -> Bars:
    manifest, frame = verified(data_dir, dataset_id, BARS)
    check_bars(frame, manifest.instrument)
    return Bars(frame=frame, instrument=manifest.instrument, dataset_id=dataset_id, calendar=bars_calendar(manifest))


def load_observations(data_dir: Path, dataset_id: str) -> pd.DataFrame:
    """Every row of an observations dataset: event, published (UTC), revision, estimated, then, for
    a feature dataset, the document, then the values."""
    return verified(data_dir, dataset_id, OBSERVATIONS)[1]


def value_columns(rows: pd.DataFrame) -> list[str]:
    """An observations frame's value columns: all but the event, publication, revision and estimate
    columns, and a feature dataset's document column."""
    return [c for c in rows.columns if c not in OBSERVATION_FIELDS and c != "document"]


def weakest(classes) -> str:
    """The weakest of some evidence classes (synthetic, exploratory, retrospective, point_in_time)."""
    return min(classes, key=EVIDENCE_CLASSES.index)


def load_documents(data_dir: Path, dataset_id: str) -> tuple[DocumentsManifest, pd.DataFrame]:
    """A documents dataset's manifest, and every document version: document, revision, event,
    published (UTC), estimated, title, text, text_sha256, url."""
    return verified(data_dir, dataset_id, DOCUMENTS)


def bars_calendar(manifest: DatasetManifest) -> str | None:
    """The exchange calendar the bars were imported against; None if they have none (synthetic
    bars, or bars checked against weekdays only)."""
    report = manifest.import_report
    return report.calendar.split(CALENDAR_LIBRARY)[0] if report and CALENDAR_LIBRARY in report.calendar else None


def session_closes(bars: Bars) -> pd.Series:
    """Each session's close as a UTC timestamp, early closes included, from the bars' exchange
    calendar: the moment by which a decision at that session is made."""
    if bars.calendar is None:
        raise DatasetError("These bars have no exchange calendar, so their sessions' closes are unknown; import them with one.")
    sessions = bars.frame.index
    schedule = pandas_market_calendars.get_calendar(bars.calendar).schedule(sessions[0], sessions[-1])
    closes = schedule["market_close"].reindex(sessions)
    if closes.isna().any():
        raise DatasetError(f"{bars.calendar} has no session on {closes.index[closes.isna()][0].date()}, so its close is unknown.")
    return closes


def list_manifests(data_dir: Path) -> dict[str, Manifest]:
    """Valid datasets by ID. Entries that fail verification are left out; a strategy that refers to
    one fails with the verification error when it runs."""
    root = data_dir / "datasets"
    if not root.exists():
        return {}
    manifests = {}
    for path in sorted(root.iterdir()):
        if path.is_dir() and not path.name.startswith("."):
            try:
                manifests[DATASET_ID_PREFIX + path.name] = read_manifest(data_dir, DATASET_ID_PREFIX + path.name)
            except DatasetError:
                pass
    return manifests


SYNTHETIC_INSTRUMENT = Instrument(symbol="SYN", currency="USD", multiplier=50, tick_size=0.25, margin_init=0.10, margin_maint=0.05, expiry=date(2030, 12, 31))


def synthetic_bars(seed: int, sessions: int, start: str = "2024-01-02", tick_size: float = 0.25) -> pd.DataFrame:
    """Deterministic daily OHLC: a random walk whose drift switches sign every 40 sessions, so
    moving-average crossovers occur. Weekdays only; no holidays are modelled."""
    rng = np.random.default_rng(seed)
    drift = np.where((np.arange(sessions) // 40) % 2 == 0, 0.004, -0.004)
    closes = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.012, sessions)))
    opens = np.concatenate([[100.0], closes[:-1]]) * np.exp(rng.normal(0, 0.003, sessions))
    wick = np.abs(rng.normal(0, 0.006, sessions)) * closes
    frame = pd.DataFrame(
        {"open": opens, "high": np.maximum(opens, closes) + wick, "low": np.minimum(opens, closes) - wick, "close": closes},
        index=pd.bdate_range(start, periods=sessions, name="session"),
    )
    on_grid = (frame / tick_size).round() * tick_size
    # Rounding can move open/close outside the rounded wick; widen the wick to keep bars consistent.
    on_grid["high"] = on_grid[["open", "high", "close"]].max(axis=1)
    on_grid["low"] = on_grid[["open", "low", "close"]].min(axis=1)
    return on_grid


def create_synthetic(data_dir: Path, seed: int = 42, sessions: int = 500) -> str:
    return save(
        data_dir,
        synthetic_bars(seed, sessions, tick_size=SYNTHETIC_INSTRUMENT.tick_size),
        name=f"Synthetic SYN daily bars (seed {seed})",
        instrument=SYNTHETIC_INSTRUMENT,
        evidence_class="synthetic",
        source=f"qrs.datasets.synthetic_bars(seed={seed}, sessions={sessions})",
        redistributable=True,
    )
