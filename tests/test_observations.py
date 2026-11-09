"""Observations and as-of joins (Stage 4 workstream 4): a hand-calculated fixture with late releases,
revisions and an early close; freshness from publication; the run cutoff; refused imports; kept
estimates; each run's evidence class; refused strategies; and app, CLI and export parity. The
causality harness (tests/test_operator_contracts.py) rewrites observations published after a
session's close, as well as later bars."""

import io
import json
import re
import subprocess
import sys
import zipfile
from datetime import date

import pandas as pd
import pytest

from conftest import IMPORT_DECLARATION, INSTRUMENT, client_for, run_bundle, start, wait
from qrs import cli, compiler, datasets, graph
from qrs.contracts import Edge, ImportDeclaration, Node, ObservationsDeclaration, PortRef, StrategyDocument
from qrs.datasets import DatasetError
from qrs.importer import import_csv, import_observations
from qrs.operators.data import as_of_values
from qrs.ports import Bars
from qrs.runtime import Run, execute

# NYSE sessions around Thanksgiving 2024: closed on the 28th, closing early (13:00 New York, 18:00
# UTC) on the 29th, otherwise at 16:00 New York (21:00 UTC).
BARS_CSV = b"""Date,Open,High,Low,Close
2024-11-25,100.00,101.00,99.00,100.50
2024-11-26,100.50,102.00,100.00,101.50
2024-11-27,101.50,103.00,101.00,102.50
2024-11-29,102.50,103.50,102.00,103.00
2024-12-02,103.00,104.00,102.50,103.50
2024-12-03,103.50,104.50,103.00,104.00
2024-12-04,104.00,105.00,103.50,104.50
2024-12-05,104.50,105.50,104.00,105.00
2024-12-06,105.00,106.00,104.50,105.50
"""
SESSIONS = pd.DatetimeIndex(["2024-11-25", "2024-11-26", "2024-11-27", "2024-11-29", "2024-12-02", "2024-12-03", "2024-12-04", "2024-12-05", "2024-12-06"])

# Weekly figures, each with the time it was published, in UTC (21:00 is a regular close):
# A, the week of the 15th, published on the 22nd at 13:30.
# B, the week of the 22nd, published on the 27th at 21:00, exactly at the close.
# C, the week of the 29th, published on the 29th at 19:00, after the early close.
# B revised on 3 December at 14:00, after C's first release.
# C revised at 05:00 on 4 December Tokyo time (+09:00): 20:00 UTC on the 3rd, before that day's close.
OBSERVATIONS_CSV = b"""Week,Published,Release,Rate
2024-11-15,2024-11-22T08:30:00-05:00,first,1.0
2024-11-22,2024-11-27T21:00:00+00:00,first,2.0
2024-11-29,2024-11-29T14:00:00-05:00,first,3.0
2024-11-22,2024-12-03T09:00:00-05:00,revised,2.5
2024-11-29,2024-12-04T05:00:00+09:00,revised,3.5
"""


def observations_declaration(**changes) -> ObservationsDeclaration:
    declaration = {
        "kind": "qrs/observations",
        "name": "Weekly rate",
        "columns": {"event": "Week", "published": "Published", "revision": "Release"},
        "event_format": "%Y-%m-%d",
        "values": {"Rate": "percent"},
        "evidence_class": "retrospective",
        "source": "tests",
        "redistributable": True,
    }
    return ObservationsDeclaration.model_validate({**declaration, **changes})


def bars_declaration(**changes) -> dict:
    return {**IMPORT_DECLARATION, "name": "NYSE sessions", "calendar": "NYSE", **changes}


def import_fixture(data_dir) -> tuple[str, str]:
    bars = import_csv(data_dir, BARS_CSV, ImportDeclaration.model_validate(bars_declaration()))
    return bars, import_observations(data_dir, OBSERVATIONS_CSV, observations_declaration())


def joined(data_dir, bars_id: str, observations_id: str, until: date | None = None, **params) -> pd.Series:
    run = Run(data_dir, until)
    bars = run.op("bars", "qrs.dataset", 1, {}, {"dataset_id": bars_id})["bars"]
    return run.op("rate", "qrs.as_of", 1, {"bars": bars}, {"dataset_id": observations_id, "column": "Rate", **params})["series"]


def values(series: pd.Series) -> list:
    return [None if pd.isna(v) else v for v in series]


