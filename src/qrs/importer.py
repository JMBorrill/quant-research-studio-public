"""CSV import: the one path from a user's file of daily bars, observations or documents to an
ordinary immutable dataset.

The file must hold every price: rows that are unparsable, missing a price, out of order, or that
break a bar rule are rejected by row number (the header is row 1), and nothing is ever filled.
Expected sessions the file lacks are reported in the manifest's import report, not filled.
Observations and documents are rejected the same way, by row, and each needs a publication
timestamp with an explicit UTC offset (Stage 4 decision 9)."""

import csv
import io
import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

import pandas as pd
import pandas_market_calendars

from qrs import datasets
from qrs.contracts import OBSERVATION_COLUMNS, DocumentsDeclaration, ImportDeclaration, ImportReport, ObservationsDeclaration
from qrs.datasets import COLUMNS, DOCUMENT_FIELDS, OBSERVATION_FIELDS, DatasetError
from qrs.hashing import sha256_hex

WEEKDAYS = "weekdays (approximate)"
SCALE = {"currency": 1, "cents": 100}
DECIMAL = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)")
MAX_LISTED_PROBLEMS = 20
# A document's text can be long (a full report): allow a field as large as the upload limit. The csv
# module's limit is process-wide; nothing else here reads CSV.
MAX_FIELD_CHARS = 10 * 1024 * 1024
csv.field_size_limit(MAX_FIELD_CHARS)
SETTLEMENT_ONLY = "a settlement-only file cannot be imported, since OHLC is never fabricated"


def import_csv(data_dir: Path, content: bytes, declaration: ImportDeclaration) -> str:
    """Validate the file against the declaration, publish it as a dataset and return its ID."""
    mapping = declaration.columns
    if len({mapping.open, mapping.high, mapping.low, mapping.close}) < 4:
        raise DatasetError(f"Open, high, low and close must be four different columns: {SETTLEMENT_ONLY}.")
    calendar = expected_sessions_source(declaration.calendar)
    table, rows, malformed = read_columns(content, mapping.model_dump())
    frame, problems = parse(table, rows, declaration)
    if malformed or problems:
        raise DatasetError(rejection(malformed + problems))
    datasets.check_bars(frame, declaration.instrument)  # the file-wide rules: enough sessions, ending before expiry
    if (frame.nunique(axis=1) == 1).all():
        raise DatasetError(f"Every row has open = high = low = close: {SETTLEMENT_ONLY}.")
    report = ImportReport(
        source_sha256=sha256_hex(content),
        columns=mapping,
        date_format=declaration.date_format,
        price_unit=declaration.price_unit,
        calendar=calendar,
        missing_sessions=missing_sessions(frame.index, declaration.calendar),
    )
    return datasets.save(
        data_dir,
        frame,
        name=declaration.name,
        instrument=declaration.instrument,
        evidence_class=declaration.evidence_class,
        source=declaration.source,
        redistributable=declaration.redistributable,
        import_report=report,
    )


def read_columns(content: bytes, mapping: dict[str, str], exact: tuple[str, ...] = ()) -> tuple[pd.DataFrame, list[int], list[tuple[int, str]]]:
    """The columns named in `mapping` (field -> header) as text, stripped except for the fields in
    `exact`; each record's row number in the file, that of its first line; and the rows with more
    values than the header has columns. Those are left out: an unquoted thousands separator, for
    example, would shift every later price into the wrong column."""
    try:
        text = content.decode("utf-8-sig")  # spreadsheets often start a UTF-8 file with a byte-order mark
    except UnicodeDecodeError as e:
        raise DatasetError(f"The file is not UTF-8 text (invalid byte at offset {e.start}).") from None
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = [name.strip() for name in next(reader, [])]
        unclear = [name for name in mapping.values() if header.count(name) != 1]
        if unclear:
            shown = [name[:80] for name in header[:50]]  # a field may be megabytes long: never echo it whole
            raise DatasetError(f"Columns {unclear} must each appear exactly once in the header, which is {shown}{' ...' if len(header) > 50 else ''}.")
        positions = {field: header.index(name) for field, name in mapping.items()}
        records, rows, malformed = [], [], []
        first_line = reader.line_num + 1
        for record in reader:
            row, first_line = first_line, reader.line_num + 1  # a quoted field can span lines
            if not any(cell.strip() for cell in record):
                continue  # a blank line, still counted in the row numbers
            if any(cell.strip() for cell in record[len(header) :]):
                malformed.append((row, f"{len(record)} values but the header has {len(header)} columns"))
                continue
            cells = [record[i] if i < len(record) else "" for i in positions.values()]
            records.append([cell if field in exact else cell.strip() for field, cell in zip(positions, cells, strict=True)])
            rows.append(row)
    except csv.Error as e:
        raise DatasetError(f"Row {reader.line_num} is not valid CSV: {e}.") from None
    return pd.DataFrame(records, columns=list(positions), dtype=object), rows, malformed


