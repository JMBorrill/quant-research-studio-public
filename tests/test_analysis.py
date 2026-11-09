"""Stage 5 workstream 2: analysis runs. Documents are checked before they are queued; inputs are read
through one context that applies the effective cutoff; the worker writes an immutable directory with
a record and hashes; failed and cancelled analyses are recorded; and runs are unaffected. SciPy, which
analyses use, is never imported by strategy execution (amendment A1)."""

import io
import json
import re
import subprocess
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_market_calendars
import pytest

import fixture_ops  # noqa: F401  registers tests.sleep for validation in this process
from conftest import client_for, new_strategy, save, start, wait
from qrs import analysis, cli, compiler, datasets, export, methods, templates
from qrs.contracts import AnalysisDocument, ImportDeclaration
from qrs.importer import import_csv, import_observations
from qrs.registry import Params
from qrs.runtime import PACKAGE_DIR, execute
from qrs.store import Store
from test_observations import BARS_CSV, OBSERVATIONS_CSV, bars_declaration, observations_declaration
from test_record import recording_strategy

ROOT = Path(__file__).parents[1]


def document(inputs: dict, method: str = "qrs.summary", **changes) -> AnalysisDocument:
    return AnalysisDocument.model_validate({"name": "test", "method": method, "method_version": 1, "inputs": inputs, **changes})


def analyse(home: Path, doc: AnalysisDocument, out: Path) -> tuple[dict, dict]:
    """Run an analysis through the CLI, in this process; its record and result."""
    out.parent.mkdir(parents=True, exist_ok=True)
    (out.parent / f"{out.name}.json").write_text(doc.model_dump_json())
    assert cli.main(["analyse", str(out.parent / f"{out.name}.json"), "--home", str(home), "--out", str(out)]) == 0
    return json.loads((out / "record.json").read_text()), json.loads((out / "result.json").read_text())


class Given:
    """A context that supplies one series, for checking a method by hand."""

    def __init__(self, values):
        self.values = pd.Series(values, index=pd.bdate_range("2024-01-01", periods=len(values)), dtype=float)

    def series(self, name):
        return self.values


def test_summary_matches_hand_calculations():
    found = methods.get("qrs.summary", 1).run(Given([1.0, 2.0, 3.0, 4.0, 10.0, np.nan]), Params()).values
    # Mean 4; deviations -3, -2, -1, 0, 6: sum of squares 50, so the sample variance is 50 / 4 = 12.5.
    # Third moment (−27 − 8 − 1 + 216) / 5 = 36, second 50 / 5 = 10: g1 = 36 / 10^1.5 = 1.138420, and
    # G1 = g1 √(n(n − 1)) / (n − 2) = 1.138420 · √20 / 3 = 1.697056 (Excel's SKEW).
    # Fourth moment (81 + 16 + 1 + 1296) / 5 = 278.8, g2 = 278.8 / 100 − 3 = −0.212, and
    # G2 = ((n + 1) g2 + 6)(n − 1) / ((n − 2)(n − 3)) = (6 · −0.212 + 6) · 4 / 6 = 3.152 (Excel's KURT).
    assert (found["n"], found["effective_n"], found["missing"], found["mean"]) == (5, 5, 1, 4.0)
    assert found["std"] == pytest.approx(12.5**0.5, rel=1e-12)
    assert found["skewness"] == pytest.approx(1.697056, abs=1e-6)
    assert found["excess_kurtosis"] == pytest.approx(3.152, abs=1e-12)
    assert found["quantiles"]["0.5"] == 3.0 and found["quantiles"]["0.25"] == 2.0 and found["quantiles"]["0.95"] == pytest.approx(8.8)


