"""Stage 5 workstream 3: descriptive methods, the rolling deviation operator and deterministic SVG
figures. Each method is checked against hand calculations, end to end through `qrs analyse`, and
within its budget."""

import hashlib
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ElementTree
from datetime import date

import numpy as np
import pandas as pd
import pytest

from conftest import client_for
from qrs import analysis, datasets, figures, methods
from qrs.contracts import ImportDeclaration
from qrs.importer import import_csv, import_observations
from qrs.runtime import Run
from qrs.store import Store
from test_analysis import analyse, document, run_with_record, wait_analysis
from test_observations import BARS_CSV, OBSERVATIONS_CSV, bars_declaration, observations_declaration


class Given:
    """A context supplying given series, returns that add up or compound, and event sessions."""

    def __init__(self, series: dict[str, pd.Series], additive: bool = False, events: pd.DatetimeIndex | None = None):
        self.given, self.is_additive, self.event_sessions = series, additive, events

    def series(self, name, whole=False):
        return self.given[name]

    def from_since(self, values):
        return values

    def additive(self, name):
        return self.is_additive

    def events(self, name):
        return self.event_sessions


def run(method_id: str, context, **params) -> methods.Result:
    method = methods.get(method_id, 1)
    return method.run(context, method.params.model_validate(params))


def days(values, start="2024-01-01") -> pd.Series:
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def test_distribution_counts_equal_width_bins():
    found = run("qrs.distribution", Given({"values": days(range(10))}), bins=5)
    assert found.tables["histogram"]["count"].tolist() == [2, 2, 2, 2, 2]
    assert found.tables["histogram"]["low"].tolist() == [0.0, 1.8, 3.6, 5.4, 7.2]
    assert (found.values["min"], found.values["max"], found.values["n"]) == (0.0, 9.0, 10)


def test_rolling_mean_and_deviation_and_the_operator_agree_with_hand_values():
    found = run("qrs.rolling", Given({"values": days([1, 2, 3, 4])}), period=2)
    assert found.tables["rolling"]["mean"].tolist()[1:] == [1.5, 2.5, 3.5]
    assert found.tables["rolling"]["std"].tolist()[1:] == [0.5, 0.5, 0.5]  # population deviation
    assert found.values["windows"] == 3
    operator = Run(None).op("vol", "qrs.rolling_std", 1, {"series": days([1, 2, 3, 4])}, {"period": 2})["series"]
    assert operator.tolist()[1:] == [0.5, 0.5, 0.5] and np.isnan(operator.iloc[0])


def test_seasonal_profile_compounds_fractions_sums_price_changes_and_drops_partial_months():
    index = pd.bdate_range("2020-01-15", "2021-03-12")
    returns = pd.Series(0.0, index=index)
    returns[(index.month == 2) & (index.year == 2020)] = 0.01  # 20 sessions in February 2020
    returns[(index.month == 2) & (index.year == 2021)] = -0.01
    found = run("qrs.seasonal", Given({"returns": returns}))
    table = found.tables["monthly"]
    assert table.index.tolist() == [2020, 2021] and "Jan" in table  # January 2020 and March 2021 are left out
    assert table.loc[2020, "Feb"] == pytest.approx(1.01**20 - 1) and table.loc[2021, "Feb"] == pytest.approx(0.99**20 - 1)
    assert np.isnan(table.loc[2020, "Jan"]) and table.loc[2021, "Jan"] == 0.0
    assert found.values["months"]["Feb"]["years"] == 2 and found.values["months"]["Mar"]["years"] == 1
    added = run("qrs.seasonal", Given({"returns": returns * 100}, additive=True)).tables["monthly"]
    assert added.loc[2020, "Feb"] == pytest.approx(20.0) and added.loc[2021, "Feb"] == pytest.approx(-20.0)
    assert found.warnings == ["Some calendar months have only 1 years."]


