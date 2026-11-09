"""Analysis runs (Stage 5 decision 1). An analysis document names a registered method, its
parameters and its inputs: datasets, and the outputs of succeeded runs. It is checked before it is
queued, executed by a worker, and written to an immutable directory with a record and hashes, as a
run is.

Every input is read through one Context, which applies the effective cutoff: the earliest of the
analysis's `until` and the cutoffs of the runs it reads, so no input is read past another's end
and no run's output past its own. Bars are read through runtime.Run, and observations joined with
the As-of operator's as_of_values, so analyses share the engine's point-in-time rules."""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet

from qrs import datasets, graph, methods, runtime
from qrs.contracts import AnalysisDocument, ProjectDocument, StrategyDocument
from qrs.hashing import canonical_json, sha256_file, sha256_hex
from qrs.operators.data import as_of_values
from qrs.operators.indicators import simple_returns

# The most sessions an input may have: over 75 years of daily bars. Larger requests are refused before
# they are queued, so every method stays within its budget (Stage 5 plan, performance).
MAX_SESSIONS = 20_000
RUN_INPUTS = ("equity", "record", "fills", "model")  # the input kinds read from a succeeded run
# The plan's resampling budgets, in resamples × sessions (× trials): a 2,000-replicate bootstrap on
# 12,500 sessions, and the Reality Check over 200 trials × 2,500 sessions × 1,000 replicates.
BOOTSTRAP_BUDGET = 2_000 * 12_500
REALITY_CHECK_BUDGET = 200 * 2_500 * 1_000


class AnalysisError(ValueError):
    pass


@dataclass
class Trials:
    """A finished experiment as an analysis reads it."""

    objective: str
    direction: str
    trials: list[dict]  # every trial: its number, parameter values (by "node.param", and described), status and run
    returns: pd.DataFrame  # session returns of the succeeded trials over their common window, a column per trial, best first
    objectives: dict[int, float]  # each succeeded trial's objective over the window
    axes: dict[str, list]  # each axis ("node.param") and the values it tries, in order
    window: list[dict]  # every experiment on the same holdout window (experiments.selections)

    @property
    def n(self) -> int:
        """Every trial on the holdout window, failed ones too, of experiments that choose a strategy."""
        return sum(e["trials"] for e in self.window if not e["costs_only"])


def _store(home: Path):
    from qrs.store import Store  # the store imports this module to backfill reads

    return Store(home / "qrs.sqlite")


def _experiment(home: Path, experiment_id: str) -> tuple[dict, StrategyDocument]:
    """An experiment's row and base strategy."""
    from qrs import experiments

    store = _store(home)
    row = store.get_experiment(experiment_id)
    experiment = experiments.load(store, experiment_id)
    return row, store.get_strategy(experiment.strategy_id, experiment.revision)[1]


def run_dir(home: Path, run_id: str) -> Path:
    return home / "runs" / run_id


def frozen_folder(home: Path, run_id: str) -> Path:
    """The folder holding what a run was queued with, whether it has finished or is still in staging."""
    folder = run_dir(home, run_id)
    return folder if folder.exists() else home / "runs" / ".staging" / run_id


def frozen_strategy(home: Path, run_id: str) -> StrategyDocument:
    """The strategy a run was queued with."""
    return StrategyDocument.model_validate_json((frozen_folder(home, run_id) / "strategy.json").read_text())


def capped(doc: AnalysisDocument, store) -> AnalysisDocument:
    """An analysis in a project reads nothing from its confirmation start (Stage 5 P14): only a
    family's evaluation does, and is submitted without this cap."""
    if doc.project is None:
        return doc
    try:
        start = ProjectDocument.model_validate_json(store.get_project(doc.project)["document"]).confirmation_start
    except KeyError:
        raise AnalysisError(f"Project {doc.project} not found.") from None
    return doc.model_copy(update={"until": min(doc.until or start, start)})


def datasets_read(doc: AnalysisDocument, home: Path) -> list[str]:
    """Every dataset the analysis may read: its inputs', and those of the runs it reads."""
    found = []
    for given in doc.inputs.values():
        if given.kind in ("bars", "observations"):
            found += [dataset_id for dataset_id, _ in _datasets_of(given)]
        elif given.kind in RUN_INPUTS:
            found += graph.dataset_ids(frozen_strategy(home, given.run))
        elif given.kind == "experiment":
            found += graph.dataset_ids(_experiment(home, given.experiment)[1])
    return sorted(set(found))