def test_an_analysis_reproduces_its_hash_and_records_what_it_read(tmp_path):
    dataset_id = datasets.create_synthetic(tmp_path)
    doc = document({"values": {"kind": "bars", "dataset": dataset_id}}, until="2024-12-02")
    record, result = analyse(tmp_path, doc, tmp_path / "a")
    again, _ = analyse(tmp_path, doc, tmp_path / "b")
    assert again["result_sha256"] == record["result_sha256"] and again == record
    close = datasets.load(tmp_path, dataset_id).frame["close"]
    close = close[close.index < "2024-12-02"]
    assert result["values"]["n"] == len(close) - 1
    assert result["values"]["mean"] == pytest.approx((close / close.shift(1) - 1).mean(), rel=1e-12)
    assert (result["cutoff"], result["evidence_class"], record["cutoff"]) == ("2024-12-02", "synthetic", "2024-12-02")
    assert record["inputs"] == {"values": {"kind": "bars", "dataset": dataset_id, "measure": "percent"}}
    assert record["scipy"] and "scipy" not in record["runtime"]["packages"]
    assert record["method"]["key"] == "qrs.summary@1" and set(record["outputs"]) == {"result.json"}
    # Price changes per contract: the close's change times the multiplier.
    _, per_contract = analyse(tmp_path, document({"values": {"kind": "bars", "dataset": dataset_id, "measure": "price_change"}}), tmp_path / "c")
    every_close = datasets.load(tmp_path, dataset_id).frame["close"]
    assert per_contract["values"]["mean"] == pytest.approx(every_close.diff().mean() * 50, rel=1e-9)


def test_leakage_probe_rewriting_bars_after_the_cutoff_changes_nothing(tmp_path):
    frame = datasets.synthetic_bars(3, 300)
    later = frame.index >= "2024-08-01"
    rewritten = frame.copy()
    rewritten.loc[later] = (frame.loc[later] * 1.3 / 0.25).round() * 0.25
    ids = [
        datasets.save(tmp_path, f, name=n, instrument=datasets.SYNTHETIC_INSTRUMENT, evidence_class="synthetic", source="tests", redistributable=True)
        for f, n in ((frame, "a"), (rewritten, "a"))
    ]
    results = [analyse(tmp_path, document({"values": {"kind": "bars", "dataset": i}}, until="2024-08-01"), tmp_path / f"out{n}") for n, i in enumerate(ids)]
    assert ids[0] != ids[1] and results[0][0]["result_sha256"] == results[1][0]["result_sha256"]


def test_leakage_probe_observations_published_after_the_last_close_change_nothing(tmp_path):
    bars = import_csv(tmp_path, BARS_CSV, ImportDeclaration.model_validate(bars_declaration()))
    rows = b"""Week,Published,Release,Rate
2024-11-15,2024-11-22T08:30:00-05:00,first,1.0
2024-11-22,2024-11-27T21:00:00+00:00,first,2.0
2024-11-29,2024-12-03T22:00:00+00:00,first,%s
"""
    # The last row is published after the close of 3 December, the last session before the 4th.
    ids = [import_observations(tmp_path, rows % value, observations_declaration()) for value in (b"3.0", b"99.0")]
    found = []
    for n, observations in enumerate(ids):
        given = {"values": {"kind": "observations", "dataset": observations, "column": "Rate", "on": bars, "max_age_days": 30}}
        found.append(analyse(tmp_path, document(given, until="2024-12-04"), tmp_path / f"o{n}"))
    assert found[0][0]["result_sha256"] == found[1][0]["result_sha256"]
    # A on the 25th and 26th; B from its publication at the 27th's close, to the 3rd.
    assert found[0][1]["values"]["n"] == 6 and found[0][1]["values"]["mean"] == pytest.approx((1 + 1 + 2 + 2 + 2 + 2) / 6)


def probed_methods() -> list[str]:
    """Every method reading datasets; those reading experiments read trials run only to their holdout
    start, which test_selection checks."""
    return [m.key for m in methods.all_methods() if any(set(kinds) - {"experiment"} for kinds in m.inputs.values())]


