"""CSV import: row-numbered rejections, price units, missing-session reports, the upload limit and
CLI/app parity."""

import json
import re
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from conftest import IMPORT_DECLARATION, bars_csv, ohlc
from qrs import compiler, datasets, templates
from qrs.api import MAX_UPLOAD_BYTES, create_app
from qrs.cli import main
from qrs.contracts import ImportDeclaration
from qrs.datasets import DatasetError, synthetic_bars
from qrs.hashing import sha256_hex
from qrs.importer import import_csv
from qrs.runtime import execute

HEADER = "Date,Open,High,Low,Close"
ROWS = ["2026-01-05,100.00,101.00,99.50,100.50", "2026-01-06,101.00,102.50,100.50,102.00", "2026-01-07,102.00,104.50,101.50,104.00"]


def declaration(**changes) -> ImportDeclaration:
    return ImportDeclaration.model_validate({**IMPORT_DECLARATION, **changes})


def csv_file(*rows: str, header: str = HEADER) -> bytes:
    return "\n".join([header, *rows, ""]).encode()


def published(data_dir) -> list[str]:
    return list(datasets.list_manifests(data_dir))


@pytest.mark.parametrize(
    "row, changes, message",
    [
        ("2026-13-06,101.00,102.50,100.50,102.00", {}, "row 3: date '2026-13-06' does not match the format '%Y-%m-%d'"),
        ("06/01/2026,101.00,102.50,100.50,102.00", {}, "row 3: date '06/01/2026' does not match the format '%Y-%m-%d'"),
        ("2026-01-06,abc,102.50,100.50,102.00", {}, "row 3: open 'abc' is not a number"),
        ("2026-01-06,101.00,102.50,100.50,NaN", {}, "row 3: close 'NaN' is not a number"),
        ("2026-01-06,1_01.00,102.50,100.50,102.00", {}, "row 3: open '1_01.00' is not a number"),
        ("2026-01-06,1.01e2,102.50,100.50,102.00", {}, "row 3: open '1.01e2' is not a number"),
        ("2026-01-06,1,010.00,1,025.00,1,005.00,1,020.00", {}, "row 3: 9 values but the header has 5 columns"),  # unquoted thousands
        (",101.00,102.50,100.50,102.00", {}, "row 3: date is missing"),
        ("2026-01-06,101.00,,100.50,102.00", {}, "row 3: high is missing"),
        ("2026-01-06,101.00,102.50,100.50", {}, "row 3: close is missing"),
        ("2026-01-05,101.00,102.50,100.50,102.00", {}, "row 3: sessions must be unique and in increasing order"),
        ("2026-01-02,101.00,102.50,100.50,102.00", {}, "row 3: sessions must be unique and in increasing order"),
        ("2026-01-06,101.00,101.75,100.50,102.00", {}, "row 3: high must be >= open and close, and low <= open and close"),
        ("2026-01-06,101.00,102.50,101.50,102.00", {}, "row 3: high must be >= open and close, and low <= open and close"),
        ("2026-01-06,101.10,102.50,100.50,102.00", {}, "row 3: prices must be multiples of the tick size 0.25"),
        ("2026-01-06,-1.00,102.50,-1.00,102.00", {}, "row 3: prices must be positive"),
        ("2026-01-06 16:00,101.00,102.50,100.50,102.00", {"date_format": "%Y-%m-%d %H:%M"}, "row 3: sessions must be dates without a time of day"),
    ],
)
def test_each_rejection_names_its_row_and_publishes_nothing(tmp_path, row, changes, message):
    rows = [ROWS[0], row, ROWS[2]]
    if changes:  # every row in the declared format
        rows = [ROWS[0].replace("2026-01-05", "2026-01-05 00:00"), row, ROWS[2].replace("2026-01-07", "2026-01-07 00:00")]
    with pytest.raises(DatasetError, match=re.escape(message)):
        import_csv(tmp_path, csv_file(*rows), declaration(**changes))
    assert published(tmp_path) == []