def test_as_of_uses_what_was_published_by_each_close(tmp_path):
    bars_id, observations_id = import_fixture(tmp_path)
    week = joined(tmp_path, bars_id, observations_id, max_age_days=7)
    assert week.index.equals(SESSIONS)
    # 25th, 26th: A (B is not published until the 27th's close). 27th: B, published exactly at the
    # close. 29th: still B; C comes an hour after the early close. 2nd: C. 3rd: C's revision, an hour
    # before the close in UTC (read without its offset it would come after); B's revision that
    # morning changes nothing, B being the older week. The values before each revision stand.
    assert values(week) == [1.0, 1.0, 2.0, 2.0, 3.0, 3.5, 3.5, 3.5, 3.5]
    # With two days' freshness: A is 3.3 days old on the 25th; on the 2nd C is 3.1 days old, and B
    # is not used instead; on the 5th C's revision is 2 days and an hour old. Stale values are missing.
    assert values(joined(tmp_path, bars_id, observations_id, max_age_days=2)) == [None, None, 2.0, 2.0, None, 3.5, 3.5, None, None]


def test_freshness_is_measured_from_the_publication_of_the_revision_used():
    close = pd.Timestamp("2024-12-02 21:00", tz="UTC")
    rows = pd.DataFrame(
        {
            "event": pd.to_datetime(["2024-11-22", "2024-11-29"]),
            "published": [close - pd.Timedelta(days=5), close - pd.Timedelta(days=1)],
            "revision": "",
            "estimated": False,
            "value": [1.0, 2.0],
        }
    )
    closes = pd.Series([close], index=pd.DatetimeIndex(["2024-12-02"]))
    assert as_of_values(rows, "value", closes, pd.Timedelta(days=1)).tolist() == [2.0]  # exactly one day old: still fresh
    stale = as_of_values(rows, "value", closes, pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
    assert stale.isna().all()  # a second too old, and the older event is not carried forward


def test_a_later_revision_of_an_older_event_never_replaces_a_newer_event():
    close = pd.Timestamp("2024-12-03 21:00", tz="UTC")
    rows = pd.DataFrame(
        {
            "event": pd.to_datetime(["2024-11-22", "2024-11-29", "2024-11-22"]),
            "published": [close - pd.Timedelta(days=6), close - pd.Timedelta(days=4), close - pd.Timedelta(hours=1)],
            "revision": ["", "", "revised"],
            "estimated": False,
            "value": [2.0, 3.0, 2.5],
        }
    )
    closes = pd.Series([close], index=pd.DatetimeIndex(["2024-12-03"]))
    # The week of the 22nd was revised after the week of the 29th was published: the newer week stands.
    assert as_of_values(rows, "value", closes, pd.Timedelta(days=7)).tolist() == [3.0]


def test_a_run_cutoff_reads_no_observation_published_after_the_last_close_before_it(tmp_path):
    bars_id, observations_id = import_fixture(tmp_path)
    run = Run(tmp_path, until=date(2024, 12, 3))
    bars = run.dataset(bars_id)
    rows = run.observations(observations_id, bars)
    # The last session before the 3rd is the 2nd, closing at 21:00 UTC: A, B and C are read, and
    # neither revision, both published on the 3rd.
    assert rows["published"].max() == pd.Timestamp("2024-11-29 19:00", tz="UTC") and len(rows) == 3
    full = joined(tmp_path, bars_id, observations_id, max_age_days=7)
    pd.testing.assert_series_equal(joined(tmp_path, bars_id, observations_id, until=date(2024, 12, 3), max_age_days=7), full.iloc[:5])

    strategy = tmp_path / "strategy.py"
    strategy.write_text(compiler.compile_for_data(as_of_strategy(bars_id, observations_id), tmp_path, date(2024, 12, 3)))
    record = execute(strategy, tmp_path, tmp_path / "out", date(2024, 12, 3))
    assert record["until"] == "2024-12-03" and set(record["datasets"]) == {bars_id, observations_id}


def test_closes_follow_the_exchange_calendar_across_daylight_saving():
    # New York moved to daylight time on 10 March 2024: the close went from 21:00 to 20:00 UTC.
    bars = Bars(pd.DataFrame(index=pd.DatetimeIndex(["2024-03-08", "2024-03-11"])), INSTRUMENT, "sha256:" + "0" * 64, calendar="NYSE")
    assert datasets.session_closes(bars).tolist() == [pd.Timestamp("2024-03-08 21:00", tz="UTC"), pd.Timestamp("2024-03-11 20:00", tz="UTC")]


@pytest.mark.parametrize("evidence_class", ["synthetic", "retrospective", "point_in_time"])
def test_imports_refuse_missing_or_naive_publication_times_whatever_the_evidence_class(tmp_path, evidence_class):
    content = b"""Week,Published,Release,Rate
2024-11-15,2024-11-22T08:30:00-05:00,first,1.0
2024-11-22,2024-11-27 16:00,first,2.0
2024-11-29,,first,3.0
2024-12-06,2024-12-06,first,4.0
2024-12-13,9999-12-31T23:59:59-05:00,first,5.0
"""
    with pytest.raises(DatasetError) as refused:
        import_observations(tmp_path, content, observations_declaration(evidence_class=evidence_class))
    assert str(refused.value).splitlines()[1:] == [
        "row 3: published '2024-11-27 16:00' has no UTC offset",
        "row 4: published is missing",
        "row 5: published '2024-12-06' has no UTC offset",
        "row 6: published '9999-12-31T23:59:59-05:00' is out of range",  # the year 10000 in UTC
    ]


ESTIMATED_CSV = b"""Week,Published,Release,Rate,Estimated
2024-11-15,2024-11-22T08:30:00-05:00,first,1.0,false
2024-11-22,2024-11-27T21:00:00+00:00,first,2.0,TRUE
"""


def test_estimated_timestamps_cannot_be_point_in_time_and_are_kept_with_how_they_were_made(tmp_path):
    columns = {"event": "Week", "published": "Published", "revision": "Release", "estimated": "Estimated"}
    how = "Release times reconstructed from the agency's calendar, at 08:30 New York."
    refused = r"1 row\(s\) have estimated publication timestamps \(first row 3\), so the data is retrospective, not point_in_time"
    with pytest.raises(DatasetError, match=refused):
        import_observations(tmp_path, ESTIMATED_CSV, observations_declaration(columns=columns, evidence_class="point_in_time", estimates=how))
    with pytest.raises(DatasetError, match=r"first row 3\): declare how they were estimated"):
        import_observations(tmp_path, ESTIMATED_CSV, observations_declaration(columns=columns))
    with pytest.raises(DatasetError, match="no row is marked estimated"):
        import_observations(tmp_path, OBSERVATIONS_CSV, observations_declaration(estimates=how))

    dataset_id = import_observations(tmp_path, ESTIMATED_CSV, observations_declaration(columns=columns, estimates=how))
    manifest = datasets.read_manifest(tmp_path, dataset_id)
    assert (manifest.evidence_class, manifest.estimated_rows, manifest.estimates) == ("retrospective", 1, how)
    assert datasets.load_observations(tmp_path, dataset_id)["estimated"].tolist() == [False, True]


def test_other_observation_rows_are_rejected_by_row_number(tmp_path):
    columns = {"event": "Week", "published": "Published", "revision": "Release", "estimated": "Estimated"}
    content = b"""Week,Published,Release,Rate,Estimated
2024-11-15,2024-11-22T08:30:00-05:00,first,1.0,false
15/11/2024,2024-11-22T08:30:00-05:00,first,1.0,false
2024-11-15,2024-11-22T13:30:00+00:00,again,1.5,false
2024-11-22,next week,first,,maybe
"""
    with pytest.raises(DatasetError) as refused:
        import_observations(tmp_path, content, observations_declaration(columns=columns))
    assert str(refused.value).splitlines()[1:] == [
        "row 3: event '15/11/2024' does not match the format '%Y-%m-%d'",
        "row 4: repeats the event and publication time of row 2",
        "row 5: published 'next week' is not an ISO 8601 timestamp",
        "row 5: estimated must be true or false, not 'maybe'",
        "row 5: Rate is missing",
    ]


def test_observations_changed_after_publication_are_refused(tmp_path):
    _, observations_id = import_fixture(tmp_path)
    data = datasets.directory(tmp_path, observations_id) / "observations.parquet"
    data.chmod(0o644)
    data.write_bytes(data.read_bytes() + b"\0")
    with pytest.raises(DatasetError, match="changed after publication"):
        datasets.load_observations(tmp_path, observations_id)


def node(node_id: str, operator_id: str, **params) -> Node:
    return Node(id=node_id, operator=operator_id, version=1, params=params)


def wire(source: tuple[str, str], target: tuple[str, str]) -> Edge:
    return Edge(source=PortRef(node=source[0], port=source[1]), target=PortRef(node=target[0], port=target[1]))


def threshold_strategy(bars_id: str, source: Node, port: str, value: float, lag: int = 0) -> StrategyDocument:
    """Long while the source's series, `lag` sessions earlier, is above `value`; flat otherwise."""
    nodes = [
        node("bars", "qrs.dataset", dataset_id=bars_id),
        source,
        node("entry", "qrs.threshold", comparison=">", value=value),
        node("exit", "qrs.not"),
        node("position", "qrs.long_flat"),
        node("backtest", "qrs.backtest"),
    ]
    edges = [
        wire(("bars", "bars"), (source.id, "bars")),
        wire(("bars", "bars"), ("backtest", "bars")),
        wire(("entry", "signal"), ("exit", "a")),
        wire(("entry", "signal"), ("position", "entry")),
        wire(("exit", "signal"), ("position", "exit")),
        wire(("position", "positions"), ("backtest", "positions")),
    ]
    if lag:
        nodes.append(node("earlier", "qrs.lag", sessions=lag))
        edges += [wire((source.id, port), ("earlier", "a")), wire(("earlier", "series"), ("entry", "a"))]
    else:
        edges.append(wire((source.id, port), ("entry", "a")))
    return StrategyDocument(name="Threshold", nodes=nodes, edges=edges)


def as_of_strategy(bars_id: str, observations_id: str, lag: int = 0) -> StrategyDocument:
    """Long while the joined rate is above 2.5: from the close of 2 December, when it is 3.0."""
    rate = node("rate", "qrs.as_of", dataset_id=observations_id, column="Rate", max_age_days=7)
    return threshold_strategy(bars_id, rate, "series", 2.5, lag)


def test_joins_onto_bars_without_a_calendar_and_datasets_of_the_wrong_kind_are_refused_before_running(tmp_path):
    bars_id, observations_id = import_fixture(tmp_path)
    weekdays_only = ImportDeclaration.model_validate(bars_declaration(name="Weekdays only", calendar=None))
    weekdays = import_csv(tmp_path, BARS_CSV, weekdays_only)
    synthetic = datasets.create_synthetic(tmp_path, sessions=60)
    for uncalendared, name in [(weekdays, "Weekdays only"), (synthetic, "Synthetic SYN daily bars (seed 42)")]:
        with pytest.raises(
            compiler.InvalidStrategy, match=re.escape(f"needs each session's close from the bars' exchange calendar, but these bars have none: {name}.")
        ):
            compiler.compile_for_data(as_of_strategy(uncalendared, observations_id), tmp_path)
    swapped = as_of_strategy(observations_id, bars_id)
    assert [(i.node, i.message) for i in graph.check_data(swapped, tmp_path)][:2] == [
        ("bars", "dataset_id: the dataset holds observations, not bars."),
        ("rate", "dataset_id: the dataset holds bars, not observations."),
    ]


def test_a_join_onto_bars_with_a_day_their_calendar_has_no_session_is_refused_before_running(tmp_path):
    # Such a bar is imported (the import only reports missing sessions), but its close is unknown.
    _, observations_id = import_fixture(tmp_path)
    with_holiday = BARS_CSV.replace(b"2024-11-29,", b"2024-11-28,102.50,103.50,102.00,103.00\n2024-11-29,")
    bars_id = import_csv(tmp_path, with_holiday, ImportDeclaration.model_validate(bars_declaration(name="With Thanksgiving")))
    with pytest.raises(compiler.InvalidStrategy, match=re.escape("With Thanksgiving: NYSE has no session on 2024-11-28, so its close is unknown.")):
        compiler.compile_for_data(as_of_strategy(bars_id, observations_id), tmp_path)


def test_observations_do_not_count_towards_the_sessions_a_backtest_needs(tmp_path):
    # Lagging the rate 6 sessions needs 6 + 2 = 8 sessions: the bars have 9, the observations 5 rows.
    bars_id, observations_id = import_fixture(tmp_path)
    assert graph.check_data(as_of_strategy(bars_id, observations_id, lag=6), tmp_path) == []
    issues = graph.check_data(as_of_strategy(bars_id, observations_id, lag=8), tmp_path)
    assert [i.message for i in issues] == ["A warm-up of 8 sessions leaves no session to trade: it needs 10 sessions; the data has 9."]


def upload(client, content: bytes, declaration: dict) -> str:
    response = client.post("/api/datasets/import", params={"declaration": json.dumps(declaration)}, content=content, headers={"Content-Type": "text/csv"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def run_strategy(client, document: StrategyDocument) -> dict:
    strategy = client.post("/api/strategies", json={"document": document.model_dump(mode="json")}).json()
    run = wait(client, start(client, strategy)["id"])
    assert run["status"] == "succeeded", run["error"]
    return strategy | {"run": run, "result": client.get(f"/api/runs/{run['id']}/result").json()}


def test_a_run_is_as_weak_as_the_weakest_dataset_it_read(tmp_path):
    with client_for(tmp_path) as client:
        bars_id = upload(client, BARS_CSV, bars_declaration(evidence_class="point_in_time"))
        observations_id = upload(client, OBSERVATIONS_CSV, observations_declaration().model_dump(mode="json"))
        assert {d["kind"] for d in client.get("/api/datasets").json()} == {"qrs/dataset", "qrs/observations"}
        joined_runs = [run_strategy(client, as_of_strategy(bars_id, observations_id)) for _ in range(2)]
        assert [r["result"]["evidence_class"] for r in joined_runs] == ["retrospective", "retrospective"]
        compared = client.post("/api/compare", json={"run_ids": [r["run"]["id"] for r in joined_runs]}).json()
        assert [r["evidence_class"] for r in compared["runs"]] == ["retrospective", "retrospective"]

        bars_only = threshold_strategy(bars_id, node("price", "qrs.column"), "series", 102.0)
        assert run_strategy(client, bars_only)["result"]["evidence_class"] == "point_in_time"


def test_app_cli_and_export_reproduce_an_as_of_run(tmp_path, tmp_path_factory):
    with client_for(tmp_path) as client:
        bars_id = upload(client, BARS_CSV, bars_declaration())
        observations_id = upload(client, OBSERVATIONS_CSV, observations_declaration().model_dump(mode="json"))
        app = run_strategy(client, as_of_strategy(bars_id, observations_id))
        bundle = client.get(f"/api/strategies/{app['id']}/export").content
    app_record = app["result"]["record"]
    assert [f["side"] for f in app["result"]["results"]["backtest"]["fills"]] == ["BUY"]  # the rate passes 2.5 on 2 December
    assert app_record["runtime"]["packages"]["pandas_market_calendars"]

    work = tmp_path_factory.mktemp("parity")
    (work / "strategy.json").write_text(json.dumps(app["document"]))
    command = [sys.executable, "-m", "qrs.cli", "run", str(work / "strategy.json"), "--data", str(tmp_path), "--out", str(work / "cli")]
    done = subprocess.run(command, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    cli_record = json.loads((work / "cli" / "record.json").read_text())
    folder = datasets.directory(tmp_path, observations_id).name
    assert f"data/datasets/{folder}/observations.parquet" in zipfile.ZipFile(io.BytesIO(bundle)).namelist()
    export_record = run_bundle(bundle, work)
    for record in (cli_record, export_record):
        for field in ("code_sha256", "runtime", "datasets", "outputs", "result_sha256"):
            assert record[field] == app_record[field], field


def test_qrs_import_reads_an_observations_declaration(tmp_path, capsys):
    (tmp_path / "rates.csv").write_bytes(OBSERVATIONS_CSV)
    (tmp_path / "rates.json").write_text(observations_declaration().model_dump_json())
    assert cli.main(["import", str(tmp_path / "rates.csv"), str(tmp_path / "rates.json"), "--home", str(tmp_path)]) == 0
    dataset_id = capsys.readouterr().out.split()[1]
    assert datasets.read_manifest(tmp_path, dataset_id).kind == "qrs/observations"
    # A file of bars still imports as bars through the same command.
    (tmp_path / "bars.csv").write_bytes(BARS_CSV)
    (tmp_path / "bars.json").write_text(json.dumps(bars_declaration()))
    assert cli.main(["import", str(tmp_path / "bars.csv"), str(tmp_path / "bars.json"), "--home", str(tmp_path)]) == 0


def test_an_invalid_declaration_is_reported_against_its_own_kind_only(tmp_path, capsys):
    with client_for(tmp_path) as client:
        for declaration, field in [(bars_declaration(name=""), "name"), ({**observations_declaration().model_dump(mode="json"), "values": {}}, "values")]:
            response = client.post(
                "/api/datasets/import", params={"declaration": json.dumps(declaration)}, content=BARS_CSV, headers={"Content-Type": "text/csv"}
            )
            assert response.status_code == 422
            assert [error["loc"] for error in response.json()["detail"]] == [["query", "declaration", field]]
    (tmp_path / "broken.json").write_text("{not json")
    assert cli.main(["import", str(tmp_path / "rates.csv"), str(tmp_path / "broken.json"), "--home", str(tmp_path)]) == 1
    assert capsys.readouterr().err.startswith("error: 1 validation error for ImportDeclaration")