def parse(table: pd.DataFrame, rows: list[int], declaration: ImportDeclaration) -> tuple[pd.DataFrame, list[tuple[int, str]]]:
    """The bars the rows describe, and every problem as (row, message), in column order within a
    row. Rows without a valid date are left out of the session rules; missing prices break none."""
    dates, problems = parse_dates(table, "date", declaration.date_format, rows)
    prices = {}
    for field in COLUMNS:
        prices[field] = [price(text, SCALE[declaration.price_unit]) for text in table[field]]
        for i, value in enumerate(prices[field]):
            if value is None:
                problems.append((rows[i], missing_or(table[field][i], f"{field} {table[field][i]!r} is not a number", field)))
    frame = pd.DataFrame(prices, index=pd.DatetimeIndex(dates, name="session"), dtype=float)
    dated = frame.index.notna()
    dated_rows = [row for row, ok in zip(rows, dated, strict=True) if ok]
    for rule, positions in datasets.session_problems(frame[dated], declaration.instrument).items():
        problems.extend((dated_rows[p], rule) for p in positions)
    return frame, problems


def parse_dates(table: pd.DataFrame, field: str, date_format: str, rows: list[int]) -> tuple[pd.Series, list[tuple[int, str]]]:
    """The field's dates, and a problem for each row whose text is not one in the format."""
    try:
        dates = pd.to_datetime(table[field], format=date_format, errors="coerce")
    except ValueError as e:  # an invalid format, such as an unknown directive
        raise DatasetError(f"Invalid {field} format {date_format!r}: {e}.") from None
    texts = table[field]
    return dates, [(rows[i], missing_or(texts[i], f"{field} {texts[i]!r} does not match the format {date_format!r}", field)) for i in dates.index[dates.isna()]]


def price(text: str, scale: int) -> float | None:
    """The price in currency units, or None unless the text is a plain decimal number. Decimal
    division is exact, so a price in cents and the same price in currency units give the same float."""
    return float(Decimal(text) / scale) if DECIMAL.fullmatch(text) else None


def missing_or(text: str, unparsable: str, field: str) -> str:
    return f"{field} is missing" if text == "" else unparsable


def rejection(problems: list[tuple[int, str]]) -> str:
    """The problems in row order (stable, so a row's keep their column order)."""
    problems = sorted(problems, key=lambda problem: problem[0])
    listed = [f"row {row}: {message}" for row, message in problems[:MAX_LISTED_PROBLEMS]]
    if len(problems) > MAX_LISTED_PROBLEMS:
        listed.append(f"... and {len(problems) - MAX_LISTED_PROBLEMS} more")
    return "The file was rejected:\n" + "\n".join(listed)


def expected_sessions_source(calendar: str | None) -> str:
    if calendar is None:
        return WEEKDAYS
    if calendar not in pandas_market_calendars.get_calendar_names():
        raise DatasetError(f"Unknown exchange calendar {calendar!r}; declare no calendar to check against weekdays (approximate).")
    return f"{calendar}{datasets.CALENDAR_LIBRARY}{version('pandas_market_calendars')})"


def missing_sessions(sessions: pd.DatetimeIndex, calendar: str | None) -> list[date]:
    """Every session the calendar expects between the first and last of `sessions` that is absent."""
    first, last = sessions[0], sessions[-1]
    if calendar is None:
        expected = pd.bdate_range(first, last)
    else:
        expected = pandas_market_calendars.get_calendar(calendar).valid_days(first, last).tz_localize(None)
    return [day.date() for day in expected.difference(sessions)]


DECLARATIONS = {"qrs/observations": ObservationsDeclaration, "qrs/documents": DocumentsDeclaration}