@pytest.mark.parametrize("key", probed_methods())
def test_leakage_probe_every_method_ignores_bars_and_observations_after_the_cutoff(tmp_path, key):
    """Criterion 4: two stores alike before the cutoff, with every bar from it and every observation
    published after the last close before it rewritten, give every method the same result hash."""
    frame = datasets.synthetic_bars(3, 650)
    frame = frame[frame.index.isin(pandas_market_calendars.get_calendar("NYSE").valid_days("2024-01-01", "2027-01-01").tz_localize(None))]
    cutoff = frame.index[400]
    rewritten = frame.copy()
    rewritten.loc[frame.index >= cutoff] = (frame.loc[frame.index >= cutoff] * 1.3 / 0.25).round() * 0.25
    rewritten = rewritten.drop(frame.index[401::7])  # and sessions missing
    fridays = frame.index[frame.index.dayofweek == 4]
    rates = np.random.default_rng(5).normal(size=len(fridays)).round(3)
    method = next(m for m in methods.all_methods() if m.key == key)
    hashes = []
    for n, bars in enumerate((frame, rewritten)):
        home = tmp_path / str(n)
        csv = bars.rename_axis("Date").rename(columns=str.title)[["Open", "High", "Low", "Close"]].to_csv()
        bars_id = import_csv(home, csv.encode(), ImportDeclaration.model_validate(bars_declaration()))
        published = fridays + pd.Timedelta(days=3, hours=14)
        values = np.where(published >= cutoff, rates + 7 * n, rates)  # published on the cutoff's date or later: after the last close before it
        csv = "Week,Published,Release,Rate\n" + "".join(
            f"{f.date()},{p.isoformat()}+00:00,first,{v}\n" for f, p, v in zip(fridays, published, values, strict=True)
        )
        observations_id = import_observations(home, csv.encode(), observations_declaration())
        # A second bars input reads price changes, so that two inputs are never the same series.
        measures = iter(["percent", "price_change"])
        observations = {"kind": "observations", "dataset": observations_id, "column": "Rate", "on": bars_id}
        inputs = {
            name: {"kind": "bars", "dataset": bars_id, "measure": next(measures)} if "bars" in kinds else observations for name, kinds in method.inputs.items()
        }
        record, _ = analyse(home, document(inputs, method.id, method_version=method.version, until=str(cutoff.date())), home / "out")
        hashes.append(record["result_sha256"])
    assert hashes[0] == hashes[1]


def run_with_record(home: Path, until: date | None, doc=None) -> str:
    """A succeeded run, as the service would leave it, of a strategy (by default the EMA crossover
    with a record); its ID."""
    run_id = "a1b2c3d4e5f6" if until else "f6e5d4c3b2a1"
    folder = home / "runs" / run_id
    folder.mkdir(parents=True)
    doc = doc or recording_strategy(next(iter(datasets.list_manifests(home))))
    (folder / "strategy.json").write_text(doc.model_dump_json())
    (folder / "strategy.py").write_text(compiler.compile_strategy(doc))
    if until:
        (folder / "until.txt").write_text(until.isoformat())
    execute(folder / "strategy.py", home, folder, until)
    store = Store(home / "qrs.sqlite")
    store.create_run(run_id, "s", 1, "code")
    store.update_run(run_id, status="succeeded")
    return run_id


def test_run_outputs_are_never_read_past_the_runs_own_cutoff(tmp_path):
    datasets.create_synthetic(tmp_path)
    cut = run_with_record(tmp_path, date(2024, 10, 1))
    full = run_with_record(tmp_path, None)
    for name, given in (("equity", {"kind": "equity", "run": cut}), ("record", {"kind": "record", "run": cut, "node": "kept", "column": "change"})):
        record, result = analyse(tmp_path, document({"values": given}, until="2025-06-01"), tmp_path / name)
        assert result["cutoff"] == "2024-10-01" and record["inputs"]["values"]["result_sha256"]
    # A run without a cutoff is cut at the analysis's, and gives the same numbers as the cut run there.
    _, cut_result = analyse(tmp_path, document({"values": {"kind": "record", "run": cut, "node": "kept", "column": "change"}}), tmp_path / "x")
    _, full_result = analyse(
        tmp_path, document({"values": {"kind": "record", "run": full, "node": "kept", "column": "change"}}, until="2024-10-01"), tmp_path / "y"
    )
    assert cut_result["values"] == full_result["values"]
    # A run whose output was changed after it finished is refused.
    (tmp_path / "runs" / full / "records" / "kept.parquet").chmod(0o644)
    (tmp_path / "runs" / full / "records" / "kept.parquet").write_bytes(b"changed")
    with pytest.raises(analysis.AnalysisError, match="does not match the hash"):
        analysis.execute(tmp_path / "y" / "analysis.json", tmp_path, tmp_path / "z")