def test_every_problem_is_listed_in_row_order_up_to_a_limit(tmp_path):
    sessions = pd.bdate_range("2026-01-05", periods=30)
    rows = [f"{day:%Y-%m-%d},100.10,101.00,99.50,100.50" for day in sessions]  # every open is off the tick grid
    rows[1] = "2026-01-05,100.10,,99.50,100.50"  # row 3 also repeats row 2's date and lacks its high
    with pytest.raises(DatasetError) as rejected:
        import_csv(tmp_path, csv_file(*rows), declaration())
    lines = str(rejected.value).splitlines()
    assert lines[0] == "The file was rejected:"
    off_grid = "prices must be multiples of the tick size 0.25"
    repeated = "sessions must be unique and in increasing order"
    assert lines[1:6] == [f"row 2: {off_grid}", "row 3: high is missing", f"row 3: {repeated}", f"row 3: {off_grid}", f"row 4: {off_grid}"]
    assert len(lines) == 22 and lines[-2].startswith("row 19: ") and lines[-1] == "... and 12 more"


def test_problems_within_a_row_follow_the_columns(tmp_path):
    with pytest.raises(DatasetError, match="row 3: date is missing\nrow 3: open 'x' is not a number\nrow 3: close is missing"):
        import_csv(tmp_path, csv_file(ROWS[0], ",x,102.50,100.50,", ROWS[2]), declaration())


def test_date_formats_must_be_strptime_formats():
    # pandas reads "mixed" by guessing each date, which would silently swap days and months.
    for value in ("mixed", "ISO8601", ""):
        with pytest.raises(ValidationError, match="date_format"):
            declaration(date_format=value)


def test_settlement_only_files_are_rejected_since_ohlc_is_never_fabricated(tmp_path):
    settle = csv_file("2026-01-05,100.50", "2026-01-06,102.00", header="Date,Settle")
    one_column = {"date": "Date", "open": "Settle", "high": "Settle", "low": "Settle", "close": "Settle"}
    with pytest.raises(DatasetError, match="must be four different columns: a settlement-only file cannot be imported"):
        import_csv(tmp_path, settle, declaration(columns=one_column))
    filled = csv_file("2026-01-05,100.50,100.50,100.50,100.50", "2026-01-06,102.00,102.00,102.00,102.00")
    with pytest.raises(DatasetError, match="Every row has open = high = low = close: a settlement-only file"):
        import_csv(tmp_path, filled, declaration())
    assert published(tmp_path) == []
    # A flat session among real bars is ordinary data.
    assert import_csv(tmp_path, csv_file(ROWS[0], "2026-01-06,102.00,102.00,102.00,102.00"), declaration())


@pytest.mark.parametrize(
    "content, changes, message",
    [
        (csv_file(*ROWS, header="Date,Open,High,Low,Last"), {}, "Columns ['Close'] must each appear exactly once in the header, which is ['Date',"),
        (csv_file(*ROWS, header="Date,Open,High,Low,Close,Close"), {}, "Columns ['Close'] must each appear exactly once in the header"),
        (b"", {}, "Columns ['Date', 'Open', 'High', 'Low', 'Close'] must each appear exactly once in the header, which is []"),
        (csv_file(*ROWS).replace(b"Date", b"D\xe4te"), {}, "The file is not UTF-8 text"),
        (csv_file(*ROWS), {"date_format": "%Y-%m-%Q"}, "Invalid date format '%Y-%m-%Q'"),
        (csv_file(*ROWS), {"calendar": "CBOT_Corn"}, "Unknown exchange calendar 'CBOT_Corn'"),
        (csv_file(ROWS[0]), {}, "at least two sessions are required"),
        (csv_file(*ROWS), {"instrument": {**IMPORT_DECLARATION["instrument"], "expiry": "2026-01-06"}}, "to 2026-01-07 but the contract expires 2026-01-06"),
    ],
)
def test_file_wide_problems_are_rejected(tmp_path, content, changes, message):
    with pytest.raises(DatasetError, match=re.escape(message)):
        import_csv(tmp_path, content, declaration(**changes))
    assert published(tmp_path) == []