def check(doc: AnalysisDocument, home: Path, run_status: Callable[[str], str | None], pending: tuple[str, ...] = ()) -> methods.Method:
    """Refuse a document that cannot run, listing every problem, before anything is queued.
    `run_status(run_id)` gives a run's status, or None if there is no such run. Runs in `pending` were
    queued just before the analysis (a hypothesis's holdout evaluation), so finish before it starts;
    the analysis fails if one does not succeed."""
    method = methods.get(doc.method, doc.method_version)
    missing = [
        f"Input '{name}': experiment {i.experiment} not found."
        for name, i in doc.inputs.items()
        if i.kind == "experiment" and not _experiment_exists(home, i.experiment)
    ]
    if missing:  # the cutoff below needs every experiment
        raise AnalysisError(" ".join(missing))
    problems = []
    cutoff = effective_cutoff(doc, home)
    try:
        method.params.model_validate(doc.params)
    except ValueError as e:
        problems.append(f"params: {e}")
    if set(doc.inputs) != set(method.inputs):
        problems.append(f"{method.key} takes the inputs {sorted(method.inputs)}, not {sorted(doc.inputs)}.")
    for name, given in doc.inputs.items():
        if name in method.inputs and given.kind not in method.inputs[name]:
            problems.append(f"Input '{name}' must be one of {', '.join(method.inputs[name])}, not {given.kind}.")
        for dataset_id, kind in _datasets_of(given):
            try:
                manifest = datasets.read_manifest(home, dataset_id)
            except datasets.DatasetError as e:
                problems.append(f"Input '{name}': {e}")
                continue
            if manifest.kind != kind:
                problems.append(f"Input '{name}': {dataset_id} holds {datasets.KINDS[manifest.kind]}, not {datasets.KINDS[kind]}.")
            elif kind == datasets.OBSERVATIONS and given.column not in manifest.values:
                problems.append(f"Input '{name}': the observations have no column '{given.column}'; they have {sorted(manifest.values)}.")
            elif kind == datasets.BARS and given.kind == "bars" and manifest.instrument.continuous is not None and given.measure != "price_change":
                problems.append(
                    f"Input '{name}': {manifest.name} is a continuous series, whose adjusted levels make percentages meaningless; read price_change."
                )
            elif (
                kind == datasets.BARS
                and given.kind == "bars"
                and manifest.instrument.continuous is not None
                and manifest.instrument.continuous.adjustment != "difference"
            ):
                problems.append(
                    f"Input '{name}': {manifest.name} is spliced without adjustment, so its returns include every roll gap; use a difference-adjusted series."
                )
            elif kind == datasets.BARS and given.kind == "observations" and datasets.bars_calendar(manifest) is None:
                problems.append(f"Input '{name}': {manifest.name} has no exchange calendar, so its closes, at which observations are joined, are unknown.")
            elif kind == datasets.BARS and cutoff is not None and manifest.start >= cutoff:
                problems.append(f"Input '{name}': {manifest.name} starts on {manifest.start}, so nothing is read before the cutoff {cutoff}.")
        if given.kind == "trial":
            problems.append(f"Input '{name}' refers to an experiment's trial, which only a hypothesis's evaluation resolves.")
        elif given.kind == "experiment":
            problems += [f"Input '{name}': {problem}" for problem in _experiment_problems(home, given.experiment, doc.until)]
        elif given.kind in RUN_INPUTS and given.run not in pending:
            status = run_status(given.run)
            if status != "succeeded":
                state = "does not exist." if status is None else f"is {status}; only succeeded runs can be read."
                problems.append(f"Input '{name}': run {given.run} {state}")
            else:
                found = _run_problems(home, given) or _continuous_upstream(home, given)  # the node must exist to trace its bars
                problems += [f"Input '{name}': {problem}" for problem in found]
    if not problems:
        problems += _calendar_problems(doc, home) + _budget_problems(doc, home, method.params.model_validate(doc.params))
    if problems:
        raise AnalysisError(" ".join(problems))
    return method


def _experiment_exists(home: Path, experiment_id: str) -> bool:
    try:
        _experiment(home, experiment_id)
    except KeyError:
        return False
    return True


