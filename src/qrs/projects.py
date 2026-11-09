"""Research projects, hypothesis families and the read history (Stage 5 decision 3, amendment A2).

A project names its datasets in scope and a confirmation start: from it, their sessions are the test
period. Analyses in the project read only before it (analysis.capped). Hypotheses are registered in
frozen families. A confirmatory family fails closed:
- its registration is refused if anything has read the test period of a dataset in scope, and
- its evaluation is refused if, since it was registered, anything but an evaluation of one of the
  project's confirmatory families has read it. Such a family is compromised, permanently.
Every read is in the store's append-only read history, written when the read is queued or made.

Each hypothesis is evaluated once, over the test period: its analysis reads from the confirmation
start, and a reference to an experiment's trial is resolved then, by queuing that trial's holdout
evaluation. The evaluation is bound to the hypothesis once anything has read the test period for it,
so a failed one is never retried."""

import json
import threading
import time
from dataclasses import dataclass
from datetime import date

from qrs import analysis, datasets, experiments, graph, methods, stats
from qrs.contracts import AnalysisDocument, EquityInput, FamilyDocument, ProjectDocument
from qrs.hashing import canonical_json, sha256_hex
from qrs.store import NotFound, Store, now


class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class AsOf:
    """A point in the store's history: a moment, and the last read recorded by then. A report is
    fixed at one, so it reads the same however the store grows after it."""

    time: str  # as the store records times
    read: int

    @classmethod
    def now(cls, store: Store) -> "AsOf":
        moment = now()
        while now() <= moment:  # anything stored from here on is stamped after the moment
            time.sleep(0.0005)
        return cls(moment, store.last_read())


# Evaluations in this service, one at a time, so a hypothesis cannot be evaluated twice at once.
_evaluating = threading.Lock()


def create(store: Store, doc: ProjectDocument) -> str:
    for dataset_id in doc.datasets:
        datasets.read_manifest(store.home, dataset_id)  # raises DatasetError for one not found
    return store.create_project(doc.model_dump_json())


def load(store: Store, project_id: str) -> ProjectDocument:
    try:
        return ProjectDocument.model_validate_json(store.get_project(project_id)["document"])
    except NotFound:
        raise ProjectError(f"Project {project_id} not found.") from None


def reaches_test_period(read: dict, project: ProjectDocument) -> bool:
    """A read with no cutoff, or a cutoff after the confirmation start (cutoffs are exclusive)."""
    return read["until"] is None or date.fromisoformat(read["until"]) > project.confirmation_start


def horizons(store: Store, project: ProjectDocument, as_of: AsOf | None = None) -> dict[str, dict]:
    """Each dataset in scope: the latest cutoff anything has read it to (None for a full read, or if
    unread), how many reads there were, and whether any reached the test period."""
    found = {}
    for dataset_id in project.datasets:
        reads = store.reads(related(store, [dataset_id]), upto=as_of.read if as_of else None)
        cutoffs = [r["until"] for r in reads]
        found[dataset_id] = {
            "reads": len(reads),
            "read_to": None if not reads or None in cutoffs else max(cutoffs),
            "full": None in cutoffs,
            "test_period_read": any(reaches_test_period(r, project) for r in reads),
        }
    return found


def related(store: Store, dataset_ids: list[str]) -> list[str]:
    """These datasets and every other holding the same data: the same data or imported file, or
    features extracted from the same documents (and those documents). A read of any of them is a read
    of the same data, whatever its ID (amendment A2)."""
    manifests = datasets.list_manifests(store.home)

    def keys(dataset_id: str) -> set[tuple[str, str]]:
        manifest = manifests.get(dataset_id)
        if manifest is None:
            return set()
        report = getattr(manifest, "import_report", None)
        source = report.source_sha256 if report else getattr(manifest, "source_sha256", None)
        derivation = getattr(manifest, "derivation", None)
        documents = derivation.documents if derivation else dataset_id if manifest.kind == datasets.DOCUMENTS else None
        return {("data", manifest.data_sha256)} | ({("source", source)} if source else set()) | ({("documents", documents)} if documents else set())

    # To a fixed point: features of documents re-imported under another name share their reads too.
    held = {i: keys(i) for i in manifests}
    found = set(dataset_ids)
    while True:
        wanted = set().union(*(held.get(i, set()) for i in found))
        more = {i for i, k in held.items() if k & wanted} - found
        if not more:
            return sorted(found)
        found |= more