def parse_declaration(raw: str | bytes) -> ImportDeclaration | ObservationsDeclaration | DocumentsDeclaration:
    """An import declaration: observations or documents when its "kind" says so, otherwise bars. An
    invalid one raises pydantic's ValidationError from that model alone."""
    try:
        kind = json.loads(raw).get("kind")
    except (ValueError, AttributeError):
        kind = None  # the bars model then reports what is wrong
    return DECLARATIONS.get(kind, ImportDeclaration).model_validate_json(raw)


def import_observations(data_dir: Path, content: bytes, declaration: ObservationsDeclaration) -> str:
    """Validate a file of observations against the declaration, publish it and return its ID. A file
    with any estimated publication timestamp must say how they were estimated, and cannot be
    declared point_in_time."""
    named = {field: header for field, header in declaration.columns.model_dump().items() if header is not None}
    headers = [*named.values(), *declaration.values]
    if len(set(headers)) < len(headers):
        raise DatasetError(f"Each declared column must be a different header: {headers}.")
    if set(declaration.values) & set(OBSERVATION_COLUMNS):
        raise DatasetError(f"A value column cannot be named {', '.join(OBSERVATION_COLUMNS)}.")
    table, rows, malformed = read_columns(content, {**named, **{f"value:{name}": name for name in declaration.values}})
    frame, problems = parse_observations(table, rows, declaration)
    if malformed or problems:
        raise DatasetError(rejection(malformed + problems))
    if frame.empty:
        raise DatasetError("The file has no observations.")
    check_estimates(rows, frame["estimated"], declaration)
    frame = frame.sort_values(["event", "published"], kind="stable", ignore_index=True)
    return datasets.save_observations(data_dir, frame, declaration, sha256_hex(content))


def check_estimates(rows: list[int], flags: pd.Series, declaration: ObservationsDeclaration | DocumentsDeclaration) -> None:
    """A file with any estimated publication timestamp must say how they were estimated, and
    cannot be declared point_in_time; one that says so must have some."""
    estimated = [row for row, flag in zip(rows, flags, strict=True) if flag]
    if estimated and declaration.evidence_class == "point_in_time":
        raise DatasetError(
            f"{len(estimated)} row(s) have estimated publication timestamps (first row {estimated[0]}), so the data is retrospective, not point_in_time."
        )
    if estimated and declaration.estimates is None:
        raise DatasetError(f"{len(estimated)} row(s) have estimated publication timestamps (first row {estimated[0]}): declare how they were estimated.")
    if declaration.estimates is not None and not estimated:
        raise DatasetError("The declaration says how timestamps were estimated, but no row is marked estimated.")


def parse_observations(table: pd.DataFrame, rows: list[int], declaration: ObservationsDeclaration) -> tuple[pd.DataFrame, list[tuple[int, str]]]:
    """The observations the rows describe, and every problem as (row, message)."""
    events, problems = parse_dates(table, "event", declaration.event_format, rows)
    published = []
    for row, text in zip(rows, table["published"], strict=True):
        moment, problem = publication(text)
        published.append(moment)
        if problem:
            problems.append((row, problem))
    flags, flag_problems = estimate_flags(table, rows)
    problems.extend(flag_problems)
    values = {}
    for name in declaration.values:
        column = table[f"value:{name}"]
        values[name] = [price(text, 1) for text in column]
        problems.extend((rows[i], missing_or(column[i], f"{name} {column[i]!r} is not a number", name)) for i, v in enumerate(values[name]) if v is None)
    first_row = {}
    for row, key in zip(rows, zip(events, published, strict=True), strict=True):
        if pd.notna(key[0]) and key[1] is not None:
            if key in first_row:
                problems.append((row, f"repeats the event and publication time of row {first_row[key]}"))
            first_row.setdefault(key, row)
    frame = pd.DataFrame(
        {
            "event": events,
            "published": pd.DatetimeIndex(published, tz="UTC"),
            "revision": table["revision"] if "revision" in table else "",
            "estimated": flags,
            **{name: pd.Series(v, dtype=float) for name, v in values.items()},
        }
    )
    return frame[OBSERVATION_FIELDS + list(declaration.values)], problems