def test_event_study_profiles_returns_around_event_sessions():
    returns = days([0.0, 1.0, -2.0, 0.5, 0.0, 3.0, 0.0])
    events = pd.DatetimeIndex([returns.index[2], returns.index[5], pd.Timestamp("2030-01-01")])
    found = run("qrs.event_study", Given({"returns": returns}, events=events), before=1, after=1)
    profile = found.tables["profile"].set_index("offset")
    assert profile.loc[0, "mean"] == pytest.approx(0.5) and profile.loc[0, "mean_abs"] == pytest.approx(2.5)
    # The sessions before the events returned 1.0 and 0.0; those after 0.5 and 0.0.
    assert profile.loc[-1, "mean"] == pytest.approx(0.5) and profile.loc[1, "mean"] == pytest.approx(0.25) and profile.loc[1, "events"] == 2
    assert found.values["mean_abs_event"] == pytest.approx(2.5)
    assert found.values["mean_abs_other"] == pytest.approx(1.5 / 5)
    assert found.values["events"] == 2 and "1 event sessions have no return" in found.warnings[0]


def test_feature_against_forward_return_pairs_each_feature_with_the_next_sessions():
    returns = days([1.0, 2.0, 3.0, 4.0, 5.0])
    feature = days([10.0, 20.0, 30.0, 40.0, 50.0])
    found = run("qrs.feature_return", Given({"feature": feature, "returns": returns}, additive=True), horizon=2)
    pairs = found.tables["pairs"]
    # The forward return at t sums t + 1 and t + 2, so only the first three sessions have one.
    assert pairs["forward"].tolist() == [5.0, 7.0, 9.0] and pairs["feature"].tolist() == [10.0, 20.0, 30.0]
    assert (found.values["spearman"], found.values["n"], found.values["effective_n"]) == (1.0, 3, 1)
    compounded = run("qrs.feature_return", Given({"feature": feature, "returns": days([0.1, 0.1, 0.1])}), horizon=2)
    assert compounded.tables["pairs"]["forward"].tolist() == pytest.approx([0.21])


def test_coverage_of_bars_and_observations(tmp_path):
    bars = import_csv(tmp_path, BARS_CSV, ImportDeclaration.model_validate(bars_declaration()))
    observations = import_observations(tmp_path, OBSERVATIONS_CSV, observations_declaration())
    _, of_bars = analyse(tmp_path, document({"data": {"kind": "bars", "dataset": bars}}, "qrs.coverage"), tmp_path / "bars")
    assert (of_bars["values"]["sessions"], of_bars["values"]["missing_sessions"], of_bars["values"]["last"]) == (9, 0, "2024-12-06")
    given = {"data": {"kind": "observations", "dataset": observations, "column": "Rate", "on": bars}}
    _, of_rows = analyse(tmp_path, document(given, "qrs.coverage", until="2024-12-04"), tmp_path / "rows")
    # Published by the 3rd's close: A, B, C and both revisions (C's at 20:00 UTC, before the close).
    assert {k: of_rows["values"][k] for k in ("rows", "events", "revisions", "estimated_rows")} == {"rows": 5, "events": 3, "revisions": 2, "estimated_rows": 0}
    # C, the week of the 29th, was published that day at 19:00 UTC; B's revision, for the week of the
    # 22nd, on 3 December at 14:00 UTC, 11 days and 14 hours after its event date.
    assert of_rows["values"]["lag_days"]["min"] == pytest.approx(19 / 24)
    assert of_rows["values"]["lag_days"]["max"] == pytest.approx(11 + 14 / 24)


def test_methods_run_end_to_end_with_figures(tmp_path):
    bars = import_csv(tmp_path, BARS_CSV, ImportDeclaration.model_validate(bars_declaration()))
    observations = import_observations(tmp_path, OBSERVATIONS_CSV, observations_declaration())
    returns = {"kind": "bars", "dataset": bars}
    events = {"kind": "observations", "dataset": observations, "column": "Rate", "on": bars}
    record, result = analyse(tmp_path, document({"returns": returns, "events": events}, "qrs.event_study", params={"before": 1, "after": 1}), tmp_path / "e")
    assert set(record["outputs"]) == {"result.json", "tables/profile.csv", "figures/profile.svg"}
    assert result["outputs"] == {"tables/profile.csv": "aggregates", "figures/profile.svg": "aggregates"}
    # Published on the 22nd (before the bars), the 27th at the close, the 29th after the early close
    # (so the 2nd), and revisions known at the 3rd's close: events on the 25th, 27th, 2nd and 3rd.
    assert result["values"]["events"] == 3  # the 25th has no return: it is the first session
    record, result = analyse(tmp_path, document({"feature": events, "returns": returns}, "qrs.feature_return"), tmp_path / "f")
    assert result["outputs"]["figures/scatter.svg"] == "rows" and result["values"]["n"] == 7
    again, _ = analyse(tmp_path, document({"feature": events, "returns": returns}, "qrs.feature_return"), tmp_path / "g")
    assert again["outputs"] == record["outputs"]