def _experiment_problems(home: Path, experiment_id: str, until: date | None) -> list[str]:
    holdout = date.fromisoformat(_experiment(home, experiment_id)[0]["holdout_start"])
    if until is not None and until < holdout:
        # The ranking that chose the best trial read every session to the holdout start.
        return [f"experiment {experiment_id} ranked its trials on sessions to {holdout}, after until {until}; read it to its holdout start."]
    statuses = [run["status"] for run in _store(home).trial_runs(experiment_id)]
    if {"queued", "running"} & set(statuses):
        return [f"experiment {experiment_id} has trials still queued or running; its ranking could change."]
    if statuses.count("succeeded") < 2:
        return [f"experiment {experiment_id} has fewer than 2 succeeded trials to compare."]
    return []


def _run_problems(home: Path, given) -> list[str]:
    """Why a succeeded run cannot supply an input."""
    record = _run_record(home, given.run)
    if given.kind in ("equity", "fills"):
        nodes = runtime.backtest_nodes(record)
        return [] if len(nodes) == 1 else [f"run {given.run} has {len(nodes)} backtests; {given.kind} is read from runs with one."]
    if given.kind == "model":
        if f"models/{given.node}.json" not in record["outputs"]:
            return [f"run {given.run} fitted no models in a node '{given.node}'."]
        terms = _model_terms(home, given.run, given.node)
        return [] if given.term in terms else [f"the models of '{given.node}' have no term '{given.term}'; they have {terms}."]
    if f"records/{given.node}.parquet" not in record["outputs"]:
        return [f"run {given.run} has no record node '{given.node}'."]
    columns = [c for c in pyarrow.parquet.read_schema(run_dir(home, given.run) / "records" / f"{given.node}.parquet").names if c != "session"]
    return [] if given.column in columns else [f"record '{given.node}' has no column '{given.column}'; it has {columns}."]


def _continuous_upstream(home: Path, given) -> list[str]:
    """A record or model computed beside a traded contract may use a continuous series (amendment
    A3), but only a difference-adjusted one, as for bars inputs: a spliced series' levels carry every
    roll gap, which no check can see there."""
    if given.kind not in ("record", "model"):
        return []
    manifests = (datasets.read_manifest(home, d) for d in sessions_of(given, home))
    spliced = [m.name for m in manifests if m.instrument.continuous is not None and m.instrument.continuous.adjustment != "difference"]
    return [f"node '{given.node}' is fed by {', '.join(spliced)}, spliced without adjustment, so its values include every roll gap."] if spliced else []


def sessions_of(given, home: Path) -> list[str]:
    """The bars datasets whose sessions an input is on: its own, the bars an observations input is
    joined at, the bars a run's backtest traded, or those feeding a record node."""
    if given.kind == "bars":
        return [given.dataset]
    if given.kind == "observations":
        return [given.on]
    if given.kind == "experiment":
        return [_experiment(home, given.experiment)[0]["bars"]]
    strategy = frozen_strategy(home, given.run)
    return [graph.traded_bars(strategy)] if given.kind in ("equity", "fills") else graph.upstream_bars(strategy, given.node)


def _calendar_problems(doc: AnalysisDocument, home: Path) -> list[str]:
    """Inputs are matched by session, so they must be on sessions that close at the same moments: one
    exchange calendar's, or one dataset's if it has no calendar. Otherwise a value known at one
    venue's close could be paired with a return that ended earlier at another. Each input is also
    held to the session limit."""
    problems, keys = [], {}
    for name, given in doc.inputs.items():
        for dataset_id in sessions_of(given, home):
            manifest = datasets.read_manifest(home, dataset_id)
            keys.setdefault(datasets.bars_calendar(manifest) or dataset_id, []).append(name)
            if manifest.rows > MAX_SESSIONS:
                problems.append(f"Input '{name}' is on {manifest.name}, which has {manifest.rows} sessions; an analysis may read at most {MAX_SESSIONS}.")
    if len(keys) > 1:
        described = "; ".join(f"{', '.join(sorted(set(names)))} on {key}" for key, names in sorted(keys.items()))
        problems.append(f"Inputs must be on the sessions of one exchange calendar, or of one dataset without one ({described}).")
    return problems


