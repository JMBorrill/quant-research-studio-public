"""Experiments (Stage 4 workstream 5), through the service with real workers: trials as runs cut off
at the holdout start, ranking, the 200-trial limit, validation before queuing, holdout isolation and
evaluation, seeds, cancellation, time limits, re-runs and the `qrs experiment` command."""

import json
import sqlite3
import subprocess
import sys
from datetime import date
from typing import get_args

import pandas as pd
import pytest

import fixture_ops  # noqa: F401  registers tests.sleep for validation in this process
from conftest import cash_flow_ledger, client_for, dataset_id, new_strategy, save, start, wait
from qrs import cli, datasets, experiments
from qrs.contracts import Edge, Experiment, Node, Objective, PortRef, RunInfo
from qrs.jobs import JobManager
from qrs.store import Store

HOLDOUT = 400  # the synthetic dataset's 500 sessions: trials see 400, the holdout the last 100


def holdout_start(home) -> date:
    return datasets.load(home, datasets.list_manifests(home).popitem()[0]).frame.index[HOLDOUT].date()


def experiment(strategy: dict, holdout: date, axes: list[tuple[str, str, list]], **search) -> dict:
    return {
        "name": "EMA periods",
        "strategy_id": strategy["id"],
        "revision": strategy["revision"],
        "search": "random" if search else "grid",
        "axes": [{"node": node, "param": param, "values": values} for node, param, values in axes],
        "objective": "net_pnl",
        "direction": "maximize",
        "holdout_start": holdout.isoformat(),
        **search,
    }


def finished(client, created: dict) -> dict:
    for trial in created["trials"]:
        wait(client, trial["run"]["id"], timeout=120)
    return client.get(f"/api/experiments/{created['id']}").json()