def test_figures_are_byte_identical_escaped_well_formed_svg():
    hostile = '<script>alert("x")</script> & co'
    drawn = [
        figures.line(hostile, ["a", "b", "c"], {"one": [1.0, float("nan"), 3.0], hostile: [2.0, 2.5, -1.0]}, hostile),
        figures.bar(hostile, [hostile, "b"], [1.0, -2.0], "y"),
        figures.scatter(hostile, [1.0, 2.0, float("nan")], [3.0, 4.0, 5.0], hostile, "y"),
        figures.heatmap(hostile, ["r1", hostile], ["c1", "c2"], [[1.0, 2.0], [float("nan"), 4.0]], "x", "y"),
        figures.intervals(hostile, ["a", "b"], [0.5, 1.0], [0.1, float("nan")], [0.9, 2.0], "y"),
    ]
    for svg in drawn:
        root = ElementTree.fromstring(svg)
        assert root.tag == "{http://www.w3.org/2000/svg}svg"
        assert "<script" not in svg and "&lt;script&gt;" in svg and "&amp; co" in svg
        assert not any(el.tag.endswith("script") or el.tag.endswith("foreignObject") for el in root.iter())
    assert figures.line("t", ["a", "b"], {"s": [0.1 + 0.2, 0.3]}, "y") == figures.line("t", ["a", "b"], {"s": [0.1 + 0.2, 0.3]}, "y")
    coordinates = re.findall(r' (?:x|y|cx|cy|x1|x2|y1|y2|width|height|points)="([^"]+)"', figures.scatter("t", [1 / 3, 1], [2 / 3, 0], "x", "y"))
    assert coordinates and all(re.fullmatch(r"-?\d+(\.\d{1,2})?", n) for c in coordinates for n in re.split("[ ,]", c))


def test_descriptive_methods_meet_their_budget():
    """Fifty years of daily data by 20 series in under 10 s."""
    rng = np.random.default_rng(1)
    start = time.perf_counter()
    for _ in range(20):
        values = pd.Series(rng.normal(0, 0.01, 12_600), index=pd.bdate_range("1975-01-01", periods=12_600))
        context = Given({"values": values, "returns": values, "feature": values.shift(1)}, events=values.index[::21])
        for method_id in ("qrs.summary", "qrs.distribution", "qrs.rolling", "qrs.seasonal", "qrs.event_study", "qrs.feature_return"):
            run(method_id, context)
    assert time.perf_counter() - start < 10


def test_larger_inputs_and_figures_are_handled_by_the_service(home, monkeypatch):
    monkeypatch.setattr(analysis, "MAX_SESSIONS", 100)
    with client_for(home) as client:
        dataset = client.get("/api/datasets").json()[0]["id"]
        refused = client.post("/api/analyses", json=document({"values": {"kind": "bars", "dataset": dataset}}).model_dump(mode="json"))
        assert refused.status_code == 422 and "has 500 sessions; an analysis may read at most 100" in refused.json()["detail"]
        monkeypatch.setattr(analysis, "MAX_SESSIONS", 20_000)
        queued = client.post(
            "/api/analyses", json=document({"values": {"kind": "bars", "dataset": dataset}}, "qrs.distribution").model_dump(mode="json")
        ).json()
        assert wait_analysis(client, queued["id"])["status"] == "succeeded"
        figure = client.get(f"/api/analyses/{queued['id']}/figures/histogram")
        assert figure.status_code == 200 and figure.headers["content-type"] == "image/svg+xml"
        assert "sandbox" in figure.headers["content-security-policy"] and figure.text.startswith("<svg")
        assert client.get(f"/api/analyses/{queued['id']}/figures/..%2Fresult").status_code == 404
        assert client.get(f"/api/analyses/{queued['id']}/result").json()["tables"]["histogram"][0]["count"] >= 0