def _budget_problems(doc: AnalysisDocument, home: Path, params) -> list[str]:
    """Resampling beyond the plan's budgets is refused before queuing (exit criterion 13): sessions
    from `since` and before the analysis's `until` (an experiment's, before its holdout start), and
    the trials that succeeded. A run's own cutoff is not counted, so the bound never understates the
    work, and a hypothesis's registration counts as its evaluation will."""
    replicates = getattr(params, "replicates", None)
    if replicates is None:
        return []
    sessions = max(_sessions_read(home, doc, given, d) for given in doc.inputs.values() for d in sessions_of(given, home))
    experiments = [given.experiment for given in doc.inputs.values() if given.kind == "experiment"]
    trials = sum(1 for e in experiments for run in _store(home).trial_runs(e) if run["status"] == "succeeded")
    work, budget = (replicates * sessions * trials, REALITY_CHECK_BUDGET) if trials else (replicates * sessions, BOOTSTRAP_BUDGET)
    if work <= budget:
        return []
    across = f" of {trials} trials" if trials else ""
    return [f"{replicates} resamples of {sessions} sessions{across} exceed the budget of {budget:,} resampled values; use fewer replicates or less data."]


def _sessions_read(home: Path, doc: AnalysisDocument, given, dataset_id: str) -> int:
    index = datasets.load(home, dataset_id).frame.index
    ends = [doc.until] + ([date.fromisoformat(_experiment(home, given.experiment)[0]["holdout_start"])] if given.kind == "experiment" else [])
    end = min((e for e in ends if e is not None), default=None)
    if doc.since:
        index = index[index >= pd.Timestamp(doc.since)]
    if end:
        index = index[index < pd.Timestamp(end)]
    return len(index)


def _datasets_of(given) -> list[tuple[str, str]]:
    """The datasets an input reads, with the kind each must be."""
    if given.kind == "bars":
        return [(given.dataset, datasets.BARS)]
    if given.kind == "observations":
        return [(given.dataset, datasets.OBSERVATIONS), (given.on, datasets.BARS)]
    return []


def _model_terms(home: Path, run_id: str, node: str) -> list[str]:
    fits = json.loads((run_dir(home, run_id) / "models" / f"{node}.json").read_text())
    return [*fits[0]["features"], "intercept"] if fits else []


def _run_record(home: Path, run_id: str) -> dict:
    return json.loads((run_dir(home, run_id) / "record.json").read_text())


def effective_cutoff(doc: AnalysisDocument, home: Path) -> date | None:
    """The earliest of the analysis's `until` and the cutoffs of the runs it reads."""
    cutoffs = [doc.until] + [runtime.frozen_until(run_dir(home, i.run)) for i in doc.inputs.values() if i.kind in RUN_INPUTS]
    cutoffs += [date.fromisoformat(_experiment(home, i.experiment)[0]["holdout_start"]) for i in doc.inputs.values() if i.kind == "experiment"]
    return min((c for c in cutoffs if c is not None), default=None)


def _selection_warning(run: str, s: dict) -> str:
    found = f"Run {run} is trial {s['trial']} of experiment {s['experiment']}"
    if s.get("costs_only"):
        found += ", a search over costs only, which chooses no strategy"
    else:
        found += f", one of {s['trials']} trials on its window"
    if s.get("repeat_look"):
        found += "; its holdout window had been evaluated before, so this is a repeat look at it"
    return found + "."


def _selected_from(store, run_id: str | None) -> tuple[str, int, dict | None] | None:
    """The experiment and trial a run is, or is the holdout evaluation of, with that holdout
    evaluation's row, following re-runs back to the run they re-ran."""
    while run_id:
        holdout = store.holdout_evaluation(run_id)
        if holdout:
            return holdout["experiment_id"], holdout["trial"], holdout
        row = store.get_run(run_id) if store.run_status(run_id) else {}
        if row.get("experiment"):
            return row["experiment"], row["trial"], None
        run_id = row.get("predecessor")
    return None


