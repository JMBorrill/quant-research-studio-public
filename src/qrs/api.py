"""Local HTTP API for the studio. Binds to loopback only; requests with any other Host header are
refused (a guard against DNS rebinding). The built studio is served from the same origin."""

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from qrs import analysis, comparison, datasets, experiments, export, graph, importer, methods, projects, registry, reports, runtime, templates
from qrs.compiler import InvalidStrategy, compile_strategy
from qrs.contracts import (
    AnalysisDocument,
    AnalysisInfo,
    DocumentsDeclaration,
    Experiment,
    FamilyDocument,
    Issue,
    MethodInfo,
    ObservationsDeclaration,
    OperatorInfo,
    ProjectDocument,
    ReportDocument,
    RunInfo,
    StrategyDocument,
)
from qrs.graph import effective_warm_up, validate
from qrs.hashing import sha256_hex
from qrs.jobs import TIME_LIMIT, JobManager, NotCancellable
from qrs.store import NotFound, StaleRevision, Store

STUDIO_DIST = Path(__file__).resolve().parents[2] / "apps" / "studio" / "dist"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class CreateStrategy(BaseModel):
    template_id: str | None = None
    dataset_id: str | None = None
    datasets: dict[str, str] | None = None  # by kind, for templates that read more than bars
    document: StrategyDocument | None = None


class SaveStrategy(BaseModel):
    revision: int
    document: StrategyDocument


class StartRun(BaseModel):
    revision: int


class CompileRequest(BaseModel):
    document: StrategyDocument


class CompareRequest(BaseModel):
    run_ids: list[str]


class HoldoutRequest(BaseModel):
    trial: int = Field(ge=1, strict=True)


class NoteRequest(BaseModel):
    text: str = Field(min_length=1)


class WithdrawRequest(BaseModel):
    hypothesis: str
    reason: str = Field(min_length=1)


class EvaluateRequest(BaseModel):
    hypothesis: str