def test_until_is_applied_to_dates(tmp_path):
    dataset = datasets.create_synthetic(tmp_path)
    _, result = analyse(tmp_path, document({"data": {"kind": "bars", "dataset": dataset}}, "qrs.coverage", until=date(2024, 3, 1)), tmp_path / "c")
    assert result["values"]["last"] == "2024-02-29"


def test_a_runs_cutoff_cuts_the_other_inputs_and_forward_returns_stop_before_it(tmp_path):
    dataset = datasets.create_synthetic(tmp_path)
    cut = run_with_record(tmp_path, date(2024, 10, 1))
    feature = {"kind": "record", "run": cut, "node": "kept", "column": "fast"}
    _, result = analyse(
        tmp_path, document({"feature": feature, "returns": {"kind": "bars", "dataset": dataset}}, "qrs.feature_return", params={"horizon": 3}), tmp_path / "f"
    )
    pairs = pd.read_csv(tmp_path / "f" / "tables" / "pairs.csv", index_col=0, parse_dates=True)
    sessions = datasets.load(tmp_path, dataset).frame.index
    last_read = sessions[sessions < "2024-10-01"][-1]
    # The last pair's forward return ends at the last session read, three sessions after it.
    assert result["cutoff"] == "2024-10-01" and pairs.index[-1] == sessions[sessions.get_loc(last_read) - 3]


# Regressions from the workstream's review.


def test_inputs_on_different_exchange_calendars_are_refused(tmp_path):
    """A value published at 20:00 UTC is known at the NYSE close (21:00) but not at the CBOT close
    (19:20): matched by date alone, it would be paired with a return that ended before it."""
    nyse = import_csv(tmp_path, BARS_CSV, ImportDeclaration.model_validate(bars_declaration()))
    cbot = import_csv(tmp_path, BARS_CSV, ImportDeclaration.model_validate(bars_declaration(calendar="CBOT_Agriculture")))
    observations = import_observations(tmp_path, OBSERVATIONS_CSV, observations_declaration())
    feature = {"kind": "observations", "dataset": observations, "column": "Rate", "on": nyse}
    with pytest.raises(analysis.AnalysisError, match=r"one exchange calendar.*\(returns on CBOT_Agriculture; feature on NYSE\)"):
        analysis.check(document({"feature": feature, "returns": {"kind": "bars", "dataset": cbot}}, "qrs.feature_return"), tmp_path, lambda _: None)
    analysis.check(document({"feature": feature, "returns": {"kind": "bars", "dataset": nyse}}, "qrs.feature_return"), tmp_path, lambda _: None)


def test_event_sessions_are_the_first_close_at_or_after_each_publication(tmp_path):
    bars = import_csv(tmp_path, BARS_CSV, ImportDeclaration.model_validate(bars_declaration()))
    observations = import_observations(tmp_path, OBSERVATIONS_CSV, observations_declaration())
    doc = document(
        {"returns": {"kind": "bars", "dataset": bars}, "events": {"kind": "observations", "dataset": observations, "column": "Rate", "on": bars}},
        "qrs.event_study",
    )
    # A on the 22nd precedes the bars, so has no session; B at the 27th's close exactly; C after the
    # 29th's early close, so the 2nd; both revisions before the 3rd's close.
    assert analysis.Context(doc, tmp_path).events("events").strftime("%Y-%m-%d").tolist() == ["2024-11-27", "2024-12-02", "2024-12-03"]


def test_price_changes_add_up_through_the_context(tmp_path):
    dataset = datasets.create_synthetic(tmp_path)
    _, result = analyse(tmp_path, document({"returns": {"kind": "bars", "dataset": dataset, "measure": "price_change"}}, "qrs.seasonal"), tmp_path / "s")
    close = datasets.load(tmp_path, dataset).frame["close"]
    february = close[(close.index >= "2024-01-31") & (close.index < "2024-03-01")]
    table = pd.read_csv(tmp_path / "s" / "tables" / "monthly.csv", index_col="year")
    assert table.loc[2024, "Feb"] == pytest.approx((february.iloc[-1] - february.iloc[0]) * 50)