def test_fills_are_read_per_session_and_models_at_each_fit(tmp_path):
    """Criterion 9: analyses read a run's fills and fitted models, cut at the effective cutoff."""
    datasets.create_synthetic(tmp_path)
    run_id = run_with_record(tmp_path, None)
    results = tmp_path / "runs" / run_id / "results" / "backtest"
    fills = pd.read_csv(results / "fills.csv", parse_dates=["session"])
    fills = fills[fills["session"] < "2024-10-01"]
    signed = fills["quantity"].where(fills["side"] == "BUY", -fills["quantity"])
    sessions = pd.read_csv(results / "equity.csv", parse_dates=["session"])["session"]
    for column, expected in (("quantity", signed), ("commission", fills["commission"])):
        given = {"kind": "fills", "run": run_id, "column": column}
        record, result = analyse(tmp_path, document({"values": given}, until="2024-10-01"), tmp_path / column)
        assert result["values"]["n"] == (sessions < "2024-10-01").sum()  # every session, 0 without a fill
        assert result["values"]["mean"] * result["values"]["n"] == pytest.approx(expected.sum(), rel=1e-12)
        assert record["inputs"]["values"] == given | {"result_sha256": record["inputs"]["values"]["result_sha256"]}

    home = tmp_path / "macro"
    chosen = templates.prepare("macro_ridge", home)
    ridge = run_with_record(home, None, templates.instantiate("macro_ridge", chosen=chosen))
    fits = json.loads((home / "runs" / ridge / "models" / "fit.json").read_text())
    cutoff = fits[3]["cutoff"]
    for term, values in (("intercept", [f["intercept"] for f in fits[:3]]), (fits[0]["features"][1], [f["coefficients"][1] for f in fits[:3]])):
        _, result = analyse(home, document({"values": {"kind": "model", "run": ridge, "node": "fit", "term": term}}, until=cutoff), home / term)
        assert result["values"]["n"] == 3 and result["values"]["mean"] == pytest.approx(np.mean(values), rel=1e-12)
    with pytest.raises(analysis.AnalysisError, match=r"the models of 'fit' have no term 'nothing'; they have \['change'"):
        analysis.check(document({"values": {"kind": "model", "run": ridge, "node": "fit", "term": "nothing"}}), home, lambda _: "succeeded")
    with pytest.raises(analysis.AnalysisError, match="fitted no models in a node 'predict'"):
        analysis.check(document({"values": {"kind": "model", "run": ridge, "node": "predict", "term": "intercept"}}), home, lambda _: "succeeded")


@pytest.mark.parametrize(
    "inputs, method, message",
    [
        ({"values": {"kind": "bars", "dataset": "sha256:" + "e" * 64}}, "qrs.summary", "not found"),
        ({"other": {"kind": "equity", "run": "000000000000"}}, "qrs.summary", r"takes the inputs \['values'\]"),
        ({"values": {"kind": "equity", "run": "000000000000"}}, "qrs.summary", "run 000000000000 does not exist"),
        ({"values": {"kind": "bars", "dataset": "sha256:" + "e" * 64}}, "qrs.nothing", "Unknown method qrs.nothing"),
    ],
)
def test_documents_that_cannot_run_are_refused_before_queuing(home, inputs, method, message):
    with client_for(home) as client:
        refused = client.post("/api/analyses", json=document(inputs, method).model_dump(mode="json"))
        assert refused.status_code == 422 and re.search(message, refused.json()["detail"]), refused.json()
        assert client.get("/api/analyses").json() == []