def test_layout_does_not_change_the_bars_and_rows_count_blank_lines(tmp_path):
    plain = import_csv(tmp_path, csv_file(*ROWS), declaration())
    # A byte-order mark, padded and reordered headers, an extra column, CRLF line endings and blank lines.
    styled = (
        "\ufeff Volume , Close ,Low,High,Open, Date \r\n"
        "7,100.50,99.50,101.00,100.00,2026-01-05\r\n"
        "\r\n"
        "8, 102.00 ,100.50,102.50,101.00,2026-01-06\r\n"
        "9,104.00,101.50,104.50,102.00,2026-01-07\r\n"
    )
    restyled = import_csv(tmp_path, styled.encode(), declaration())
    assert datasets.read_manifest(tmp_path, restyled).data_sha256 == datasets.read_manifest(tmp_path, plain).data_sha256
    with pytest.raises(DatasetError, match=re.escape("row 4: high must be >= open and close")):
        import_csv(tmp_path, styled.replace("102.50", "101.50").encode(), declaration())


# A known fixture: weekdays from Monday 18 November to Tuesday 31 December 2024, without the two
# CBOT holidays in that span (Thanksgiving and Christmas) and without the week of 9 December.
HOLIDAYS = [date(2024, 11, 28), date(2024, 12, 25)]
MISSING_WEEK = [date(2024, 12, day) for day in range(9, 14)]
FILE_SESSIONS = pd.bdate_range("2024-11-18", "2024-12-31").difference(pd.DatetimeIndex(HOLIDAYS + MISSING_WEEK))


@pytest.mark.parametrize(
    "calendar, label, missing",
    [
        ("CBOT_Agriculture", "CBOT_Agriculture (pandas_market_calendars ", MISSING_WEEK),
        (None, "weekdays (approximate)", sorted(HOLIDAYS + MISSING_WEEK)),
    ],
)
def test_missing_sessions_are_reported_and_never_filled(tmp_path, calendar, label, missing):
    frame = ohlc([400.0] * len(FILE_SESSIONS), [401.5] * len(FILE_SESSIONS), FILE_SESSIONS)
    content = bars_csv(frame)
    dataset_id = import_csv(tmp_path, content, declaration(calendar=calendar))
    manifest = datasets.read_manifest(tmp_path, dataset_id)
    report = manifest.import_report
    assert report.missing_sessions == missing
    assert report.calendar.startswith(label)
    assert (report.source_sha256, report.columns.model_dump(), report.date_format, report.price_unit) == (
        sha256_hex(content),
        IMPORT_DECLARATION["columns"],
        "%Y-%m-%d",
        "currency",
    )
    # The bars are exactly the file's rows: nothing inserted for the missing sessions.
    pd.testing.assert_frame_equal(datasets.load(tmp_path, dataset_id).frame, frame.rename_axis("session"), check_freq=False)
    assert manifest.rows == len(FILE_SESSIONS)
    assert import_csv(tmp_path, content, declaration(calendar=calendar)) == dataset_id  # reproducible


def backtest(data_dir, dataset_id, out) -> dict:
    out.mkdir()
    (out / "strategy.py").write_text(compiler.compile_for_data(templates.instantiate("ema_crossover", dataset_id), data_dir))
    return execute(out / "strategy.py", data_dir, out)


def test_cents_and_currency_files_give_identical_bars_and_backtests(tmp_path):
    # Cent prices such as 101.13 are not exact in binary, so the two units must still meet on one float.
    instrument = {**IMPORT_DECLARATION["instrument"], "tick_size": 0.01}
    frame = synthetic_bars(seed=5, sessions=200, tick_size=0.01)
    frame = frame.drop(frame.index[100:105])  # a missing week that the backtest runs across
    ids = {unit: import_csv(tmp_path, bars_csv(frame, unit), declaration(price_unit=unit, instrument=instrument)) for unit in ("currency", "cents")}
    currency, cents = (datasets.read_manifest(tmp_path, ids[unit]) for unit in ("currency", "cents"))
    assert currency.data_sha256 == cents.data_sha256
    assert currency.model_dump(exclude={"import_report"}) == cents.model_dump(exclude={"import_report"})
    assert (currency.import_report.price_unit, cents.import_report.price_unit) == ("currency", "cents")
    as_written = frame.map(lambda p: float(f"{p:.2f}")).rename_axis("session")  # each price as the file states it
    pd.testing.assert_frame_equal(datasets.load(tmp_path, ids["cents"]).frame, as_written, check_exact=True, check_freq=False)

    runs = {unit: backtest(tmp_path, ids[unit], tmp_path / unit) for unit in ids}
    assert len(pd.read_csv(tmp_path / "cents" / "results" / "backtest" / "fills.csv")) > 0
    assert runs["cents"]["outputs"] == runs["currency"]["outputs"]
    assert runs["cents"]["result_sha256"] == runs["currency"]["result_sha256"]