def test_rolling_windows_with_a_missing_value_are_missing():
    found = run("qrs.rolling", Given({"values": days([1.0, np.nan, 3.0, 4.0, 5.0])}), period=2)
    assert np.isnan(found.tables["rolling"]["std"].iloc[:3]).all() and found.tables["rolling"]["std"].tolist()[3:] == [0.5, 0.5]


def test_coverage_counts_missing_sessions(tmp_path):
    without = b"\n".join(line for line in BARS_CSV.split(b"\n") if not line.startswith(b"2024-12-03"))
    bars = import_csv(tmp_path, without, ImportDeclaration.model_validate(bars_declaration()))
    _, result = analyse(tmp_path, document({"data": {"kind": "bars", "dataset": bars}}, "qrs.coverage"), tmp_path / "c")
    assert (result["values"]["sessions"], result["values"]["missing_sessions"]) == (8, 1)
    assert pd.read_csv(tmp_path / "c" / "tables" / "missing.csv")["session"].tolist() == ["2024-12-03"]


def test_forward_returns_compound_losses_beyond_the_whole_stake_and_constants_are_warned():
    assert methods.forward(days([0.1, -1.5, 0.3]), 1, additive=False).tolist()[:2] == pytest.approx([-1.5, 0.3])
    found = run("qrs.feature_return", Given({"feature": days([1.0] * 5), "returns": days([0.1, 0.2, 0.3, 0.4, 0.5])}))
    assert found.values["spearman"] is None and "constant" in found.warnings[-1]
    assert run("qrs.distribution", Given({"values": days([1.0, np.nan, 2.0])})).values["n"] == 2


def test_svg_is_byte_identical_in_a_fresh_process_and_drops_control_characters():
    golden = "3716b5bf958341cffff70d3602ac3e00790f8a51f940b25320ce009b47f3daa6"
    assert hashlib.sha256(figures.bar("Golden", ["a", "b"], [1.0, -2.0], "y").encode()).hexdigest() == golden
    code = "import hashlib; from qrs import figures; print(hashlib.sha256(figures.bar('Golden', ['a', 'b'], [1.0, -2.0], 'y').encode()).hexdigest())"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip() == golden
    ElementTree.fromstring(figures.bar("a\x01b", ["c\x02"], [1.0], "y"))


def test_figures_of_unfinished_analyses_and_undeclared_figures_are_refused(home):
    store = Store(home / "qrs.sqlite")
    store.create_analysis("0123456789ab", "failed", "qrs.distribution@1")
    store.update_job("analysis", "0123456789ab", status="failed")
    with client_for(home) as client:
        assert client.get("/api/analyses/0123456789ab/figures/histogram").status_code == 409
        dataset = client.get("/api/datasets").json()[0]["id"]
        queued = client.post(
            "/api/analyses", json=document({"values": {"kind": "bars", "dataset": dataset}}, "qrs.distribution").model_dump(mode="json")
        ).json()
        wait_analysis(client, queued["id"])
        missing = client.get(f"/api/analyses/{queued['id']}/figures/rolling")
        assert missing.status_code == 404 and "has no figure rolling" in missing.json()["detail"]


def test_an_analysis_of_fifty_years_writes_its_table_and_figure_within_budget(tmp_path):
    dataset = datasets.save(
        tmp_path,
        datasets.synthetic_bars(9, 12_600, start="1976-01-02"),
        name="long",
        instrument=datasets.SYNTHETIC_INSTRUMENT,
        evidence_class="synthetic",
        source="tests",
        redistributable=True,
    )
    start = time.perf_counter()
    analyse(tmp_path, document({"values": {"kind": "bars", "dataset": dataset}}, "qrs.rolling"), tmp_path / "r")
    assert time.perf_counter() - start < 10 / 20  # one of the 20 series the budget allows 10 s for