def test_period_reads(store: Store, project: ProjectDocument, after: int = 0, upto: int | None = None) -> list[dict]:
    return [r for r in store.reads(related(store, project.datasets), after, upto) if reaches_test_period(r, project)]


def register(store: Store, doc: FamilyDocument) -> str:
    """Freeze a family; refused, naming the reads, if it is confirmatory and the test period has been
    read. Each hypothesis's analysis must be one that can run on the project's scope."""
    project = load(store, doc.project)
    problems = []
    for hypothesis in doc.hypotheses:
        problems += [f"{hypothesis.id}: {p}" for p in _specification_problems(store, doc.project, project, hypothesis.analysis)]
        if hypothesis.direction != "two_sided" and methods.get(hypothesis.analysis.method, hypothesis.analysis.method_version).tests == "joint":
            problems.append(f"{hypothesis.id}: a joint test has no direction; register it as two_sided.")
    if problems:
        raise ProjectError(" ".join(problems))
    # Taken before the check: a read recorded meanwhile is one the family's evaluation must see.
    reads_seen = store.last_read()
    if doc.confirmatory:
        reads = test_period_reads(store, project)
        if reads:
            raise ProjectError(
                f"A confirmatory family cannot be registered: the test period from {project.confirmation_start} has been read "
                f"({_describe(store, reads)}). Register it as post hoc, or choose a later confirmation start in a new project."
            )
    sha256 = sha256_hex(canonical_json(doc.model_dump(mode="json")))
    return store.register_family(doc.project, doc.model_dump_json(), sha256, doc.confirmatory, reads_seen)


def _specification_problems(store: Store, project_id: str, project: ProjectDocument, spec: AnalysisDocument) -> list[str]:
    """Why a hypothesis's analysis could not be evaluated on the project's scope."""
    problems = []
    if spec.project not in (None, project_id):
        problems.append("its analysis belongs to another project.")
    for name, given in spec.inputs.items():
        if given.kind == "trial":
            try:
                row = store.get_experiment(given.experiment)
            except NotFound:
                problems.append(f"input '{name}': experiment {given.experiment} not found.")
                continue
            if not store.trial_runs(given.experiment):
                problems.append(f"input '{name}': experiment {given.experiment} has no trials.")
                continue
            if date.fromisoformat(row["holdout_start"]) > project.confirmation_start:
                problems.append(f"input '{name}': the experiment's trials read to {row['holdout_start']}, past the confirmation start.")
            experiment = experiments.load(store, given.experiment)
            _, base = store.get_strategy(experiment.strategy_id, experiment.revision)
            read = graph.dataset_ids(base)
        elif given.kind in analysis.RUN_INPUTS:
            problems.append(
                f"input '{name}': a hypothesis reads a run only through an experiment's trial, which is run on the test period when it is evaluated."
            )
            continue
        else:
            read = analysis.datasets_read(spec.model_copy(update={"inputs": {name: given}}), store.home)
        outside = sorted(set(read) - set(project.datasets))
        if outside:
            problems.append(f"input '{name}' reads datasets outside the project's scope: {', '.join(outside)}.")
    method = methods.get(spec.method, spec.method_version)
    if method.tests is None:
        problems.append(f"{method.key} tests nothing, so it cannot test a hypothesis.")
    if problems:
        return problems
    try:  # the method, parameters, input kinds and calendar, with any trial of an experiment standing for its top one
        resolved = {n: EquityInput(kind="equity", run=store.trial_runs(g.experiment)[0]["id"]) if g.kind == "trial" else g for n, g in spec.inputs.items()}
        pending = tuple(i.run for i in resolved.values() if i.kind == "equity")
        evaluated = {"inputs": resolved, "until": None, "since": project.confirmation_start}  # as its evaluation will read
        analysis.check(spec.model_copy(update=evaluated), store.home, store.run_status, pending=pending)
    except (analysis.AnalysisError, ValueError) as e:
        problems.append(str(e))
    return problems


