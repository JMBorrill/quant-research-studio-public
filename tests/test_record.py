"""Stage 5 workstream 1: qrs.record saves a table beside the backtest, and as-of alignment is
vectorised with the same results."""

import time
from datetime import date

import numpy as np
import pandas as pd

from conftest import run_recorded
from qrs import compiler, datasets, templates
from qrs.contracts import Edge, Node, PortRef
from qrs.operators.data import as_of_values
from qrs.runtime import Run, execute


def recording_strategy(dataset_id: str):
    """The EMA crossover with the fast EMA and the one-session return recorded beside its backtest."""
    doc = templates.instantiate("ema_crossover", dataset_id)
    fast = next(n.id for n in doc.nodes if n.operator == "qrs.ema")
    doc.nodes += [
        Node(id="change", operator="qrs.returns", version=1),
        Node(id="features", operator="qrs.table", version=1, params={"name": "fast"}),
        Node(id="widened", operator="qrs.merge", version=1, params={"name": "change"}),
        Node(id="kept", operator="qrs.record", version=1),
    ]
    wires = [("bars", "bars", "change", "bars"), (fast, "series", "features", "series"), ("features", "table", "widened", "table")]
    wires += [("change", "series", "widened", "series"), ("widened", "table", "kept", "table")]
    doc.edges += [Edge(source=PortRef(node=a, port=b), target=PortRef(node=c, port=d)) for a, b, c, d in wires]
    return doc


def test_a_record_saves_exactly_its_input_cut_by_until(tmp_path):
    dataset_id = datasets.create_synthetic(tmp_path)
    strategy = tmp_path / "strategy.py"
    strategy.write_text(compiler.compile_strategy(recording_strategy(dataset_id)))
    until = date(2024, 12, 2)
    record = execute(strategy, tmp_path, tmp_path / "out", until)
    saved = pd.read_parquet(tmp_path / "out" / "records" / "kept.parquet")
    expected = run_recorded(strategy, tmp_path, until).outputs["widened"]["table"]
    pd.testing.assert_frame_equal(saved, expected)
    assert list(saved.columns) == ["fast", "change"] and saved.index[-1] < pd.Timestamp(until)
    assert len(saved) == (datasets.load(tmp_path, dataset_id).frame.index < pd.Timestamp(until)).sum()
    # Covered by the result hash, and reproduced byte for byte.
    assert "records/kept.parquet" in record["outputs"]
    assert execute(strategy, tmp_path, tmp_path / "again", until)["result_sha256"] == record["result_sha256"]
    # The trades are those of the strategy without the record.
    plain = tmp_path / "plain.py"
    plain.write_text(compiler.compile_strategy(templates.instantiate("ema_crossover", dataset_id)))
    without = execute(plain, tmp_path, tmp_path / "plain", until)
    assert {k: v for k, v in record["outputs"].items() if k.startswith("results/")} == without["outputs"]
    assert not (tmp_path / "plain" / "records").exists()


def reference_as_of(rows: pd.DataFrame, column: str, closes: pd.Series, max_age: pd.Timedelta) -> pd.Series:
    """The original, per-close implementation of as_of_values (Stage 4), kept as an oracle."""
    values = []
    for close in closes:
        known = rows[rows["published"] <= close]
        if known.empty:
            values.append(float("nan"))
            continue
        latest = known[known["event"] == known["event"].max()]
        revision = latest.loc[latest["published"].idxmax()]
        values.append(revision[column] if close - revision["published"] <= max_age else float("nan"))
    return pd.Series(values, index=closes.index, dtype=float)


def random_observations(rng: np.random.Generator, n: int) -> pd.DataFrame:
    """Events published late and revised, out of order, some revisions at the same moment, some of
    older events after newer ones, some values missing."""
    events = pd.Timestamp("2020-01-01") + pd.to_timedelta(rng.integers(0, 60, n), unit="D")
    published = (events + pd.to_timedelta(rng.integers(0, 20 * 24, n), unit="h")).tz_localize("UTC")
    if n:
        published = published.where(rng.random(n) > 0.1, published[0])  # ties
    values = rng.normal(size=n)
    values[rng.random(n) < 0.05] = np.nan
    return pd.DataFrame({"event": events, "published": published, "revision": "", "estimated": False, "value": values})


