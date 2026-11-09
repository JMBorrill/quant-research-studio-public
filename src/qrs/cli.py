"""Command line: qrs serve | demo | import | extract | run | experiment | analyse | project | family | report | export | schema."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError
from pydantic.json_schema import GenerateJsonSchema, models_json_schema

from qrs import analysis, compiler, datasets, experiments, export, extraction, graph, importer, methods, projects, reports, responses, templates
from qrs.contracts import (
    AnalysisDocument,
    AnalysisInfo,
    DatasetManifest,
    DocumentsDeclaration,
    DocumentsManifest,
    Experiment,
    Extraction,
    FamilyDocument,
    Hypothesis,
    ImportDeclaration,
    Issue,
    MethodInfo,
    ObservationsDeclaration,
    ObservationsManifest,
    OperatorInfo,
    ProjectDocument,
    ReportDocument,
    RunInfo,
    StrategyDocument,
    Template,
)
from qrs.jobs import TIME_LIMIT, JobManager
from qrs.runtime import RunError, execute
from qrs.store import Store

DEFAULT_HOME = Path(".qrs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qrs", description="Quant Research Studio")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Start the local studio service on 127.0.0.1.")
    serve.add_argument("--home", type=Path, default=DEFAULT_HOME, help="Application data directory (default .qrs).")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument(
        "--time-limit", type=_minutes, default=TIME_LIMIT / 60, help=f"Minutes a run may take before its worker is stopped (default {TIME_LIMIT / 60:g})."
    )

    demo = commands.add_parser("demo", help="Create the synthetic dataset and an EMA crossover strategy.")
    demo.add_argument("--home", type=Path, default=DEFAULT_HOME)

    imp = commands.add_parser("import", help="Import a CSV file of daily bars, observations or documents as a dataset, as declared in a JSON file.")
    imp.add_argument("csv", type=Path)
    imp.add_argument(
        "declaration",
        type=Path,
        help='ImportDeclaration JSON (bars), or ObservationsDeclaration or DocumentsDeclaration JSON with "kind": "qrs/observations" or "qrs/documents".',
    )
    imp.add_argument("--home", type=Path, default=DEFAULT_HOME)

    extract = commands.add_parser(
        "extract", help="Build a feature dataset from documents, as an extraction JSON file declares, from stored model responses; prints its ID."
    )
    extract.add_argument("extraction", type=Path, help="Extraction JSON: the documents, the provider and model, the prompt, the fields and the budget.")
    extract.add_argument("--record", action="store_true", help="First ask the provider for any response not yet stored, within the budget.")
    extract.add_argument("--home", type=Path, default=DEFAULT_HOME)

    run = commands.add_parser("run", help="Compile a strategy JSON file and run it in this process.")
    run.add_argument("strategy", type=Path)
    run.add_argument("--data", type=Path, default=DEFAULT_HOME, help="Directory holding datasets/ (default .qrs).")
    run.add_argument("--out", type=Path, required=True, help="New directory for strategy.py, results and record.json.")
    run.add_argument("--until", type=date.fromisoformat, help="Read no data on or after this date (YYYY-MM-DD).")

    experiment = commands.add_parser("experiment", help="Validate an experiment JSON file and queue its trials, which `qrs serve` runs; prints the trials.")
    experiment.add_argument("experiment", type=Path, help="Experiment JSON: the base strategy revision, the search, the objective and the holdout start.")
    experiment.add_argument("--home", type=Path, default=DEFAULT_HOME)

    analyse = commands.add_parser("analyse", help="Check an analysis JSON file and run it in this process.")
    analyse.add_argument("analysis", type=Path, help="Analysis JSON: the method and version, its parameters and inputs, and an optional until.")
    analyse.add_argument("--home", type=Path, default=DEFAULT_HOME, help="Directory holding datasets/ and runs/ (default .qrs).")
    analyse.add_argument("--out", type=Path, required=True, help="New directory for analysis.json, tables, figures, result.json and record.json.")

    project = commands.add_parser("project", help="Create a research project from a project JSON file; prints its ID and its datasets' read horizons.")
    project.add_argument("project", type=Path, help="Project JSON: the question, the datasets in scope and the confirmation start.")
    project.add_argument("--home", type=Path, default=DEFAULT_HOME)

    family = commands.add_parser("family", help="Register a family of hypotheses from a family JSON file, frozen; prints its ID.")
    family.add_argument("family", type=Path, help="Family JSON: the project, confirmatory or not, the level, the correction and the hypotheses.")
    family.add_argument("--home", type=Path, default=DEFAULT_HOME)

    report = commands.add_parser("report", help="Build a report from stored artefacts, or verify one against the store.")
    actions = report.add_subparsers(dest="action", required=True)
    build = actions.add_parser("build", help="Write report.html, evidence.json and record.json into a new directory.")
    build.add_argument("report", type=Path, help="Report JSON: the project, a title and sections of blocks.")
    build.add_argument("--home", type=Path, default=DEFAULT_HOME)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--shareable", action="store_true", help="Refuse a report that includes data whose terms forbid redistribution.")
    check = actions.add_parser("verify", help="Rebuild a report as of when it was built, and compare.")
    check.add_argument("dir", type=Path)
    check.add_argument("--home", type=Path, default=DEFAULT_HOME)

    exp = commands.add_parser("export", help="Write a standalone Python bundle (ZIP) for a strategy JSON file.")
    exp.add_argument("strategy", type=Path)
    exp.add_argument("bundle", type=Path)
    exp.add_argument("--data", type=Path, default=DEFAULT_HOME)

    schema = commands.add_parser("schema", help="Write the JSON Schema for the canonical contracts.")
    schema.add_argument("--out", type=Path, default=Path("schemas/qrs.schema.json"))

    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (
        analysis.AnalysisError,
        methods.MethodError,
        compiler.InvalidStrategy,
        datasets.DatasetError,
        experiments.ExperimentError,
        projects.ProjectError,
        reports.ReportError,
        extraction.ExtractionError,
        responses.ResponseError,
        RunError,
        ValidationError,
    ) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _minutes(text: str) -> float:
    minutes = float(text)
    if minutes <= 0:
        raise argparse.ArgumentTypeError("the time limit must be more than 0 minutes")
    return minutes


def serve(args) -> int:
    import uvicorn

    from qrs.api import create_app

    uvicorn.run(create_app(args.home, time_limit=args.time_limit * 60), host="127.0.0.1", port=args.port)
    return 0


def demo(args) -> int:
    dataset_id = datasets.create_synthetic(args.home)
    strategy_id, _ = Store(args.home / "qrs.sqlite").create_strategy(templates.instantiate("ema_crossover", dataset_id))
    print(f"dataset  {dataset_id}\nstrategy {strategy_id} (EMA crossover)")
    return 0


def import_file(args) -> int:
    declaration = importer.parse_declaration(args.declaration.read_bytes())
    if isinstance(declaration, ObservationsDeclaration):
        dataset_id = importer.import_observations(args.home, args.csv.read_bytes(), declaration)
        manifest = datasets.read_manifest(args.home, dataset_id)
        print(f"dataset  {dataset_id}\nobservations: {manifest.rows}, {manifest.estimated_rows} with estimated publication times")
        return 0
    if isinstance(declaration, DocumentsDeclaration):
        dataset_id = importer.import_documents(args.home, args.csv.read_bytes(), declaration)
        manifest = datasets.read_manifest(args.home, dataset_id)
        print(f"dataset  {dataset_id}\ndocuments: {manifest.documents} ({manifest.rows} versions), {manifest.estimated_rows} with estimated publication times")
        return 0
    dataset_id = importer.import_csv(args.home, args.csv.read_bytes(), declaration)
    report = datasets.read_manifest(args.home, dataset_id).import_report
    print(f"dataset  {dataset_id}\nmissing sessions ({report.calendar}): {len(report.missing_sessions)}")
    for session in report.missing_sessions:
        print(f"  {session}")
    return 0


def extract_features(args) -> int:
    spec = Extraction.model_validate_json(args.extraction.read_bytes())
    # Extraction reads every document, so it is a full read of them and of the features it makes.
    Store(args.home / "qrs.sqlite").record_reads([spec.documents], None, "extract", spec.name)
    dataset_id = extraction.extract(args.home, spec, record=args.record)
    manifest = datasets.read_manifest(args.home, dataset_id)
    d = manifest.derivation
    invalid = ", ".join(f"{reason} {n}" for reason, n in d.invalid_rows.items()) or "none"
    print(f"dataset  {dataset_id}\nrows: {manifest.rows} ({d.duplicates} duplicates dropped); invalid: {invalid}")
    print(f"evidence class: {manifest.evidence_class} ({d.retrospective_rows} rows captured after they could be used)")
    return 0


def run(args) -> int:
    doc = StrategyDocument.model_validate_json(args.strategy.read_bytes())
    if args.out.exists() and any(args.out.iterdir()):
        raise RunError(f"{args.out} is not empty; choose a new output directory.")
    code = compiler.compile_for_data(doc, args.data, args.until)
    Store(args.data / "qrs.sqlite").record_reads(graph.dataset_ids(doc), args.until, "cli run", str(args.out))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "strategy.py").write_text(code)
    record = execute(args.out / "strategy.py", args.data, args.out, args.until)
    print(json.dumps({"code_sha256": record["code_sha256"], "result_sha256": record["result_sha256"]}, indent=2))
    return 0


def queue_experiment(args) -> int:
    store = Store(args.home / "qrs.sqlite")
    experiment = Experiment.model_validate_json(args.experiment.read_bytes())
    experiment_id = experiments.create(store, JobManager(store, args.home), experiment)
    print(f"experiment {experiment_id}: {len(store.trial_runs(experiment_id))} trials queued; `qrs serve --home {args.home}` runs them")
    for run, values in zip(store.trial_runs(experiment_id), experiments.points(experiment), strict=True):
        print(f"  trial {run['trial']}  run {run['id']}  {experiments.describe(values)}")
    return 0


def run_analysis(args) -> int:
    doc = AnalysisDocument.model_validate_json(args.analysis.read_bytes())
    if args.out.exists() and any(args.out.iterdir()):
        raise analysis.AnalysisError(f"{args.out} is not empty; choose a new output directory.")
    store = Store(args.home / "qrs.sqlite")
    doc = analysis.capped(doc, store)
    analysis.check(doc, args.home, store.run_status)
    store.record_reads(analysis.datasets_read(doc, args.home), analysis.effective_cutoff(doc, args.home), "cli analysis", str(args.out))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "analysis.json").write_text(doc.model_dump_json(indent=2) + "\n")
    record = analysis.execute(args.out / "analysis.json", args.home, args.out)
    print(json.dumps({"result_sha256": record["result_sha256"], **json.loads((args.out / "result.json").read_text())["values"]}, indent=2))
    return 0


def create_project(args) -> int:
    store = Store(args.home / "qrs.sqlite")
    project_id = projects.create(store, ProjectDocument.model_validate_json(args.project.read_bytes()))
    print(f"project {project_id}")
    for dataset_id, horizon in projects.horizons(store, projects.load(store, project_id)).items():
        read = "in full" if horizon["full"] else f"to {horizon['read_to']}" if horizon["read_to"] else "never"
        print(f"  {dataset_id}  read {read}{' (test period read)' if horizon['test_period_read'] else ''}")
    return 0


def register_family(args) -> int:
    store = Store(args.home / "qrs.sqlite")
    doc = FamilyDocument.model_validate_json(args.family.read_bytes())
    family_id = projects.register(store, doc)
    print(f"family {family_id}: {'confirmatory' if doc.confirmatory else 'post hoc'}, {len(doc.hypotheses)} hypotheses, {doc.correction} at {doc.alpha}")
    return 0


def report_command(args) -> int:
    store = Store(args.home / "qrs.sqlite")
    jobs = JobManager(store, args.home)
    if args.action == "build":
        record = reports.build(store, jobs, ReportDocument.model_validate_json(args.report.read_bytes()), args.out, shareable=args.shareable)
        print(f"wrote {args.out}: report_sha256 {record['report_sha256']}")
        return 0
    problems = reports.verify(store, jobs, args.dir)
    for problem in problems:
        print(problem)
    print("verified" if not problems else "NOT verified")
    return 1 if problems else 0


def export_bundle(args) -> int:
    doc = StrategyDocument.model_validate_json(args.strategy.read_bytes())
    # Recorded first: a bundle runs anywhere on all its data, so it is a full read (amendment A2).
    Store(args.data / "qrs.sqlite").record_reads(graph.dataset_ids(doc), None, "export", str(args.bundle))
    args.bundle.write_bytes(export.build(doc, args.data))
    print(f"wrote {args.bundle}")
    return 0


class _WithoutFieldTitles(GenerateJsonSchema):
    """Field titles would become one TypeScript alias per field; model titles are kept."""

    def field_title_should_be_set(self, schema) -> bool:
        return False


def write_schema(args) -> int:
    models = [
        (m, "validation")
        for m in (
            StrategyDocument,
            OperatorInfo,
            Issue,
            DatasetManifest,
            ImportDeclaration,
            ObservationsManifest,
            ObservationsDeclaration,
            DocumentsManifest,
            DocumentsDeclaration,
            Template,
            RunInfo,
            Experiment,
            Extraction,
            AnalysisDocument,
            AnalysisInfo,
            MethodInfo,
            ProjectDocument,
            FamilyDocument,
            Hypothesis,
            ReportDocument,
        )
    ]
    _, schema = models_json_schema(models, title="Quant Research Studio contracts", schema_generator=_WithoutFieldTitles)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.out}")
    return 0


COMMANDS = {
    "serve": serve,
    "demo": demo,
    "import": import_file,
    "extract": extract_features,
    "run": run,
    "experiment": queue_experiment,
    "analyse": run_analysis,
    "project": create_project,
    "family": register_family,
    "report": report_command,
    "export": export_bundle,
    "schema": write_schema,
}

if __name__ == "__main__":
    sys.exit(main())