def _describe(store: Store, reads: list[dict]) -> str:
    names = {i: m.name for i, m in datasets.list_manifests(store.home).items()}
    shown = [f"{names.get(r['dataset'], r['dataset'])} by {r['kind']} {r['source']} to {r['until'] or 'its end'}" for r in reads[:5]]
    return "; ".join(shown) + (f"; and {len(reads) - 5} more" if len(reads) > 5 else "")


def family(store: Store, family_id: str) -> tuple[dict, FamilyDocument]:
    try:
        row = store.get_family(family_id)
    except NotFound:
        raise ProjectError(f"Family {family_id} not found.") from None
    return row, FamilyDocument.model_validate_json(row["document"])


def compromising_reads(store: Store, row: dict, upto: int | None = None) -> list[dict]:
    """For a confirmatory family: reads of the test period from its registration until its last
    hypothesis was settled (to `upto`, if given), other than evaluations of the project's
    confirmatory families. Once every hypothesis is evaluated or withdrawn, no read can change what
    the family concluded."""
    project = load(store, row["project_id"])
    allowed = {f["id"] for f in store.families(row["project_id"]) if f["confirmatory"]}
    settled = settled_at(store, row)
    reads = test_period_reads(store, project, after=row["reads_seen"], upto=upto)
    # A read in the same millisecond as the settling counts: failing closed.
    return [r for r in reads if r["family"] not in allowed and (settled is None or r["recorded_at"] <= settled)]


def settled_at(store: Store, row: dict) -> str | None:
    """When a family's last hypothesis was evaluated or withdrawn, or None while any is open."""
    settled = {}
    for event in [*store.evaluations(row["id"]), *store.withdrawals(row["id"])]:
        at = event.get("requested_at") or event["withdrawn_at"]
        settled[event["hypothesis"]] = min(at, settled.get(event["hypothesis"], at))
    hypotheses = FamilyDocument.model_validate_json(row["document"]).hypotheses
    if any(h.id not in settled for h in hypotheses):
        return None
    return max(settled.values())


def withdraw(store: Store, family_id: str, hypothesis: str, reason: str) -> None:
    """Withdraw a hypothesis before it is evaluated: it counts as p = 1. Recorded, and never undone.
    An evaluated hypothesis cannot be withdrawn: its first evaluation is binding."""
    with _evaluating:  # so a withdrawal never races an evaluation of the same hypothesis
        row, doc = family(store, family_id)
        if hypothesis not in {h.id for h in doc.hypotheses}:
            raise ProjectError(f"Family {family_id} has no hypothesis '{hypothesis}'.")
        if any(w["hypothesis"] == hypothesis for w in store.withdrawals(family_id)):
            raise ProjectError(f"Hypothesis '{hypothesis}' is already withdrawn.")
        if any(e["hypothesis"] == hypothesis for e in store.evaluations(family_id)):
            raise ProjectError(f"Hypothesis '{hypothesis}' has been evaluated; its first evaluation is binding and cannot be withdrawn.")
        store.withdraw(family_id, hypothesis, reason)


def evaluate(store: Store, jobs, family_id: str, hypothesis_id: str) -> dict:
    """Evaluate a hypothesis once, over the test period: queue its analysis, and first any trial's
    holdout evaluation it refers to. Refused, with nothing read, for a withdrawn or evaluated
    hypothesis, a compromised confirmatory family, or a trial that cannot be chosen yet."""
    with _evaluating:
        return _evaluate(store, jobs, family_id, hypothesis_id)


