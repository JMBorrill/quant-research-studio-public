"""Experiments (Stage 4 workstream 5). An experiment searches the parameters of one strategy revision:
a grid tries every point, and a random search draws `trials` distinct points with its seed. At most
MAX_TRIALS trials, and every trial's strategy is validated and compiled before any is queued.

Each trial is an ordinary queued run of the strategy with its point's values, cut off at the holdout
start, so no trial reads a holdout session. Trials run one at a time in the run queue and keep their
status whatever happens; a failed or timed-out trial does not stop the rest. They are ranked by the
objective over their common window, as run comparison computes it.

The holdout is evaluated only on request: a run of the chosen trial on the full data, whose metrics
cover only the sessions from the holdout start, measured from the last close before it (decision 7).
Every evaluation is recorded. One of a window (the same bars and holdout start) already evaluated,
in any experiment, is flagged as a repeat (Phase 4B).

An experiment reading exploratory data must say it is exploratory (Phase 4B decision 8): its ranking
and holdout evaluations are then labelled exploratory and kept apart from verified ones."""

import contextlib
import random
import sys
import uuid
from math import prod
from typing import Any

import pandas as pd

from qrs import comparison, datasets, graph, registry
from qrs.compiler import InvalidStrategy, compile_for_data
from qrs.contracts import Experiment, RunInfo, StrategyDocument
from qrs.jobs import JobManager, NotCancellable
from qrs.store import NotFound, Store

MAX_TRIALS = 200
COSTS = ("commission_per_contract", "slippage_ticks")  # backtest parameters that cost money but choose no strategy
ACTIVE = ("queued", "running")  # the statuses of a run that has not finished
Point = dict[tuple[str, str], Any]  # (node, parameter) -> value


class ExperimentError(ValueError):
    pass


def points(experiment: Experiment) -> list[Point]:
    """Each trial's values, in trial order. Refused past MAX_TRIALS, before anything is queued."""
    keys = [(axis.node, axis.param) for axis in experiment.axes]
    if len(set(keys)) < len(keys):
        raise ExperimentError("Each parameter may appear in only one axis.")
    total = prod(len(axis.values) for axis in experiment.axes)
    if experiment.search == "grid":
        if total > MAX_TRIALS:
            raise ExperimentError(f"The grid has {total} points; an experiment may have at most {MAX_TRIALS} trials.")
        indexes = range(total)
    else:
        if experiment.trials > MAX_TRIALS:
            raise ExperimentError(f"A random search of {experiment.trials} trials asks for more than the {MAX_TRIALS} an experiment may have.")
        if experiment.trials > total:
            raise ExperimentError(f"The space has {total} distinct points, fewer than the {experiment.trials} trials asked for.")
        if total > sys.maxsize:  # more than random.sample can draw from
            raise ExperimentError(f"The space has {total} points, too many to draw from; list fewer values.")
        indexes = random.Random(experiment.seed).sample(range(total), experiment.trials)
    return [point(experiment, index) for index in indexes]


def point(experiment: Experiment, index: int) -> Point:
    """The grid point at `index`, counting with the last axis fastest, as itertools.product does."""
    values = {}
    for axis in reversed(experiment.axes):
        index, position = divmod(index, len(axis.values))
        values[(axis.node, axis.param)] = axis.values[position]
    return dict(reversed(values.items()))


def trial_strategy(base: StrategyDocument, values: Point) -> StrategyDocument:
    doc = base.model_copy(deep=True)
    nodes = {node.id: node for node in doc.nodes}
    for (node, param), value in values.items():
        if node not in nodes:
            raise ExperimentError(f"The strategy has no node '{node}'.")
        nodes[node].params[param] = value
    return doc


def describe(values: Point) -> str:
    return ", ".join(f"{node}.{param} = {value!r}" for (node, param), value in values.items())