def test_the_service_runs_analyses_and_records_failed_and_cancelled_ones(home):
    with client_for(home) as client:
        dataset = client.get("/api/datasets").json()[0]["id"]
        methods_listed = client.get("/api/methods").json()
        summary = next(m for m in methods_listed if m["id"] == "qrs.summary")
        assert summary["inputs"]["values"] == ["bars", "observations", "equity", "record", "fills", "model"]
        queued = client.post("/api/analyses", json=document({"values": {"kind": "bars", "dataset": dataset}}).model_dump(mode="json")).json()
        done = wait_analysis(client, queued["id"])
        assert done["status"] == "succeeded" and done["method"] == "qrs.summary@1"
        shown = client.get(f"/api/analyses/{queued['id']}/result").json()
        assert shown["result"]["values"]["n"] == 499 and shown["record"]["result_sha256"] == done["result_sha256"]

        # A run occupies the worker; an analysis queued behind it is cancelled before it starts.
        strategy = new_strategy(client)
        doc = strategy["document"]
        doc["nodes"].append({"id": "pause", "operator": "tests.sleep", "version": 1, "params": {"seconds": 30}})
        doc["edges"] = [e for e in doc["edges"] if e["target"]["node"] != "backtest" or e["target"]["port"] != "bars"]
        doc["edges"] += [
            {"source": {"node": "bars", "port": "bars"}, "target": {"node": "pause", "port": "bars"}},
            {"source": {"node": "pause", "port": "bars"}, "target": {"node": "backtest", "port": "bars"}},
        ]
        slow = start(client, strategy, save(client, strategy, doc))
        wait(client, slow["id"], until=("running",))
        behind = client.post("/api/analyses", json=document({"values": {"kind": "bars", "dataset": dataset}}).model_dump(mode="json")).json()
        cancelled = client.post(f"/api/analyses/{behind['id']}/cancel").json()
        assert (cancelled["status"], cancelled["error"]) == ("cancelled", "Cancelled before it started.")
        client.post(f"/api/runs/{slow['id']}/cancel")
        assert wait(client, slow["id"])["status"] == "cancelled"

        # A record column that does not exist fails in the worker, and the failure is kept.
        run = start(client, new_strategy(client))
        assert wait(client, run["id"])["status"] == "succeeded"
        failing = client.post("/api/analyses", json=document({"values": {"kind": "equity", "run": run["id"]}}).model_dump(mode="json")).json()
        assert wait_analysis(client, failing["id"])["status"] == "succeeded"
        missing = document({"values": {"kind": "record", "run": run["id"], "node": "kept", "column": "x"}}).model_dump(mode="json")
        assert client.post("/api/analyses", json=missing).status_code == 422  # no such record node: refused up front
        listed = client.get("/api/analyses").json()
        assert sorted(a["status"] for a in listed) == ["cancelled", "succeeded", "succeeded"]
        assert client.get(f"/api/analyses/{behind['id']}/result").status_code == 409


def test_failed_and_interrupted_analyses_are_kept(home):
    dataset = datasets.list_manifests(home).popitem()[0]
    # A time limit too short for any worker to finish fails the analysis, and the failure is kept.
    with client_for(home, time_limit=0.05) as client:
        queued = client.post("/api/analyses", json=document({"values": {"kind": "bars", "dataset": dataset}}).model_dump(mode="json")).json()
        done = wait_analysis(client, queued["id"])
        assert (done["status"], done["error"]) == ("failed", "Stopped after the time limit of 0.05 seconds.")
        assert (home / "analyses" / done["id"] / "analysis.json").exists()
    # An analysis left running by a service that stopped is interrupted, and published, at the next start.
    store = Store(home / "qrs.sqlite")
    store.create_analysis("0123456789ab", "left running", "qrs.summary@1")
    store.update_job("analysis", "0123456789ab", status="running")
    (home / "analyses" / ".staging" / "0123456789ab").mkdir(parents=True)
    with client_for(home) as client:
        left = client.get("/api/analyses/0123456789ab").json()
        assert (left["status"], left["error"]) == ("interrupted", "The service stopped while this analysis was in progress.")
        assert (home / "analyses" / "0123456789ab").is_dir()