def test_fractional_cents_give_the_same_bars_as_currency(tmp_path):
    # CBOT grains are quoted in cents with quarter-cent ticks: 412.25 cents is 4.1225, not exact in binary.
    instrument = {**IMPORT_DECLARATION["instrument"], "tick_size": 0.0025}
    cents = csv_file("2024-12-02,412.25,415.50,410.75,414.00", "2024-12-03,414.00,416.25,412.50,413.75")
    currency = csv_file("2024-12-02,4.1225,4.1550,4.1075,4.1400", "2024-12-03,4.1400,4.1625,4.1250,4.1375")
    ids = [import_csv(tmp_path, cents, declaration(price_unit="cents", instrument=instrument))]
    ids.append(import_csv(tmp_path, currency, declaration(instrument=instrument)))
    frames = [datasets.load(tmp_path, i).frame for i in ids]
    pd.testing.assert_frame_equal(frames[0], frames[1], check_exact=True)
    assert frames[0]["open"].tolist() == [4.1225, 4.14]


def client(home) -> TestClient:
    return TestClient(create_app(home), base_url="http://localhost")


def upload(client, content: bytes, decl: dict = IMPORT_DECLARATION, content_type: str = "text/csv"):
    return client.post("/api/datasets/import", params={"declaration": json.dumps(decl)}, content=content, headers={"Content-Type": content_type})


def test_uploads_over_the_limit_are_refused(tmp_path):
    app = client(tmp_path)
    assert upload(app, b"x" * (MAX_UPLOAD_BYTES + 1)).status_code == 413
    at_limit = upload(app, b"x" * MAX_UPLOAD_BYTES)  # not refused for its size, only for its content
    assert at_limit.status_code == 422 and "must each appear exactly once in the header" in at_limit.json()["detail"]
    assert len(at_limit.json()["detail"]) < 500  # the 10 MB header field is not echoed back
    assert published(tmp_path) == []


def test_upload_problems_are_client_errors(tmp_path):
    app = client(tmp_path)
    assert upload(app, csv_file(*ROWS), content_type="text/plain").status_code == 415  # other sites can post text/plain unasked
    assert upload(app, csv_file(*ROWS), {**IMPORT_DECLARATION, "price_unit": None}).status_code == 422  # undeclared unit
    rejected = upload(app, csv_file(ROWS[0], "2026-01-06,101.00,,100.50,102.00", ROWS[2]))
    assert rejected.status_code == 422 and "row 3: high is missing" in rejected.json()["detail"]
    assert published(tmp_path) == []


def test_cli_and_upload_import_the_same_dataset(tmp_path, capsys):
    content = bars_csv(ohlc([400.0] * len(FILE_SESSIONS), [401.5] * len(FILE_SESSIONS), FILE_SESSIONS))
    (tmp_path / "bars.csv").write_bytes(content)
    (tmp_path / "declaration.json").write_text(json.dumps(IMPORT_DECLARATION))
    assert main(["import", str(tmp_path / "bars.csv"), str(tmp_path / "declaration.json"), "--home", str(tmp_path / "cli")]) == 0
    printed = capsys.readouterr().out
    assert "missing sessions (weekdays (approximate)): 7\n  2024-11-28\n" in printed

    response = upload(client(tmp_path / "app"), content)
    assert response.status_code == 200, response.text
    assert printed.startswith(f"dataset  {response.json()['id']}\n")
    assert response.json()["import_report"]["missing_sessions"][0] == "2024-11-28"
    assert client(tmp_path / "app").get("/api/datasets").json() == [response.json()]

    (tmp_path / "declaration.json").write_text(json.dumps({**IMPORT_DECLARATION, "date_format": None}))
    assert main(["import", str(tmp_path / "bars.csv"), str(tmp_path / "declaration.json"), "--home", str(tmp_path / "cli")]) == 1
    assert "error:" in capsys.readouterr().err