def create(store: Store, jobs: JobManager, experiment: Experiment) -> str:
    """Validate every trial, then record the experiment and queue its trials; return its ID. Raises
    ExperimentError, naming the trial at fault, before anything is queued."""
    try:
        _, base = store.get_strategy(experiment.strategy_id, experiment.revision)
    except NotFound:
        raise ExperimentError(f"Strategy {experiment.strategy_id} revision {experiment.revision} not found.") from None
    operators = {node.id: registry.find(node.operator, node.version) for node in base.nodes}
    if sum("backtest" in op.outputs.values() for op in operators.values() if op) != 1:
        raise ExperimentError("An experiment needs a strategy with exactly one backtest, to rank its trials and evaluate the holdout.")
    for axis in experiment.axes:
        if operators.get(axis.node) and axis.param in operators[axis.node].dataset_params():
            raise ExperimentError(f"{axis.node}.{axis.param} names a dataset; every trial runs on the strategy's own data, which the holdout splits.")
    exploratory = []
    for dataset_id in graph.dataset_ids(base):
        manifest = datasets.read_manifest(jobs.data_dir, dataset_id)
        if manifest.kind == datasets.BARS and manifest.end < experiment.holdout_start:
            raise ExperimentError(f"{manifest.name} ends on {manifest.end}, before the holdout starts.")
        if manifest.evidence_class == "exploratory":
            exploratory.append(manifest.name)
    if exploratory and not experiment.exploratory:
        # Checked once: dataset axes are refused, so every trial reads these datasets, and a
        # dataset's class cannot change in place.
        raise ExperimentError(
            f"The strategy reads exploratory data ({', '.join(exploratory)}), so its results cannot be verified. "
            'Run it as an exploratory experiment ("exploratory": true): its ranking and holdout are then labelled exploratory and kept apart.'
        )
    trials = [(values, trial_strategy(base, values)) for values in points(experiment)]
    seen: dict[str, int] = {}  # compiled code -> trial number
    for number, (values, doc) in enumerate(trials, 1):
        try:
            code = compile_for_data(doc, jobs.data_dir, experiment.holdout_start)
        except InvalidStrategy as e:
            raise ExperimentError(f"Trial {number} ({describe(values)}): " + " ".join(i.message for i in e.issues)) from None
        # Values the parameters read alike (10, 10.0 and "10") make the same strategy.
        if code in seen:
            raise ExperimentError(f"Trial {number} ({describe(values)}) is the same strategy as trial {seen[code]}: list each value once.")
        seen[code] = number
    experiment_id = uuid.uuid4().hex[:12]
    bars = graph.traded_bars(base)
    store.create_experiment(experiment_id, experiment.model_dump_json(), bars, experiment.holdout_start.isoformat(), experiment.exploratory)
    for number, (_, doc) in enumerate(trials, 1):
        jobs.submit(experiment.strategy_id, experiment.revision, doc, until=experiment.holdout_start, trial=(experiment_id, number))
    if store.get_experiment(experiment_id)["cancelled_at"]:  # cancelled while its trials were queued
        cancel(store, jobs, experiment_id)
    return experiment_id


def cancel(store: Store, jobs: JobManager, experiment_id: str) -> None:
    """Cancel the queued trials, then stop the running one."""
    store.cancel_experiment(experiment_id)
    for run in sorted(store.trial_runs(experiment_id), key=lambda run: run["status"] != "queued"):
        if run["status"] in ACTIVE:
            with contextlib.suppress(NotCancellable):  # it finished meanwhile
                jobs.cancel(run["id"])


def evaluate_holdout(store: Store, jobs: JobManager, experiment_id: str, trial: int, family: str | None = None) -> dict:
    """Queue a run of a succeeded trial's strategy on the full data, and record the evaluation; the
    run. Refused while any trial has not finished, since the ranking the trial was chosen from could
    still change. `family` is the hypothesis family whose evaluation it is, if any."""
    experiment = load(store, experiment_id)
    runs = store.trial_runs(experiment_id)
    run = next((r for r in runs if r["trial"] == trial), None)
    if run is None:
        raise ExperimentError(f"The experiment has no trial {trial}.")
    unfinished = sum(r["status"] in ACTIVE for r in runs)
    if unfinished:
        raise ExperimentError(f"The holdout can be evaluated once every trial has finished; {unfinished} still queued or running.")
    if run["status"] != "succeeded":
        raise ExperimentError(f"Trial {trial} has not succeeded, so its holdout cannot be evaluated.")
    doc, _ = jobs.frozen(run["id"])
    holdout = jobs.submit(experiment.strategy_id, experiment.revision, doc, family=family, read="holdout")
    store.record_holdout_evaluation(holdout["id"], experiment_id, trial)
    return holdout


def load(store: Store, experiment_id: str) -> Experiment:
    return Experiment.model_validate_json(store.get_experiment(experiment_id)["document"])


def overview(store: Store, row: dict) -> dict:
    """An experiment as listed: its document, status and how many trials have each status."""
    statuses = [run["status"] for run in store.trial_runs(row["id"])]
    return {
        "id": row["id"],
        "experiment": Experiment.model_validate_json(row["document"]),
        "created_at": row["created_at"],
        "cancelled_at": row["cancelled_at"],
        "exploratory": bool(row["exploratory"]),
        # Cancelled only if the cancel stopped a trial: one asked for after the last finished changes nothing.
        "status": "cancelled" if row["cancelled_at"] and "cancelled" in statuses else "running" if set(ACTIVE) & set(statuses) else "finished",
        "counts": {status: statuses.count(status) for status in sorted(set(statuses))},
    }