def estimate_flags(table: pd.DataFrame, rows: list[int]) -> tuple[pd.Series, list[tuple[int, str]]]:
    """Each row's estimated-timestamp flag (false without the column), and a problem for each row
    whose flag is not true or false."""
    texts = table["estimated"].str.lower() if "estimated" in table else pd.Series("false", index=table.index)
    problems = [(row, f"estimated must be true or false, not {text!r}") for row, text in zip(rows, texts, strict=True) if text not in ("true", "false")]
    return texts == "true", problems


def publication(text: str) -> tuple[pd.Timestamp | None, str | None]:
    """A publication timestamp in UTC, or the reason the text is not one: it must be ISO 8601 with
    an explicit offset, since a naive time could be any time zone's."""
    if text == "":
        return None, "published is missing"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None, f"published {text!r} is not an ISO 8601 timestamp"
    if moment.tzinfo is None:
        return None, f"published {text!r} has no UTC offset"
    try:
        return pd.Timestamp(moment.astimezone(UTC)), None
    except OverflowError:  # such as 9999-12-31T23:59:59-05:00, which is in the year 10000 in UTC
        return None, f"published {text!r} is out of range"


def import_documents(data_dir: Path, content: bytes, declaration: DocumentsDeclaration) -> str:
    """Validate a file of documents, such as news or reports, against the declaration, publish it
    and return its ID. Each row is one version of a document; estimates follow the observations'
    rules."""
    columns = declaration.columns
    if (columns.event is None) != (declaration.event_format is None):
        raise DatasetError("Declare an event column and its event_format together, or neither.")
    named = {field: header for field, header in columns.model_dump().items() if header is not None}
    if len(set(named.values())) < len(named):
        raise DatasetError(f"Each declared column must be a different header: {list(named.values())}.")
    table, rows, malformed = read_columns(content, named, exact=("text",))
    frame, problems = parse_documents(table, rows, declaration)
    if malformed or problems:
        raise DatasetError(rejection(malformed + problems))
    if frame.empty:
        raise DatasetError("The file has no documents.")
    check_estimates(rows, frame["estimated"], declaration)
    frame = frame.sort_values(["published", "document"], kind="stable", ignore_index=True)
    return datasets.save_documents(data_dir, frame, declaration, sha256_hex(content))


def parse_documents(table: pd.DataFrame, rows: list[int], declaration: DocumentsDeclaration) -> tuple[pd.DataFrame, list[tuple[int, str]]]:
    """The document versions the rows describe, and every problem as (row, message). A version is
    identified by its document and revision, and no two versions of a document share a
    publication time, so which version was the latest at any moment is never ambiguous."""
    problems = [(row, "id is missing") for row, text in zip(rows, table["id"], strict=True) if text == ""]
    published = []
    for row, text in zip(rows, table["published"], strict=True):
        moment, problem = publication(text)
        published.append(moment)
        if problem:
            problems.append((row, problem))
    problems.extend((row, "text is missing") for row, text in zip(rows, table["text"], strict=True) if not text.strip())
    flags, flag_problems = estimate_flags(table, rows)
    problems.extend(flag_problems)
    published = pd.DatetimeIndex(published, tz="UTC")
    if declaration.event_format is None:
        events = pd.Series(published.tz_localize(None).normalize(), index=table.index)
    else:
        events, event_problems = parse_dates(table, "event", declaration.event_format, rows)
        problems.extend(event_problems)
    revisions = table["revision"] if "revision" in table else pd.Series("", index=table.index)
    repeated = "the document and revision" if "revision" in table else "the document (each version needs a revision label)"
    for key, what in ((revisions, repeated), (pd.Series(published, index=table.index), "the document and publication time")):
        first_row = {}
        for row, document, other in zip(rows, table["id"], key, strict=True):
            if document != "" and pd.notna(other):
                if (document, other) in first_row:
                    problems.append((row, f"repeats {what} of row {first_row[document, other]}"))
                first_row.setdefault((document, other), row)
    optional = {name: table[name] if name in table else pd.Series("", index=table.index) for name in ("title", "url")}
    frame = pd.DataFrame(
        {
            "document": table["id"],
            "revision": revisions,
            "event": events,
            "published": published,
            "estimated": flags,
            "title": optional["title"],
            "text": table["text"],
            "text_sha256": [sha256_hex(text.encode()) for text in table["text"]],
            "url": optional["url"],
        }
    )
    return frame[DOCUMENT_FIELDS], problems