class Context:
    """The only route from an analysis to its inputs. It cuts every input at the effective cutoff
    and keeps what each input read, with its hashes, for the record."""

    def __init__(self, doc: AnalysisDocument, home: Path):
        self.doc, self.home = doc, home
        self.cutoff = effective_cutoff(doc, home)
        self.since = pd.Timestamp(doc.since) if doc.since else None
        self.run = runtime.Run(home, self.cutoff)  # reads bars and observations up to the cutoff
        self.reads: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict] = {}  # run ID -> its record
        self.selections: dict[str, dict] = {}  # run ID -> the experiment it is a trial (or holdout evaluation) of, and N

    def series(self, name: str, whole: bool = False) -> pd.Series:
        """An input as one series on its session index, from `since` if the analysis has one: session
        returns for bars and equity, joined values for observations, a column for a record, a
        session's fills, or a model term at each fit. An infinite value (a return from zero equity)
        is missing."""
        values = self._series(name)
        values = values if whole else self.from_since(values)
        return values.where(np.isfinite(values))

    def _series(self, name: str) -> pd.Series:
        given = self.doc.inputs[name]
        if given.kind == "bars":
            bars = self.run.dataset(given.dataset)
            close = bars.frame["close"]
            values = simple_returns(close, 1) if given.measure == "percent" else close.diff() * bars.instrument.multiplier
            self.reads[name] = {"kind": "bars", "dataset": given.dataset, "measure": given.measure}
            if bars.instrument.continuous is not None:  # its roll sessions, which the record reports
                since = self.since if self.since is not None else close.index[1]
                self.reads[name]["rolls"] = [s for s in datasets.roll_sessions(bars) if pd.Timestamp(s) in close.index[1:] and pd.Timestamp(s) >= since]
            return values.iloc[1:].rename(name)
        if given.kind == "observations":
            bars = self.run.dataset(given.on)
            rows = self.run.observations(given.dataset, bars)
            if given.column not in datasets.value_columns(rows):
                raise AnalysisError(f"Input '{name}': the observations have no column '{given.column}'; they have {datasets.value_columns(rows)}.")
            closes = datasets.session_closes(bars)
            self.reads[name] = {"kind": "observations", "dataset": given.dataset, "on": given.on, "column": given.column}
            return as_of_values(rows, given.column, closes, pd.Timedelta(days=given.max_age_days)).rename(name)
        folder = self._verified_run(given.run)
        self._note_selection(given.run)
        problems = _run_problems(self.home, given)
        if problems:
            raise AnalysisError(f"Input '{name}': {problems[0]}")
        self.reads[name] = given.model_dump(mode="json") | {"result_sha256": self.runs[given.run]["result_sha256"]}
        if given.kind == "equity":
            equity = self._cut(self._backtest(folder, "equity.csv")["equity"])
            return (equity / equity.shift(1) - 1).iloc[1:].rename(name)
        if given.kind == "fills":
            return self._cut(self._fills(folder, given.column)).rename(name)
        if given.kind == "model":
            fits = json.loads((folder / "models" / f"{given.node}.json").read_text())
            values = [f["intercept"] if given.term == "intercept" else f["coefficients"][f["features"].index(given.term)] for f in fits]
            return self._cut(pd.Series(values, index=pd.DatetimeIndex([f["cutoff"] for f in fits]), dtype=float)).rename(name)
        return self._cut(pd.read_parquet(folder / "records" / f"{given.node}.parquet"))[given.column].rename(name)

    def _backtest(self, folder: Path, name: str) -> pd.DataFrame:
        """A file of the run's one backtest, indexed by session."""
        node = runtime.backtest_nodes(_run_record(self.home, folder.name))[0]
        return pd.read_csv(folder / "results" / node / name, index_col="session", parse_dates=True)

    def _fills(self, folder: Path, column: str) -> pd.Series:
        """A column of the fills, summed per session over the equity's sessions; quantities sold count
        as negative."""
        fills = self._backtest(folder, "fills.csv")
        values = fills[column].where(fills["side"] == "BUY", -fills[column]) if column == "quantity" else fills[column]
        sessions = self._backtest(folder, "equity.csv").index
        return values.groupby(level="session").sum().reindex(sessions, fill_value=0).astype(float)

    def additive(self, name: str) -> bool:
        """Whether an input's returns add up over sessions (price changes per contract) rather than
        compound (fractions)."""
        given = self.doc.inputs[name]
        return given.kind == "bars" and given.measure == "price_change"

    def rows(self, name: str) -> tuple[datasets.Manifest, pd.DataFrame]:
        """A dataset input's manifest and raw rows up to the cutoff: bars, or observations published
        by the close of the last session of their `on` bars."""
        given = self.doc.inputs[name]
        if given.kind == "bars":
            frame = self.run.dataset(given.dataset).frame
        elif given.kind == "observations":
            frame = self.run.observations(given.dataset, self.run.dataset(given.on))
        else:
            raise AnalysisError(f"Input '{name}' is not a dataset.")
        self.reads[name] = {"kind": given.kind, "dataset": given.dataset} | ({"on": given.on} if given.kind == "observations" else {})
        if self.since is not None:
            frame = frame[frame.index >= self.since] if given.kind == "bars" else frame[frame["published"] >= self.since.tz_localize("UTC")]
        return self.run.datasets[given.dataset], frame

    def events(self, name: str) -> pd.DatetimeIndex:
        """The sessions of an observations input's `on` bars at whose close each value of its column
        was first known: the first close at or after its publication. Not the bars' first session: it
        has no close before it, so a value published then cannot be told from an older one."""
        given = self.doc.inputs[name]
        bars = self.run.dataset(given.on)
        rows = self.run.observations(given.dataset, bars)
        published = rows.loc[rows[given.column].notna(), "published"]
        closes = datasets.session_closes(bars)
        positions = closes.searchsorted(published.sort_values())
        self.reads[name] = {"kind": "observations", "dataset": given.dataset, "on": given.on, "column": given.column}
        sessions = pd.DatetimeIndex(sorted(set(closes.index[positions[(positions > 0) & (positions < len(closes))]])), name=name)
        return sessions[sessions >= self.since] if self.since is not None else sessions

    def experiment(self, name: str) -> Trials:
        """A finished experiment: its trials, and its succeeded trials' returns over their common
        window, verified against their records, best first by the experiment's objective."""
        from qrs import comparison, experiments
        from qrs.contracts import RunInfo
        from qrs.jobs import JobManager

        given = self.doc.inputs[name]
        store = _store(self.home)
        experiment = experiments.load(store, given.experiment)
        runs = store.trial_runs(given.experiment)
        succeeded = [run for run in runs if run["status"] == "succeeded"]
        for run in succeeded:
            self._verified_run(run["id"])
        ranked = experiments.ranking(experiment, runs, JobManager(store, self.home))
        if ranked is None or "error" in ranked:
            raise AnalysisError(f"Input '{name}': the trials cannot be ranked: {(ranked or {}).get('error', 'none succeeded')}")
        start = pd.Timestamp(ranked["window"]["start"])
        columns = {}
        for row in ranked["runs"]:
            equity = comparison.load(RunInfo.model_validate(store.get_run(row["id"])), run_dir(self.home, row["id"])).equity["equity"]
            window = self._cut(equity[equity.index >= start])
            columns[row["trial"]] = self.from_since(window / window.shift(1) - 1).dropna()
        values = experiments.points(experiment)
        trials = [
            {
                "trial": r["trial"],
                "point": {f"{node}.{param}": value for (node, param), value in values[r["trial"] - 1].items()},
                "values": experiments.describe(values[r["trial"] - 1]),
                "status": r["status"],
                "run": r["id"],
            }
            for r in runs
        ]
        self.reads[name] = {
            "kind": "experiment",
            "experiment": given.experiment,
            "trials": {str(r["trial"]): self.runs[r["id"]]["result_sha256"] for r in succeeded},
        }
        return Trials(
            objective=experiment.objective,
            direction=experiment.direction,
            trials=trials,
            # As in series(), an infinite return (from zero equity) is missing; sessions any trial lacks are left out.
            returns=pd.DataFrame(columns).replace([np.inf, -np.inf], np.nan).dropna(),
            objectives={row["trial"]: row["metrics"][experiment.objective] for row in ranked["runs"]},
            axes={f"{axis.node}.{axis.param}": axis.values for axis in experiment.axes},
            window=experiments.selections(store, given.experiment),
        )

    def _verified_run(self, run_id: str) -> Path:
        folder = run_dir(self.home, run_id)
        if run_id not in self.runs:
            problem = runtime.verify_outputs(folder)
            if problem:
                raise AnalysisError(f"Run {run_id}: {problem}")
            self.runs[run_id] = _run_record(self.home, run_id)
        return folder

    def _note_selection(self, run_id: str) -> None:
        """A run that is an experiment's trial, or the holdout evaluation of one, or a re-run of
        either, was chosen from N: best-of-N results always show N (Stage 5)."""
        from qrs import experiments

        store = _store(self.home)
        selected = _selected_from(store, run_id)
        if selected:
            experiment, trial, holdout = selected
            window = experiments.selections(store, experiment)
            self.selections[run_id] = {
                "experiment": experiment,
                "trial": trial,
                "holdout_evaluation": holdout is not None,
                "trials": sum(w["trials"] for w in window if not w["costs_only"]),
            }
            if holdout is not None and holdout["repeat"]:  # the holdout window had been evaluated before
                self.selections[run_id]["repeat_look"] = True
            if next(w for w in window if w["experiment"] == experiment)["costs_only"]:
                self.selections[run_id]["costs_only"] = True

    def _cut(self, values):
        return values[values.index < pd.Timestamp(self.cutoff)] if self.cutoff else values

    def from_since(self, values):
        """Only the sessions from `since`, if the analysis has one: its sample."""
        return values[values.index >= self.since] if self.since is not None else values

    def evidence_class(self) -> str:
        """The weakest evidence class of everything read: the datasets, and the runs' datasets."""
        classes = [m.evidence_class for m in self.run.datasets.values()] + [runtime.evidence_class(r) for r in self.runs.values()]
        return datasets.weakest(classes)