def test_runs_and_analyses_share_one_queue_in_the_order_they_were_queued(tmp_path):
    store = Store(tmp_path / "qrs.sqlite")
    for queue in (lambda: store.create_analysis("aaaaaaaaaaaa", "first", "qrs.summary@1"), lambda: store.create_run("bbbbbbbbbbbb", "s", 1, "code")):
        queue()
        time.sleep(0.002)  # queue times are kept to the millisecond
    store.create_analysis("cccccccccccc", "third", "qrs.summary@1")
    order = []
    while (job := store.next_queued()) is not None:
        order.append(job)
        store.update_job(*job, status="succeeded")
    assert order == [("analysis", "aaaaaaaaaaaa"), ("run", "bbbbbbbbbbbb"), ("analysis", "cccccccccccc")]


def test_a_run_queued_in_the_same_millisecond_as_an_analysis_goes_first(tmp_path, monkeypatch):
    """A hypothesis's holdout evaluation and the analysis reading it may be queued in one millisecond;
    the analysis must not start before the run it reads."""
    monkeypatch.setattr("qrs.store.now", lambda: "2026-01-01T00:00:00.000+00:00")
    store = Store(tmp_path / "qrs.sqlite")
    store.create_run("bbbbbbbbbbbb", "s", 1, "code")
    store.create_analysis("cccccccccccc", "reads the second run", "qrs.summary@1")
    store.create_run("dddddddddddd", "s", 1, "code")
    order = []
    while (job := store.next_queued()) is not None:
        order.append(job)
        store.update_job(*job, status="succeeded")
    assert order == [("run", "bbbbbbbbbbbb"), ("run", "dddddddddddd"), ("analysis", "cccccccccccc")]


def test_equity_from_a_full_run_cut_at_a_date_equals_a_run_cut_there(tmp_path):
    datasets.create_synthetic(tmp_path)
    cut, full = run_with_record(tmp_path, date(2024, 10, 1)), run_with_record(tmp_path, None)
    _, from_cut = analyse(tmp_path, document({"values": {"kind": "equity", "run": cut}}), tmp_path / "cut")
    _, from_full = analyse(tmp_path, document({"values": {"kind": "equity", "run": full}}, until="2024-10-01"), tmp_path / "full")
    assert from_cut["values"] == from_full["values"] and from_cut["cutoff"] == from_full["cutoff"] == "2024-10-01"


def test_inputs_a_worker_could_not_read_are_refused_before_queuing(tmp_path):
    dataset = datasets.create_synthetic(tmp_path)
    run_id = run_with_record(tmp_path, None)
    store = Store(tmp_path / "qrs.sqlite")
    for column in ("x", "session"):  # the session is a record's index, not a column
        with pytest.raises(analysis.AnalysisError, match=f"record 'kept' has no column '{column}'; it has \\['fast', 'change'\\]"):
            analysis.check(document({"values": {"kind": "record", "run": run_id, "node": "kept", "column": column}}), tmp_path, store.run_status)
    # Observations are joined at closes, which bars without an exchange calendar do not have.
    observations = import_observations(tmp_path, OBSERVATIONS_CSV, observations_declaration())
    joined = {"kind": "observations", "dataset": observations, "column": "Rate", "on": dataset}
    with pytest.raises(analysis.AnalysisError, match="has no exchange calendar"):
        analysis.check(document({"values": joined}), tmp_path, store.run_status)
    with pytest.raises(analysis.AnalysisError, match="starts on 2024-01-02, so nothing is read before the cutoff 2024-01-02"):
        analysis.check(document({"values": {"kind": "bars", "dataset": dataset}}, until="2024-01-02"), tmp_path, store.run_status)


def test_analyses_are_refused_without_scipy_naming_the_extra():
    # The CLI and the service load without it, so they can refuse analyses themselves.
    code = "import sys; sys.modules['scipy'] = None\nimport qrs.api, qrs.cli\nfrom qrs import methods\n"
    code += "try:\n    methods.get('qrs.summary', 1)\nexcept methods.MethodError as e:\n    print(e)"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.stdout.strip() == "Analyses need SciPy: install the optional extra, qrs[research].", done.stderr