def create(client, body: dict) -> dict:
    response = client.post("/api/experiments", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def trial_values(summary: dict) -> list[dict]:
    return [{f"{v['node']}.{v['param']}": v["value"] for v in trial["values"]} for trial in summary["trials"]]


def test_a_grid_runs_each_point_as_a_trial_cut_off_at_the_holdout_and_ranks_them(home, tmp_path):
    holdout = holdout_start(home)
    with client_for(home) as client:
        strategy = new_strategy(client)
        summary = finished(client, create(client, experiment(strategy, holdout, [("fast", "period", [10, 20]), ("slow", "period", [40, 50])])))
    assert trial_values(summary) == [
        {"fast.period": 10, "slow.period": 40},
        {"fast.period": 10, "slow.period": 50},
        {"fast.period": 20, "slow.period": 40},
        {"fast.period": 20, "slow.period": 50},
    ]
    assert summary["status"] == "finished" and summary["counts"] == {"succeeded": 4}
    for trial in summary["trials"]:
        run = trial["run"]
        assert (run["experiment"], run["trial"], run["revision"]) == (summary["id"], trial["trial"], strategy["revision"])
        frozen = json.loads((home / "runs" / run["id"] / "strategy.json").read_text())
        periods = {f"{n['id']}.period": n["params"]["period"] for n in frozen["nodes"] if n["id"] in ("fast", "slow")}
        assert periods == trial_values(summary)[trial["trial"] - 1]  # each run froze its trial's strategy
        assert json.loads((home / "runs" / run["id"] / "record.json").read_text())["until"] == holdout.isoformat()
        equity = pd.read_csv(home / "runs" / run["id"] / "results" / "backtest" / "equity.csv")
        assert len(equity) == HOLDOUT  # no holdout session was read

    ranking = summary["ranking"]
    # The common window starts at the latest warm-up: a 50-session slow average and one more to cross.
    assert ranking["window"]["sessions"] == HOLDOUT - 50 and ranking["mismatches"] == []
    pnl = [run["metrics"]["net_pnl"] for run in ranking["runs"]]
    assert pnl == sorted(pnl, reverse=True) and sorted(run["trial"] for run in ranking["runs"]) == [1, 2, 3, 4]
    assert set(ranking["runs"][0]["metrics"]) == set(get_args(Objective))  # every metric can be the objective

    # `qrs run --until` with a trial's strategy and cutoff reproduces its result hash.
    best = next(t["run"] for t in summary["trials"] if t["trial"] == ranking["runs"][0]["trial"])
    command = [sys.executable, "-m", "qrs.cli", "run", str(home / "runs" / best["id"] / "strategy.json"), "--data", str(home)]
    done = subprocess.run([*command, "--until", holdout.isoformat(), "--out", str(tmp_path / "cli")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert json.loads((tmp_path / "cli" / "record.json").read_text())["result_sha256"] == best["result_sha256"]


def test_rewriting_holdout_prices_changes_no_trials_result(home):
    holdout = holdout_start(home)
    bars = datasets.load(home, datasets.list_manifests(home).popitem()[0])
    frame = bars.frame.copy()
    frame.iloc[HOLDOUT:] = (frame.iloc[HOLDOUT:] * 1.3 / 0.25).round() * 0.25
    rewritten = datasets.save(home, frame, name="rewritten", instrument=bars.instrument, evidence_class="synthetic", source="test", redistributable=True)
    axes = [("fast", "period", [10, 20]), ("slow", "period", [50])]
    with client_for(home) as client:
        hashes = []
        for data in (bars.dataset_id, rewritten):
            summary = finished(client, create(client, experiment(new_strategy(client, data), holdout, axes)))
            hashes.append([trial["run"]["result_sha256"] for trial in summary["trials"]])
    assert hashes[0] == hashes[1] and None not in hashes[0]


def test_the_holdout_is_evaluated_on_request_over_its_window_only_and_repeats_are_flagged(home):
    holdout = holdout_start(home)
    with client_for(home) as client:
        strategy = new_strategy(client)
        summary = finished(client, create(client, experiment(strategy, holdout, [("fast", "period", [10, 20])])))
        assert summary["holdout"] == []  # nothing reads the holdout until asked
        best = summary["ranking"]["runs"][0]["trial"]
        for _ in range(2):
            response = client.post(f"/api/experiments/{summary['id']}/holdout", json={"trial": best})
            assert response.status_code == 200, response.text
            wait(client, response.json()["holdout"][-1]["run"]["id"])
        evaluations = client.get(f"/api/experiments/{summary['id']}").json()["holdout"]
    assert [(e["trial"], e["repeat"]) for e in evaluations] == [(best, False), (best, True)]
    first = evaluations[0]
    sessions = datasets.load(home, strategy["document"]["nodes"][0]["params"]["dataset_id"]).frame.index
    assert first["window"] == {"start": holdout.isoformat(), "end": sessions[-1].date().isoformat(), "sessions": len(sessions) - HOLDOUT}
    # Its metrics cover the holdout sessions alone, measured from the last close before them.
    equity = pd.read_csv(home / "runs" / first["run"]["id"] / "results" / "backtest" / "equity.csv")["equity"]
    assert first["result"]["metrics"]["sessions"] == len(sessions) - HOLDOUT
    assert first["result"]["metrics"]["initial_cash"] == equity.iloc[HOLDOUT - 1]
    assert first["result"]["metrics"]["net_pnl"] == equity.iloc[-1] - equity.iloc[HOLDOUT - 1]


def test_the_holdout_carries_in_its_position_and_counts_its_first_sessions_fills_and_costs(home):
    """The holdout starts on a session when the trial sells the contract it held at the close
    before: that close is the baseline, and the sale, its costs and the overnight move count. Every
    figure is checked against a ledger recomputed from the bars and fills by cash flows."""
    with client_for(home) as client:
        strategy = new_strategy(client)
        full = wait(client, start(client, strategy)["id"])
        fills = pd.read_csv(home / "runs" / full["id"] / "results" / "backtest" / "fills.csv", parse_dates=["session"])
        bars = datasets.load(home, dataset_id(client))
        sessions = bars.frame.index
        first = next(i for i in sessions.get_indexer(fills[fills["side"] == "SELL"]["session"]) if i >= 300)
        holdout = sessions[first].date()
        summary = finished(client, create(client, experiment(strategy, holdout, [("fast", "period", [20])])))
        response = client.post(f"/api/experiments/{summary['id']}/holdout", json={"trial": 1})
        wait(client, response.json()["holdout"][-1]["run"]["id"])
        evaluation = client.get(f"/api/experiments/{summary['id']}").json()["holdout"][0]

    fills = pd.read_csv(home / "runs" / evaluation["run"]["id"] / "results" / "backtest" / "fills.csv", parse_dates=["session"])
    ledger = cash_flow_ledger(bars, fills, 100_000)
    baseline, held = ledger["equity"].iloc[first - 1], ledger["position"].iloc[first - 1]
    holdout_rows, holdout_fills = ledger.iloc[first:], fills[fills["session"] >= sessions[first]]
    assert held == 1 and holdout_fills["session"].iloc[0] == sessions[first]  # held in, sold on the first session
    peak = holdout_rows["equity"].cummax().clip(lower=baseline)
    signed = holdout_fills["quantity"].where(holdout_fills["side"] == "BUY", -holdout_fills["quantity"])
    expected = {
        "sessions": len(sessions) - first,
        "initial_cash": baseline,
        "net_pnl": holdout_rows["equity"].iloc[-1] - baseline,
        "commission": holdout_fills["commission"].sum(),
        "fills": len(holdout_fills),
        "max_drawdown": (peak - holdout_rows["equity"]).max(),
        "exposure_pct": (holdout_rows["position"] != 0).mean() * 100,
        "round_trips": int((held + signed.cumsum() == 0).sum()),
        "final_position": holdout_rows["position"].iloc[-1],
    }
    assert evaluation["window"] == {"start": holdout.isoformat(), "end": sessions[-1].date().isoformat(), "sessions": len(sessions) - first}
    assert evaluation["result"]["position_at_start"] == held
    assert {k: evaluation["result"]["metrics"][k] for k in expected} == pytest.approx(expected, abs=1e-6)


def test_experiments_over_200_trials_are_refused_before_anything_is_queued(home):
    holdout = holdout_start(home)
    with client_for(home) as client:
        strategy = new_strategy(client)
        refusals = {
            "a 201-point grid": experiment(strategy, holdout, [("fast", "period", list(range(2, 69))), ("slow", "period", [50, 60, 70])]),
            "201 random trials": experiment(strategy, holdout, [("fast", "period", list(range(2, 300)))], trials=201, seed=1),
            "more trials than points": experiment(strategy, holdout, [("fast", "period", [10, 20])], trials=3, seed=1),
            "a space too large to draw from": experiment(strategy, holdout, [(node, param, list(range(1, 10_001))) for node, param in HUGE], trials=1, seed=1),
        }
        messages = {}
        for name, body in refusals.items():
            response = client.post("/api/experiments", json=body)
            assert response.status_code == 422, name
            messages[name] = response.json()["detail"]
        assert client.get("/api/runs").json() == [] and client.get("/api/experiments").json() == []
    assert messages == {
        "a 201-point grid": "The grid has 201 points; an experiment may have at most 200 trials.",
        "201 random trials": "A random search of 201 trials asks for more than the 200 an experiment may have.",
        "more trials than points": "The space has 2 distinct points, fewer than the 3 trials asked for.",
        "a space too large to draw from": "The space has 100000000000000000000 points, too many to draw from; list fewer values.",
    }
    exactly_200 = experiment(strategy, holdout, [("fast", "period", list(range(2, 102))), ("slow", "period", [50, 60])])
    assert len(experiments.points(Experiment.model_validate(exactly_200))) == 200  # the limit itself is allowed


HUGE = [("fast", "period"), ("slow", "period"), ("position", "quantity"), ("backtest", "initial_cash"), ("backtest", "slippage_ticks")]


def test_every_trials_parameters_are_validated_before_any_is_queued(home):
    holdout = holdout_start(home)
    with client_for(home) as client:
        strategy = new_strategy(client)
        bad_period = client.post("/api/experiments", json=experiment(strategy, holdout, [("fast", "period", [10, 0])]))
        unknown = client.post("/api/experiments", json=experiment(strategy, holdout, [("fast", "length", [10])]))
        missing = client.post("/api/experiments", json=experiment(strategy, holdout, [("faster", "period", [10])]))
        too_short = client.post("/api/experiments", json=experiment(strategy, date(2024, 2, 1), [("fast", "period", [10])]))
        assert client.get("/api/runs").json() == []
    assert bad_period.status_code == 422 and bad_period.json()["detail"] == "Trial 2 (fast.period = 0): period: Input should be greater than or equal to 1"
    assert unknown.json()["detail"] == "Trial 1 (fast.length = 10): length: Extra inputs are not permitted"
    assert missing.json()["detail"] == "The strategy has no node 'faster'."
    assert too_short.json()["detail"].startswith("Trial 1 (fast.period = 10): A warm-up of 50 sessions leaves no session to trade")


def test_the_same_seed_reproduces_the_same_trials_and_result_hashes(home):
    holdout = holdout_start(home)
    axes = [("fast", "period", [5, 10, 15, 20]), ("slow", "period", [40, 50])]
    with client_for(home) as client:
        strategy = new_strategy(client)
        first, second = (finished(client, create(client, experiment(strategy, holdout, axes, trials=3, seed=11))) for _ in range(2))
        other = client.post("/api/experiments", json=experiment(strategy, holdout, axes, trials=3, seed=12)).json()
    assert trial_values(first) == trial_values(second) != trial_values(other)
    assert [t["run"]["result_sha256"] for t in first["trials"]] == [t["run"]["result_sha256"] for t in second["trials"]]
    assert first["experiment"]["seed"] == 11


def sleeping(client) -> dict:
    """The EMA crossover with a side branch that sleeps (tests.sleep), saved as a new revision."""
    strategy = new_strategy(client)
    doc = strategy["document"]
    doc["nodes"].append(Node(id="pause", operator="tests.sleep", version=1, params={"seconds": 0}).model_dump())
    doc["edges"].append(Edge(source=PortRef(node="bars", port="bars"), target=PortRef(node="pause", port="bars")).model_dump())
    return strategy | {"revision": save(client, strategy, doc)}


def test_cancelling_an_experiment_stops_its_running_trial_and_cancels_the_queued_ones(home):
    holdout = holdout_start(home)
    with client_for(home) as client:
        created = create(client, experiment(sleeping(client), holdout, [("pause", "seconds", [60, 61, 62])]))
        wait(client, created["trials"][0]["run"]["id"], until=("running",))
        cancelled = client.post(f"/api/experiments/{created['id']}/cancel").json()
        summary = finished(client, cancelled)
    assert summary["status"] == "cancelled" and summary["counts"] == {"cancelled": 3}
    assert [t["run"]["error"] for t in summary["trials"]] == ["Cancelled while running.", "Cancelled before it started.", "Cancelled before it started."]


def test_an_experiment_cancelled_while_its_trials_are_being_queued_cancels_every_one(home):
    holdout = holdout_start(home)
    with client_for(home) as client:
        strategy = new_strategy(client)
    store = Store(home / "qrs.sqlite")
    jobs = JobManager(store, home)  # not started, as `qrs experiment` queues trials
    submit = jobs.submit

    def submit_then_cancel(*args, trial, **options):
        queued = submit(*args, trial=trial, **options)
        if trial[1] == 1:  # cancelled as soon as the experiment is listed, with one trial queued
            experiments.cancel(store, jobs, trial[0])
        return queued

    jobs.submit = submit_then_cancel
    experiment_id = experiments.create(store, jobs, Experiment.model_validate(experiment(strategy, holdout, [("fast", "period", [10, 20, 30])])))
    assert [run["status"] for run in store.trial_runs(experiment_id)] == ["cancelled"] * 3
    assert experiments.overview(store, store.get_experiment(experiment_id))["status"] == "cancelled"


def test_a_finished_experiment_stays_finished_and_its_holdout_is_evaluated_only_through_it(home):
    holdout = holdout_start(home)
    with client_for(home) as client:
        summary = finished(client, create(client, experiment(new_strategy(client), holdout, [("fast", "period", [10])])))
        late = client.post(f"/api/experiments/{summary['id']}/cancel").json()  # after the last trial finished
        evaluated = client.post(f"/api/experiments/{summary['id']}/holdout", json={"trial": 1}).json()
        holdout_run = evaluated["holdout"][0]["run"]["id"]
        wait(client, holdout_run)
        rerun = client.post(f"/api/runs/{holdout_run}/rerun")
        missing = client.post(f"/api/experiments/{summary['id']}/holdout", json={"trial": 9})
        not_a_number = client.post(f"/api/experiments/{summary['id']}/holdout", json={"trial": True})
        recorded = client.get(f"/api/experiments/{summary['id']}").json()["holdout"]
    assert (late["status"], late["counts"]) == ("finished", {"succeeded": 1})
    # A re-run of the holdout run would evaluate the holdout again without recording it.
    assert rerun.status_code == 409 and "evaluate it again from the experiment" in rerun.json()["detail"]
    assert (missing.status_code, missing.json()["detail"]) == (422, "The experiment has no trial 9.")
    assert not_a_number.status_code == 422
    assert [e["run"]["id"] for e in recorded] == [holdout_run]


def test_a_trial_and_a_full_run_are_not_compared_over_one_window(home):
    """A trial ends at the holdout start; a full run's metrics over a shared window would include
    the holdout."""
    holdout = holdout_start(home)
    with client_for(home) as client:
        strategy = new_strategy(client)
        summary = finished(client, create(client, experiment(strategy, holdout, [("fast", "period", [20])])))
        trial, full = summary["trials"][0]["run"]["id"], wait(client, start(client, strategy)["id"])["id"]
        compared = client.post("/api/compare", json={"run_ids": [trial, full]})
        sessions = datasets.load(home, dataset_id(client)).frame.index
    assert compared.status_code == 422
    assert compared.json()["detail"] == (
        f"Only runs over the same sessions can be compared; a cutoff ends a run early "
        f"({trial}: to {sessions[HOLDOUT - 1].date()}; {full}: to {sessions[-1].date()})."
    )


def test_a_timed_out_trial_is_kept_as_failed_and_the_next_trial_runs(home):
    holdout = holdout_start(home)
    with client_for(home, time_limit=6) as client:
        summary = finished(client, create(client, experiment(sleeping(client), holdout, [("pause", "seconds", [60, 0])])))
        refused = client.post(f"/api/experiments/{summary['id']}/holdout", json={"trial": 1})
    outcomes = [(t["run"]["status"], t["run"]["error"]) for t in summary["trials"]]
    assert outcomes == [("failed", "Stopped after the time limit of 6 seconds."), ("succeeded", None)]
    assert summary["status"] == "finished" and [run["trial"] for run in summary["ranking"]["runs"]] == [2]
    # Only a succeeded trial can have its holdout evaluated.
    assert refused.status_code == 422 and refused.json()["detail"] == "Trial 1 has not succeeded, so its holdout cannot be evaluated."


def test_re_running_a_trial_keeps_its_parameters_and_cutoff(home):
    holdout = holdout_start(home)
    with client_for(home) as client:
        summary = finished(client, create(client, experiment(new_strategy(client), holdout, [("fast", "period", [10])])))
        trial = summary["trials"][0]["run"]
        again = wait(client, client.post(f"/api/runs/{trial['id']}/rerun").json()["id"])
    assert (again["predecessor"], again["result_sha256"]) == (trial["id"], trial["result_sha256"])
    assert json.loads((home / "runs" / again["id"] / "record.json").read_text())["until"] == holdout.isoformat()


def test_qrs_experiment_queues_trials_that_the_service_runs(home, tmp_path, capsys):
    holdout = holdout_start(home)
    with client_for(home) as client:
        strategy = new_strategy(client, dataset_id(client))
        (tmp_path / "experiment.json").write_text(json.dumps(experiment(strategy, holdout, [("fast", "period", [10, 20])])))
        assert cli.main(["experiment", str(tmp_path / "experiment.json"), "--home", str(home)]) == 0
        printed = capsys.readouterr().out.splitlines()
        experiment_id = printed[0].split()[1].rstrip(":")
        assert printed[1].split()[:2] == ["trial", "1"] and printed[1].endswith("fast.period = 10")
        summary = finished(client, client.get(f"/api/experiments/{experiment_id}").json())
    assert summary["counts"] == {"succeeded": 2}
    assert len(Store(home / "qrs.sqlite").trial_runs(experiment_id)) == 2


def test_searches_trials_could_not_share_the_holdout_split_of_are_refused(home):
    """Every trial runs on the strategy's own data, which the holdout splits, and has one backtest to
    rank and evaluate: a dataset axis, a second backtest, a repeated value and a negative seed are
    refused before anything is queued."""
    holdout = holdout_start(home)
    with client_for(home) as client:
        strategy = new_strategy(client)
        other = datasets.create_synthetic(home, seed=7)
        doubled = strategy["document"]
        doubled["nodes"].append(Node(id="second", operator="qrs.backtest", version=1).model_dump())
        doubled["edges"] += [
            Edge(source=PortRef(node="bars", port="bars"), target=PortRef(node="second", port="bars")).model_dump(),
            Edge(source=PortRef(node="position", port="positions"), target=PortRef(node="second", port="positions")).model_dump(),
        ]
        two_backtests = strategy | {"revision": save(client, strategy, doubled)}
        refusals = {
            "dataset axis": client.post("/api/experiments", json=experiment(strategy, holdout, [("bars", "dataset_id", [other])])),
            "two backtests": client.post("/api/experiments", json=experiment(two_backtests, holdout, [("fast", "period", [10])])),
            "repeated value": client.post("/api/experiments", json=experiment(strategy, holdout, [("fast", "period", [10, 20, 10.0])])),
            "negative seed": client.post("/api/experiments", json=experiment(strategy, holdout, [("fast", "period", [10, 20])], trials=1, seed=-5)),
        }
        assert client.get("/api/runs").json() == []
    assert {name: response.status_code for name, response in refusals.items()} == dict.fromkeys(refusals, 422)
    assert (
        refusals["dataset axis"].json()["detail"] == "bars.dataset_id names a dataset; every trial runs on the strategy's own data, which the holdout splits."
    )
    assert refusals["two backtests"].json()["detail"].startswith("An experiment needs a strategy with exactly one backtest")
    # 10.0 is the same period as 10, so it is the same strategy.
    assert refusals["repeated value"].json()["detail"] == "Trial 3 (fast.period = 10.0) is the same strategy as trial 1: list each value once."


def test_the_holdout_is_refused_until_every_trial_has_finished(home):
    holdout = holdout_start(home)
    with client_for(home) as client:
        created = create(client, experiment(sleeping(client), holdout, [("pause", "seconds", [0, 8])]))
        first = wait(client, created["trials"][0]["run"]["id"])  # trial 2 is still queued or sleeping
        early = client.post(f"/api/experiments/{created['id']}/holdout", json={"trial": 1})
        finished(client, created)
        later = client.post(f"/api/experiments/{created['id']}/holdout", json={"trial": 1})
    assert first["status"] == "succeeded"
    assert early.status_code == 422
    assert early.json()["detail"] == "The holdout can be evaluated once every trial has finished; 1 still queued or running."
    # The refusal recorded nothing, so the evaluation once both trials finished is the first.
    assert later.status_code == 200 and [(e["trial"], e["repeat"]) for e in later.json()["holdout"]] == [(1, False)]


def test_a_holdout_evaluation_of_another_trial_is_still_a_repeat(home):
    holdout = holdout_start(home)
    with client_for(home) as client:
        summary = finished(client, create(client, experiment(new_strategy(client), holdout, [("fast", "period", [10, 20])])))
        for trial in (1, 2):
            client.post(f"/api/experiments/{summary['id']}/holdout", json={"trial": trial})
        evaluations = client.get(f"/api/experiments/{summary['id']}").json()["holdout"]
    assert [(e["trial"], e["repeat"]) for e in evaluations] == [(1, False), (2, True)]


def test_trials_rank_by_the_objective_in_its_direction_and_experiments_list_by_strategy(home):
    holdout = holdout_start(home)
    with client_for(home) as client:
        strategy, other = new_strategy(client), new_strategy(client)
        body = experiment(strategy, holdout, [("fast", "period", [10, 20]), ("slow", "period", [40, 50])]) | {
            "objective": "max_drawdown",
            "direction": "minimize",
        }
        summary = finished(client, create(client, body))
        create(client, experiment(other, holdout, [("fast", "period", [10])]))
        listed = client.get("/api/experiments", params={"strategy_id": strategy["id"]}).json()
    drawdowns = [run["metrics"]["max_drawdown"] for run in summary["ranking"]["runs"]]
    assert drawdowns == sorted(drawdowns) and len(set(drawdowns)) > 1  # smallest first
    assert "equity_change" not in summary["ranking"]["runs"][0]
    assert [e["id"] for e in listed] == [summary["id"]] and listed[0]["counts"] == {"succeeded": 4}


def test_trials_with_the_same_objective_rank_in_trial_order(home):
    holdout = holdout_start(home)
    with client_for(home) as client:
        # The pause is a side branch, so the trials trade identically and tie on the objective.
        tied = experiment(sleeping(client), holdout, [("pause", "seconds", [0, 0.01, 0.02])])
        for direction in ("maximize", "minimize"):
            ranked = finished(client, create(client, tied | {"direction": direction}))["ranking"]["runs"]
            assert len({run["metrics"]["net_pnl"] for run in ranked}) == 1
            assert [run["trial"] for run in ranked] == [1, 2, 3], direction


def test_a_store_from_before_experiments_opens_with_its_runs_unchanged(tmp_path):
    # The runs table as Stage 2 created it, with one finished run.
    with sqlite3.connect(tmp_path / "qrs.sqlite") as db:
        db.execute(
            "CREATE TABLE runs (id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,"
            " code_sha256 TEXT NOT NULL, result_sha256 TEXT, error TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT)"
        )
        db.execute("INSERT INTO runs VALUES ('old', 's', 1, 'succeeded', 'c', 'r', NULL, 't0', 't1', 't2')")
    store = Store(tmp_path / "qrs.sqlite")
    run = RunInfo.model_validate(store.get_run("old"))
    assert (run.status, run.result_sha256, run.predecessor, run.experiment, run.trial) == ("succeeded", "r", None, None, None)
    assert store.list_experiments() == [] and store.trial_runs("any") == []