def summary(store: Store, jobs: JobManager, experiment_id: str) -> dict:
    """The experiment's overview, every trial with its values and run, the ranking of the succeeded
    trials, and every holdout evaluation, with its metrics over the holdout window once it has
    succeeded."""
    row = store.get_experiment(experiment_id)
    listed = overview(store, row)
    experiment = listed["experiment"]
    runs, values = store.trial_runs(experiment_id), points(experiment)
    trials = [
        {
            "trial": run["trial"],
            "values": [{"node": n, "param": p, "value": v} for (n, p), v in values[run["trial"] - 1].items()],
            "run": RunInfo.model_validate(run),
        }
        for run in runs
    ]
    evaluations = [holdout(load(store, e["experiment_id"]), e, store, jobs) for e in counted(store.window_evaluations(experiment_id))]
    return {
        **listed,
        "trials": trials,
        "ranking": ranking(experiment, runs, jobs),
        "holdout": [e for e in evaluations if e["experiment_id"] == experiment_id],
        # Every evaluation of the same holdout window (the same bars and holdout start), in this or
        # any experiment: verified and exploratory apart.
        "window": {
            "bars": row["bars"],
            "holdout_start": experiment.holdout_start.isoformat(),
            "verified": [e for e in evaluations if not e["exploratory"]],
            "exploratory": [e for e in evaluations if e["exploratory"]],
        },
    }


def counted(evaluations: list[dict]) -> list[dict]:
    """A window's evaluations, oldest first, each with how many verified and exploratory ones came before it."""
    earlier = {"verified": 0, "exploratory": 0}
    out = []
    for evaluation in evaluations:
        out.append(evaluation | {"earlier": dict(earlier)})
        earlier["exploratory" if evaluation["exploratory"] else "verified"] += 1
    return out


def ranking(experiment: Experiment, runs: list[dict], jobs: JobManager) -> dict | None:
    """The succeeded trials over their common window, best objective first, ties in trial order; or
    why comparison cannot rank them."""
    if not any(run["status"] == "succeeded" for run in runs):
        return None
    try:
        succeeded = [comparison.load(RunInfo.model_validate(run), jobs.run_dir(run["id"])) for run in runs if run["status"] == "succeeded"]
        ranked = comparison.common_window(succeeded)
    except comparison.ComparisonError as e:
        return {"error": str(e)}
    trial_of = {ledger.info.id: ledger.info.trial for ledger in succeeded}
    # Each trial's equity curve is left out: comparing chosen trials shows it.
    rows = [{key: value for key, value in run.items() if key != "equity_change"} | {"trial": trial_of[run["id"]]} for run in ranked["runs"]]
    sign = -1 if experiment.direction == "maximize" else 1
    rows.sort(key=lambda run: (sign * run["metrics"][experiment.objective], run["trial"]))
    return {"window": ranked["window"], "runs": rows, "mismatches": comparison.mismatches(succeeded)}


def holdout(experiment: Experiment, evaluation: dict, store: Store, jobs: JobManager) -> dict:
    """An evaluation, with, once it has succeeded, its metrics over the holdout sessions measured
    from the last close before the holdout: the position held then is carried in, and the first
    holdout session's price change, fills and costs count. Trials need sessions before the holdout,
    so that close exists."""
    run = RunInfo.model_validate(store.get_run(evaluation["run_id"]))
    result = {
        "experiment_id": evaluation["experiment_id"],
        "experiment_name": experiment.name,
        "exploratory": bool(evaluation["exploratory"]),
        "earlier": evaluation["earlier"],
        "trial": evaluation["trial"],
        "repeat": bool(evaluation["repeat"]),
        "requested_at": evaluation["requested_at"],
        "run": run,
    }
    if run.status == "succeeded":
        ledger = comparison.load(run, jobs.run_dir(run.id))
        start = int(ledger.equity.index.searchsorted(pd.Timestamp(experiment.holdout_start)))
        result |= {"window": comparison.window(ledger.equity.index, start), "result": comparison.from_close_before(ledger, start)}
    return result


def selections(store: Store, experiment_id: str) -> list[dict]:
    """Every experiment on the experiment's holdout window, with its trials and whether it varies
    only backtest costs. The trials of all but those are the N a best-of-N selection is chosen from
    (Stage 5): a search over costs alone chooses no strategy."""
    found = []
    for row in store.window_experiments(experiment_id):
        experiment = load(store, row["id"])
        try:
            _, base = store.get_strategy(experiment.strategy_id, experiment.revision)
        except NotFound:
            base = None
        operators = {n.id: registry.find(n.operator, n.version) for n in base.nodes} if base else {}
        costs_only = all((op := operators.get(axis.node)) is not None and "backtest" in op.outputs.values() and axis.param in COSTS for axis in experiment.axes)
        found.append({"experiment": row["id"], "name": experiment.name, "trials": len(store.trial_runs(row["id"])), "costs_only": costs_only})
    return found