def test_values_hold_arrays_with_missing_numbers_as_null():
    assert analysis.plain({"a": np.array([1.0, np.nan]), "b": pd.Series([np.float64(2.0)]), "c": np.int64(3)}) == {"a": [1.0, None], "b": [2.0], "c": 3}


def wait_analysis(client, analysis_id: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = client.get(f"/api/analyses/{analysis_id}").json()
        if found["status"] not in ("queued", "running"):
            return found
        time.sleep(0.1)
    raise AssertionError(f"analysis {analysis_id} did not finish: {found}")


def imported(command: list[str], log: Path, cwd: Path | None = None) -> str:
    """What a Python process imported (-X importtime). Its stdin stays open until it exits, as the
    service keeps a worker's open."""
    with open(log, "wb") as err:
        process = subprocess.Popen([sys.executable, "-X", "importtime", *command], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        assert process.wait() == 0, log.read_text()
        process.stdin.close()
    return log.read_text()


def test_strategy_execution_never_imports_scipy(tmp_path):
    """Amendment A1: a strategy run, through the worker, `qrs run` and an exported bundle, loads no
    SciPy, and the runtime lock that bundles install from does not list it."""
    dataset_id = datasets.create_synthetic(tmp_path)
    doc = recording_strategy(dataset_id)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "strategy.py").write_text(compiler.compile_strategy(doc))
    assert " scipy" not in imported(["-m", "qrs.worker", str(staging), str(tmp_path)], tmp_path / "worker.log")
    assert (staging / "record.json").exists()
    (tmp_path / "strategy.json").write_text(doc.model_dump_json())
    command = ["-m", "qrs.cli", "run", str(tmp_path / "strategy.json"), "--data", str(tmp_path), "--out", str(tmp_path / "cli")]
    assert " scipy" not in imported(command, tmp_path / "cli.log")
    assert (tmp_path / "cli" / "record.json").exists()
    zipfile.ZipFile(io.BytesIO(export.build(doc, tmp_path))).extractall(tmp_path / "bundle")
    assert " scipy" not in imported(["run.py", str(tmp_path / "out")], tmp_path / "bundle.log", cwd=tmp_path / "bundle")
    assert "scipy" not in (PACKAGE_DIR / "runtime.lock").read_text()
    # The check itself sees SciPy when it is imported.
    assert " scipy" in imported(["-c", "import scipy.stats"], tmp_path / "scipy.log")


def test_infinite_values_are_missing(tmp_path):
    """A return from zero equity is infinite; every method sees it as missing, not as a number."""
    datasets.create_synthetic(tmp_path)
    ctx = analysis.Context(document({"values": {"kind": "bars", "dataset": next(iter(datasets.list_manifests(tmp_path)))}}), tmp_path)
    ctx._series = lambda name: pd.Series([0.01, np.inf, -np.inf, 0.02], index=pd.bdate_range("2024-01-01", periods=4))
    assert ctx.series("values").isna().tolist() == [False, True, True, False]


def test_resampling_beyond_the_plans_budget_is_refused_before_queuing(tmp_path):
    """Criterion 13: 2,000 resamples of 12,500 sessions is the bootstrap's budget; more is refused."""
    big = datasets.save(
        tmp_path,
        datasets.synthetic_bars(2, 12_600, start="1975-01-01"),
        name="long",
        instrument=datasets.SYNTHETIC_INSTRUMENT,
        evidence_class="synthetic",
        source="t",
        redistributable=True,
    )
    given = {"returns": {"kind": "bars", "dataset": big}}
    with pytest.raises(analysis.AnalysisError, match="2000 resamples of 12600 sessions exceed the budget of 25,000,000"):
        analysis.check(document(given, "qrs.return_metrics"), tmp_path, lambda _: None)
    analysis.check(document(given, "qrs.return_metrics", params={"replicates": 1_900}), tmp_path, lambda _: None)