def create_app(home: Path, allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost"), time_limit: float = TIME_LIMIT) -> FastAPI:
    """The service; `time_limit` is the seconds a run may take before its worker is stopped."""
    registry.all_operators()  # import extension modules now, so a misconfiguration fails at startup
    store = Store(home / "qrs.sqlite")
    jobs = JobManager(store, home, time_limit)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        jobs.start()
        yield
        jobs.stop()

    app = FastAPI(title="Quant Research Studio", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    app.state.jobs = jobs

    def strategy(strategy_id: str, revision: int | None = None) -> tuple[int, StrategyDocument]:
        try:
            return store.get_strategy(strategy_id, revision)
        except NotFound:
            raise HTTPException(404, f"Strategy {strategy_id} (revision {revision or 'latest'}) not found.") from None

    def run(run_id: str) -> RunInfo:
        try:
            return RunInfo.model_validate(store.get_run(run_id))
        except NotFound:
            raise HTTPException(404, f"Run {run_id} not found.") from None

    def unprocessable(issues: list[Issue]) -> HTTPException:
        return HTTPException(422, {"message": "The strategy is not valid.", "issues": [i.model_dump() for i in issues]})

    @app.get("/api/operators")
    def operators() -> list[OperatorInfo]:
        return [op.info() for op in registry.all_operators()]

    @app.get("/api/templates")
    def list_templates() -> list[dict]:
        return templates.list_templates()

    def dataset(dataset_id: str, manifest: datasets.Manifest) -> dict:
        return {"id": dataset_id, **manifest.model_dump(mode="json")}

    @app.get("/api/datasets")
    def list_datasets() -> list[dict]:
        return [dataset(i, m) for i, m in datasets.list_manifests(home).items()]

    @app.post("/api/datasets/import")
    async def import_dataset(
        request: Request, declaration: Annotated[str, Query(description="ImportDeclaration, ObservationsDeclaration or DocumentsDeclaration JSON.")]
    ) -> dict:
        try:
            parsed = importer.parse_declaration(declaration)
        except ValidationError as e:
            raise RequestValidationError([{**error, "loc": ("query", "declaration", *error["loc"])} for error in e.errors(include_url=False)]) from None
        # text/csv is not a CORS-safelisted type, so a browser must ask before another site may post one.
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "text/csv":
            raise HTTPException(415, "Send the CSV file as the request body, with Content-Type text/csv.")
        content = bytearray()
        async for chunk in request.stream():
            content += chunk
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"The file is larger than the {MAX_UPLOAD_BYTES // 2**20} MB upload limit.")
        try:
            load = {ObservationsDeclaration: importer.import_observations, DocumentsDeclaration: importer.import_documents}.get(
                type(parsed), importer.import_csv
            )
            dataset_id = await run_in_threadpool(load, home, bytes(content), parsed)
        except datasets.DatasetError as e:
            raise HTTPException(422, str(e)) from None
        return dataset(dataset_id, datasets.read_manifest(home, dataset_id))

    @app.get("/api/strategies")
    def list_strategies() -> list[dict]:
        return store.list_strategies()

    @app.post("/api/strategies")
    def create_strategy(body: CreateStrategy) -> dict:
        if body.template_id:
            try:
                doc = templates.instantiate(body.template_id, body.dataset_id, chosen=body.datasets)
            except KeyError as e:
                raise HTTPException(404, str(e)) from None
        elif body.document:
            doc = body.document
        else:
            raise HTTPException(422, "Provide template_id or document.")
        strategy_id, revision = store.create_strategy(doc)
        return {"id": strategy_id, "revision": revision, "document": doc}

    @app.get("/api/strategies/{strategy_id}")
    def get_strategy(strategy_id: str, revision: int | None = None) -> dict:
        rev, doc = strategy(strategy_id, revision)
        return {"id": strategy_id, "revision": rev, "document": doc}

    @app.put("/api/strategies/{strategy_id}")
    def save_strategy(strategy_id: str, body: SaveStrategy) -> dict:
        try:
            return {"revision": store.save_strategy(strategy_id, body.revision, body.document)}
        except NotFound:
            raise HTTPException(404, f"Strategy {strategy_id} not found.") from None
        except StaleRevision as e:
            raise HTTPException(409, {"message": str(e), "revision": e.current}) from None

    @app.post("/api/compile")
    def compile_document(body: CompileRequest) -> dict:
        """Issues, or the generated code and each node's effective warm-up (shown on the canvas)."""
        issues = validate(body.document)
        if issues:
            return {"issues": issues, "code": None, "code_sha256": None, "warm_up": None}
        code = compile_strategy(body.document)
        return {"issues": [], "code": code, "code_sha256": sha256_hex(code.encode()), "warm_up": effective_warm_up(body.document)}

    def submit(strategy_id: str, revision: int, doc: StrategyDocument, **options) -> RunInfo:
        try:
            return RunInfo.model_validate(jobs.submit(strategy_id, revision, doc, **options))
        except InvalidStrategy as e:
            raise unprocessable(e.issues) from None
        except datasets.DatasetError as e:
            raise unprocessable([Issue(message=str(e))]) from None

    @app.post("/api/strategies/{strategy_id}/runs")
    def start_run(strategy_id: str, body: StartRun) -> RunInfo:
        return submit(strategy_id, *strategy(strategy_id, body.revision))

    @app.post("/api/runs/{run_id}/rerun")
    def rerun(run_id: str) -> RunInfo:
        """Run the same strategy again, as it was frozen with the run and with its cutoff, recording
        which run it repeats."""
        previous = run(run_id)
        if store.evaluated_holdout(run_id):
            raise HTTPException(409, "This run evaluated an experiment's holdout; evaluate it again from the experiment, which records every evaluation.")
        doc, until = jobs.frozen(run_id)
        return submit(previous.strategy_id, previous.revision, doc, predecessor=run_id, until=until)

    def experiment_row(experiment_id: str) -> dict:
        try:
            return store.get_experiment(experiment_id)
        except NotFound:
            raise HTTPException(404, f"Experiment {experiment_id} not found.") from None

    def experiment_summary(experiment_id: str) -> dict:
        experiment_row(experiment_id)
        return experiments.summary(store, jobs, experiment_id)

    @app.post("/api/experiments")
    def create_experiment(body: Experiment) -> dict:
        try:
            return experiment_summary(experiments.create(store, jobs, body))
        except (experiments.ExperimentError, datasets.DatasetError) as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/experiments")
    def list_experiments(strategy_id: str | None = None) -> list[dict]:
        listed = [experiments.overview(store, row) for row in store.list_experiments()]
        return [e for e in listed if strategy_id in (None, e["experiment"].strategy_id)]

    @app.get("/api/experiments/{experiment_id}")
    def get_experiment(experiment_id: str) -> dict:
        return experiment_summary(experiment_id)

    @app.post("/api/experiments/{experiment_id}/cancel")
    def cancel_experiment(experiment_id: str) -> dict:
        experiment_row(experiment_id)
        experiments.cancel(store, jobs, experiment_id)
        return experiment_summary(experiment_id)

    @app.post("/api/experiments/{experiment_id}/holdout")
    def evaluate_holdout(experiment_id: str, body: HoldoutRequest) -> dict:
        """Run a succeeded trial on the full data; every evaluation is recorded, repeats flagged."""
        experiment_row(experiment_id)
        try:
            experiments.evaluate_holdout(store, jobs, experiment_id, body.trial)
        except experiments.ExperimentError as e:
            raise HTTPException(422, str(e)) from None
        return experiment_summary(experiment_id)

    @app.post("/api/compare")
    def compare_runs(body: CompareRequest) -> dict:
        runs = [run(run_id) for run_id in body.run_ids]
        try:
            return comparison.compare([(r, jobs.run_dir(r.id)) for r in runs])
        except comparison.ComparisonError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/runs")
    def list_runs(strategy_id: str | None = None) -> list[RunInfo]:
        return [RunInfo.model_validate(r) for r in store.list_runs(strategy_id)]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> RunInfo:
        return run(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> RunInfo:
        run(run_id)
        try:
            jobs.cancel(run_id)
        except NotCancellable as e:
            raise HTTPException(409, str(e)) from None
        return run(run_id)

    @app.get("/api/runs/{run_id}/log", response_class=PlainTextResponse)
    def run_log(run_id: str) -> str:
        status = run(run_id).status
        folder = jobs.staging_dir / run_id if status in ("queued", "running") else jobs.run_dir(run_id)
        log = folder / "log.txt"
        return log.read_text(errors="replace") if log.exists() else ""

    @app.get("/api/runs/{run_id}/result")
    def run_result(run_id: str) -> dict:
        if run(run_id).status != "succeeded":
            raise HTTPException(409, "Only succeeded runs have results.")
        folder = jobs.run_dir(run_id)
        record = json.loads((folder / "record.json").read_text())
        # Runs from before warm-up was recorded have no warm_up.json.
        warm_up_file = folder / "warm_up.json"
        warm_up = json.loads(warm_up_file.read_text()) if warm_up_file.exists() else {}
        results = {}
        for node in runtime.backtest_nodes(record):
            base = folder / "results" / node
            fills = pd.read_csv(base / "fills.csv")
            equity = pd.read_csv(base / "equity.csv")
            sessions = warm_up.get(node)
            first_signal = equity["session"].iloc[sessions] if sessions is not None and sessions < len(equity) else None
            results[node] = {
                **json.loads((base / "summary.json").read_text()),
                "warm_up_sessions": sessions,
                "first_signal_session": first_signal,
                "fills": fills.to_dict("records"),
                "equity": equity.to_dict("records"),
            }
        return {"record": record, "evidence_class": runtime.evidence_class(record), "results": results}

    @app.get("/api/strategies/{strategy_id}/export")
    def export_strategy(strategy_id: str, revision: int | None = None) -> Response:
        rev, doc = strategy(strategy_id, revision)
        # A bundle can run anywhere on all its data, so an export is a full read (amendment A2).
        store.record_reads(graph.dataset_ids(doc), None, "export", f"{strategy_id}@{rev}")
        try:
            bundle = export.build(doc, home)
        except InvalidStrategy as e:
            raise unprocessable(e.issues) from None
        except ValueError as e:  # e.g. a dataset that is missing or changed, or an unexportable module
            raise unprocessable([Issue(message=str(e))]) from None
        filename = f"strategy-{strategy_id}-r{rev}.zip"
        return Response(bundle, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/api/methods")
    def list_methods() -> list[MethodInfo]:
        try:
            return [m.info() for m in methods.all_methods()]
        except methods.MethodError as e:
            raise HTTPException(422, str(e)) from None

    def analysis_info(analysis_id: str) -> AnalysisInfo:
        try:
            return AnalysisInfo.model_validate(store.get_analysis(analysis_id))
        except NotFound:
            raise HTTPException(404, f"Analysis {analysis_id} not found.") from None

    @app.post("/api/analyses")
    def create_analysis(body: AnalysisDocument) -> AnalysisInfo:
        """Check the document and queue it; refused with the reasons before anything is queued."""
        try:
            return AnalysisInfo.model_validate(jobs.submit_analysis(body))
        except (analysis.AnalysisError, methods.MethodError, datasets.DatasetError) as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/analyses")
    def list_analyses() -> list[AnalysisInfo]:
        return [AnalysisInfo.model_validate(a) for a in store.list_analyses()]

    @app.get("/api/analyses/{analysis_id}")
    def get_analysis(analysis_id: str) -> AnalysisInfo:
        return analysis_info(analysis_id)

    @app.post("/api/analyses/{analysis_id}/cancel")
    def cancel_analysis(analysis_id: str) -> AnalysisInfo:
        analysis_info(analysis_id)
        try:
            jobs.cancel(analysis_id, kind="analysis")
        except NotCancellable as e:
            raise HTTPException(409, str(e)) from None
        return analysis_info(analysis_id)

    @app.get("/api/analyses/{analysis_id}/result")
    def analysis_result(analysis_id: str) -> dict:
        """The document, record and result of a succeeded analysis, with its tables' rows."""
        if analysis_info(analysis_id).status != "succeeded":
            raise HTTPException(409, "Only succeeded analyses have results.")
        folder = jobs.job_dir("analysis", analysis_id)
        result = json.loads((folder / "result.json").read_text())
        tables = {Path(path).stem: pd.read_csv(folder / path).to_dict("records") for path in result["outputs"] if path.startswith("tables/")}
        return {
            "document": json.loads((folder / "analysis.json").read_text()),
            "record": json.loads((folder / "record.json").read_text()),
            "result": result,
            "tables": tables,
        }

    @app.get("/api/analyses/{analysis_id}/figures/{name}")
    def analysis_figure(analysis_id: str, name: str) -> Response:
        """A figure as SVG, for display as an image: a sandboxing policy forbids scripts in it."""
        if analysis_info(analysis_id).status != "succeeded":
            raise HTTPException(409, "Only succeeded analyses have figures.")
        folder = jobs.job_dir("analysis", analysis_id)
        path = f"figures/{name}.svg"
        if path not in json.loads((folder / "result.json").read_text())["outputs"]:
            raise HTTPException(404, f"Analysis {analysis_id} has no figure {name}.")
        headers = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox", "X-Content-Type-Options": "nosniff"}
        return Response((folder / path).read_bytes(), media_type="image/svg+xml", headers=headers)

    def project_call(call, *args):
        try:
            return call(*args)
        except (
            projects.ProjectError,
            reports.ReportError,
            datasets.DatasetError,
            analysis.AnalysisError,
            methods.MethodError,
            experiments.ExperimentError,
            InvalidStrategy,  # a hypothesis's trial whose strategy no longer compiles here
        ) as e:
            missing = isinstance(e, projects.ProjectError) and str(e).endswith("not found.")
            raise HTTPException(404 if missing else 422, str(e)) from None

    @app.post("/api/projects")
    def create_project(body: ProjectDocument) -> dict:
        return projects.summary(store, jobs, project_call(projects.create, store, body))

    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        return [
            {"id": row["id"], "document": ProjectDocument.model_validate_json(row["document"]), "created_at": row["created_at"]}
            for row in store.list_projects()
        ]

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict:
        return project_call(projects.summary, store, jobs, project_id)

    @app.post("/api/projects/{project_id}/notes")
    def add_note(project_id: str, body: NoteRequest) -> dict:
        project_call(projects.load, store, project_id)
        store.add_note(project_id, body.text)
        return projects.summary(store, jobs, project_id)

    @app.post("/api/families")
    def register_family(body: FamilyDocument) -> dict:
        return projects.status(store, jobs, project_call(projects.register, store, body))

    @app.get("/api/families/{family_id}")
    def get_family(family_id: str) -> dict:
        return project_call(projects.status, store, jobs, family_id)

    @app.post("/api/families/{family_id}/withdraw")
    def withdraw_hypothesis(family_id: str, body: WithdrawRequest) -> dict:
        project_call(projects.withdraw, store, family_id, body.hypothesis, body.reason)
        return projects.status(store, jobs, family_id)

    @app.post("/api/families/{family_id}/evaluate")
    def evaluate_hypothesis(family_id: str, body: EvaluateRequest) -> dict:
        """Evaluate one hypothesis over the test period, once; refused with nothing read if it cannot be."""
        project_call(projects.evaluate, store, jobs, family_id, body.hypothesis)
        return projects.status(store, jobs, family_id)

    reports_dir = home / "reports"

    def report_dir(report_id: str) -> Path:
        folder = reports_dir / report_id
        if not (len(report_id) == 12 and report_id.isalnum() and (folder / "record.json").exists()):
            raise HTTPException(404, f"Report {report_id} not found.")
        return folder

    def report_summary(report_id: str) -> dict:
        folder = report_dir(report_id)
        return {
            "id": report_id,
            "document": json.loads((folder / "report.json").read_text()),
            "record": json.loads((folder / "record.json").read_text()),
            "evidence": json.loads((folder / "evidence.json").read_text()),
        }

    @app.post("/api/reports")
    def build_report(body: ReportDocument) -> dict:
        """Build a report as of now, from stored artefacts."""
        report_id = uuid.uuid4().hex[:12]
        project_call(reports.build, store, jobs, body, reports_dir / report_id)
        return report_summary(report_id)

    @app.get("/api/reports")
    def list_reports() -> list[dict]:
        found = sorted(reports_dir.glob("*/record.json")) if reports_dir.exists() else []
        return [{"id": f.parent.name, "document": json.loads((f.parent / "report.json").read_text())} for f in found]

    @app.get("/api/reports/{report_id}")
    def get_report(report_id: str) -> dict:
        return report_summary(report_id)

    @app.get("/api/reports/{report_id}/html")
    def report_html(report_id: str) -> Response:
        """The report, for display in a sandboxed frame: no script may run in it."""
        headers = {"Content-Security-Policy": f"{reports.POLICY}; sandbox", "X-Content-Type-Options": "nosniff"}
        return Response((report_dir(report_id) / "report.html").read_bytes(), media_type="text/html; charset=utf-8", headers=headers)

    @app.post("/api/reports/{report_id}/verify")
    def verify_report(report_id: str) -> dict:
        problems = reports.verify(store, jobs, report_dir(report_id))
        return {"verified": not problems, "problems": problems}

    if STUDIO_DIST.is_dir():
        app.mount("/", StaticFiles(directory=STUDIO_DIST, html=True), name="studio")
    return app