def plain(value: Any) -> Any:
    """A JSON value with numpy numbers as Python numbers, and NaN or infinity as null."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | np.ndarray | pd.Series):
        return [plain(v) for v in value]
    if hasattr(value, "item"):  # a numpy scalar
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def execute(analysis_file: Path, home: Path, out_dir: Path) -> dict[str, Any]:
    """Execute a frozen analysis document; write its tables, figures, result.json and record.json
    into out_dir, which must hold no results yet."""
    if (out_dir / "result.json").exists():
        raise AnalysisError(f"{out_dir} already holds a result; choose a new output directory.")
    doc = AnalysisDocument.model_validate_json(analysis_file.read_text())
    method = methods.get(doc.method, doc.method_version)
    ctx = Context(doc, home)
    found = method.run(ctx, method.params.model_validate(doc.params))
    undeclared = sorted((set(found.tables) | set(found.figures)) - set(method.outputs))
    if undeclared:
        raise AnalysisError(f"{method.key} produced undeclared outputs: {undeclared}")

    rights = {}  # each table or figure: rows of its inputs, or only aggregates
    for kind, items, write in (
        ("tables", found.tables, lambda table, path: path.write_text(table.to_csv(lineterminator="\n"))),
        ("figures", found.figures, lambda svg, path: path.write_text(svg)),
    ):
        for name, item in sorted(items.items()):
            path = out_dir / kind / f"{name}.{'csv' if kind == 'tables' else 'svg'}"
            path.parent.mkdir(parents=True, exist_ok=True)
            write(item, path)
            rights[path.relative_to(out_dir).as_posix()] = method.outputs[name]
    chosen = [_selection_warning(run, s) for run, s in sorted(ctx.selections.items())]
    result = {
        "method": method.key,
        "name": doc.name,
        "values": plain(found.values),
        "warnings": found.warnings + chosen,
        "selections": ctx.selections,
        "assumptions": list(method.assumptions),
        "cutoff": ctx.cutoff.isoformat() if ctx.cutoff else None,
        "evidence_class": ctx.evidence_class(),
        "outputs": rights,
    }
    (out_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    outputs = {path: sha256_file(out_dir / path) for path in sorted([*rights, "result.json"])}

    record = {
        "kind": "qrs/analysis-record",
        "version": 1,
        "document_sha256": sha256_file(analysis_file),
        "method": {"key": method.key, "module": method.module, "source_sha256": sha256_file(Path(method.source_file()))},
        "runtime": runtime.runtime_identity(),
        "scipy": version("scipy"),  # apart from the runtime identity, which runs share (amendment A1)
        "cutoff": result["cutoff"],
        "inputs": ctx.reads,
        "datasets": {i: {"name": m.name, "data_sha256": m.data_sha256, "evidence_class": m.evidence_class} for i, m in sorted(ctx.run.datasets.items())},
        "outputs": outputs,
        "result_sha256": sha256_hex(canonical_json(outputs)),
    }
    (out_dir / "record.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record