def _evaluate(store: Store, jobs, family_id: str, hypothesis_id: str) -> dict:
    row, doc = family(store, family_id)
    hypothesis = next((h for h in doc.hypotheses if h.id == hypothesis_id), None)
    if hypothesis is None:
        raise ProjectError(f"Family {family_id} has no hypothesis '{hypothesis_id}'.")
    if any(w["hypothesis"] == hypothesis_id for w in store.withdrawals(family_id)):
        raise ProjectError(f"Hypothesis '{hypothesis_id}' was withdrawn; it counts as p = 1.")
    if any(e["hypothesis"] == hypothesis_id for e in store.evaluations(family_id)):
        raise ProjectError(f"Hypothesis '{hypothesis_id}' has been evaluated; its first evaluation is binding.")
    if row["confirmatory"]:
        reads = compromising_reads(store, row)
        if reads:
            raise ProjectError(f"The family is compromised: the test period was read outside its evaluations ({_describe(store, reads)}).")
    project = load(store, row["project_id"])
    trials = {name: _chosen_trial(store, jobs, given) for name, given in hypothesis.analysis.inputs.items() if given.kind == "trial"}
    spec = hypothesis.analysis.model_copy(update={"since": project.confirmation_start, "project": row["project_id"]})
    holdout = None
    try:
        inputs = dict(spec.inputs)
        for name, (experiment_id, trial) in trials.items():
            holdout = experiments.evaluate_holdout(store, jobs, experiment_id, trial, family=family_id)["id"]
            inputs[name] = EquityInput(kind="equity", run=holdout)
        queued = jobs.submit_analysis(
            spec.model_copy(update={"inputs": inputs}), family=family_id, pending=tuple(i.run for i in inputs.values() if i.kind == "equity")
        )
    except Exception as e:
        if holdout is None:  # nothing has read the test period: refused, not bound
            raise
        store.record_evaluation(family_id, hypothesis_id, None, holdout, str(e))
        raise ProjectError(f"The evaluation failed after its trial's holdout was queued, so it counts as p = 1: {e}") from None
    store.record_evaluation(family_id, hypothesis_id, queued["id"], holdout, None)
    return queued


def _chosen_trial(store: Store, jobs, given) -> tuple[str, int]:
    """The experiment and the number of its trial at the given rank, once every trial has finished."""
    try:
        overview = experiments.overview(store, store.get_experiment(given.experiment))
    except NotFound:
        raise ProjectError(f"Experiment {given.experiment} not found.") from None
    if overview["status"] != "finished":
        raise ProjectError(f"Experiment {given.experiment} is {overview['status']}; its trials are ranked once every one has finished.")
    ranked = experiments.ranking(overview["experiment"], store.trial_runs(given.experiment), jobs) or {"runs": []}
    if "error" in ranked or len(ranked["runs"]) < given.rank:
        raise ProjectError(f"Experiment {given.experiment} has no trial ranked {given.rank}: {ranked.get('error', 'too few trials succeeded')}.")
    return given.experiment, ranked["runs"][given.rank - 1]["trial"]


def as_of_status(analysis: dict, as_of: AsOf | None) -> str:
    """An analysis's status at a point in history: one that finished after it was still running then."""
    finished = analysis["finished_at"]
    return analysis["status"] if as_of is None or (finished is not None and finished <= as_of.time) else "running"