def test_vectorised_as_of_matches_the_original_on_random_histories():
    rng = np.random.default_rng(7)
    sessions = pd.bdate_range("2019-12-20", "2020-04-30")
    closes = pd.Series((sessions + pd.Timedelta(hours=19)).tz_localize("UTC"), index=sessions)
    for n in (0, 1, 2, 5, 40, 300):
        rows = random_observations(rng, n)
        for days in (1, 3, 31):
            expected = reference_as_of(rows, "value", closes, pd.Timedelta(days=days))
            pd.testing.assert_series_equal(as_of_values(rows, "value", closes, pd.Timedelta(days=days)), expected)


def test_vectorised_as_of_handles_dates_beyond_the_nanosecond_range():
    """Imported dates are parsed in microseconds, so an event in 1209, or a publication estimated before
    1677, can reach the join, as they could reach the original loop."""
    rows = pd.DataFrame(
        {
            "event": pd.to_datetime(["1209-01-01", "2016-01-01", "2017-01-01"]).as_unit("us"),
            "published": pd.to_datetime(["2017-04-01", "1600-01-01", "2017-04-01"]).as_unit("us").tz_localize("UTC"),
            "revision": "",
            "estimated": False,
            "value": [1.0, 2.0, 3.0],
        }
    )
    sessions = pd.DatetimeIndex(["2017-03-31", "2017-04-03"])
    closes = pd.Series((sessions + pd.Timedelta(hours=20)).tz_localize("UTC"), index=sessions)
    for days in (1, 3660):
        expected = reference_as_of(rows, "value", closes, pd.Timedelta(days=days))
        pd.testing.assert_series_equal(as_of_values(rows, "value", closes, pd.Timedelta(days=days)), expected)


def test_vectorised_as_of_matches_the_original_on_the_macro_template(tmp_path):
    """The one template joining observations, on its feature dataset as loaded, with a document column."""
    chosen = templates.prepare("macro_ridge", tmp_path)
    run = Run(tmp_path)
    bars = run.dataset(chosen[datasets.BARS])
    rows = run.observations(chosen[datasets.OBSERVATIONS], bars)
    closes = datasets.session_closes(bars)
    joins = [n.params for n in templates.load("macro_ridge").strategy.nodes if n.operator == "qrs.as_of"]
    assert joins
    for params in joins:
        max_age = pd.Timedelta(days=params.get("max_age_days", 31))
        expected = reference_as_of(rows, params["column"], closes, max_age)
        pd.testing.assert_series_equal(as_of_values(rows, params["column"], closes, max_age), expected)
        assert expected.notna().any()


def test_as_of_meets_its_budget():
    """One series on 12,615 closes from 1,200 weekly rows in under 0.2 s (16.6 s before)."""
    sessions = pd.bdate_range("1977-01-03", periods=12_615)
    closes = pd.Series((sessions + pd.Timedelta(hours=19)).tz_localize("UTC"), index=sessions)
    events = pd.date_range("1977-01-02", periods=1_200, freq="W")
    rows = pd.DataFrame({"event": events, "published": (events + pd.Timedelta(days=4)).tz_localize("UTC"), "revision": "", "estimated": False})
    rows["value"] = np.arange(1_200, dtype=float)
    start = time.perf_counter()
    as_of_values(rows, "value", closes, pd.Timedelta(days=31))
    assert time.perf_counter() - start < 0.2


def test_of_two_revisions_published_at_the_same_moment_the_earlier_row_stands():
    """As the original loop's idxmax chose; found missing by the Stage 5 mutation pass."""
    close = pd.Timestamp("2024-12-02 21:00", tz="UTC")
    published = close - pd.Timedelta(hours=1)
    rows = pd.DataFrame(
        {"event": pd.to_datetime(["2024-11-29"] * 2), "published": [published] * 2, "revision": ["first", "second"], "estimated": False, "value": [1.0, 2.0]}
    )
    closes = pd.Series([close], index=pd.DatetimeIndex(["2024-12-02"]))
    assert as_of_values(rows, "value", closes, pd.Timedelta(days=7)).tolist() == [1.0] == reference_as_of(rows, "value", closes, pd.Timedelta(days=7)).tolist()