def status(store: Store, jobs, family_id: str, as_of: AsOf | None = None) -> dict:
    """A family as registered, with its withdrawals and evaluations, each evaluation's analysis and
    result, and whether a confirmatory family is compromised; as it stood at `as_of`, if given."""
    row, doc = family(store, family_id)
    evaluations = []
    for evaluation in store.evaluations(family_id):
        if as_of and evaluation["requested_at"] > as_of.time:
            continue
        found = {
            "hypothesis": evaluation["hypothesis"],
            "analysis": None,
            "holdout_run": evaluation["holdout_run"],
            "error": evaluation["error"],
            "result": None,
        }
        if evaluation["analysis_id"]:
            analysis = store.get_analysis(evaluation["analysis_id"])
            found["analysis"] = analysis | {"status": as_of_status(analysis, as_of)}
            result = jobs.job_dir("analysis", evaluation["analysis_id"]) / "result.json"
            succeeded = found["analysis"]["status"] == "succeeded" and result.exists()  # reports check it against its record
            found["result"] = json.loads(result.read_text()) if succeeded else None
        evaluations.append(found)
    withdrawals = [w for w in store.withdrawals(family_id) if as_of is None or w["withdrawn_at"] <= as_of.time]
    return {
        "id": family_id,
        "document": doc,
        "sha256": row["sha256"],
        "registered_at": row["registered_at"],
        "confirmatory": bool(row["confirmatory"]),
        "compromised": bool(row["confirmatory"]) and bool(compromising_reads(store, row, as_of.read if as_of else None)),
        "withdrawals": withdrawals,
        "evaluations": evaluations,
        **conclusions(doc, withdrawals, evaluations),
    }


def conclusions(doc: FamilyDocument, withdrawals: list[dict], evaluations: list[dict]) -> dict:
    """Each hypothesis's state and p-value in its registered direction, and, once every one is
    settled, the family's adjusted p-values and conclusions. Withdrawn hypotheses, and evaluations
    that failed or gave no p-value, count as p = 1. A hypothesis is supported only when its adjusted
    p-value is below the family's level; conclusions are derived, never chosen."""
    withdrawn = {w["hypothesis"] for w in withdrawals}
    evaluated = {e["hypothesis"]: e for e in evaluations}
    outcomes = []
    for hypothesis in doc.hypotheses:
        evaluation = evaluated.get(hypothesis.id)
        if hypothesis.id in withdrawn:
            state, p_value, estimate = "withdrawn", 1.0, None
        elif evaluation is None:
            state, p_value, estimate = "not evaluated", None, None
        elif evaluation["analysis"] is not None and evaluation["analysis"]["status"] in ("queued", "running"):
            state, p_value, estimate = "running", None, None
        elif evaluation["result"] is None or evaluation["result"]["values"].get("p_value") is None:
            state, p_value, estimate = "failed", 1.0, None
        else:
            values = evaluation["result"]["values"]
            estimate = values.get("estimate")
            state, p_value = "evaluated", stats.one_sided(values["p_value"], estimate or 0.0, hypothesis.direction)
        outcomes.append({"hypothesis": hypothesis.id, "state": state, "estimate": estimate, "p_value": p_value})
    complete = all(o["p_value"] is not None for o in outcomes)
    if complete:
        adjusted = stats.adjust([o["p_value"] for o in outcomes], doc.correction)
        for outcome, p in zip(outcomes, adjusted, strict=True):
            outcome |= {"adjusted_p_value": p, "supported": outcome["state"] == "evaluated" and p < doc.alpha}
    return {"outcomes": outcomes, "complete": complete}


def summary(store: Store, jobs, project_id: str) -> dict:
    """A project with its notes, the read horizons of its datasets, its families and how many of its
    analyses ended each way, failed ones included."""
    project = load(store, project_id)
    statuses = [a["status"] for a in store.project_analyses(project_id)]
    return {
        "id": project_id,
        "document": project,
        "created_at": store.get_project(project_id)["created_at"],
        "notes": store.notes(project_id),
        "horizons": horizons(store, project),
        "families": [status(store, jobs, f["id"]) for f in store.families(project_id)],
        "analyses": {s: statuses.count(s) for s in sorted(set(statuses))},
    }
