"""Durable run execution. Runs are queued in SQLite and executed one at a time, each in its own worker
process, so closing the browser never affects a run and a crashed worker cannot erase its record.
Analyses (Stage 5) are a second kind of job in the same queue, prepared in analyses/.staging/<id>/
(the frozen analysis.json) and published to analyses/<id>/, with the same time limit, cancellation
and statuses.

Each run is prepared in runs/.staging/<id>/ (frozen strategy.json, compiled strategy.py and, for a run
with a cutoff, until.txt), executed there, and then moved to runs/<id>/ in one rename together with its
log and any error, whatever the outcome. Only runs whose output hashes verify are marked succeeded. A run that exceeds the time limit
has its worker terminated and fails with that reason, and the next queued run starts. At startup,
runs left marked as running by a stopped service become interrupted; re-running creates a new run."""

import contextlib
import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import date
from pathlib import Path

from qrs import analysis, graph
from qrs.compiler import compile_for_data
from qrs.contracts import AnalysisDocument, StrategyDocument
from qrs.hashing import sha256_hex
from qrs.runtime import frozen_until, verify_outputs
from qrs.store import NotFound, Store, now

TIME_LIMIT = 30 * 60  # seconds a run may take by default
KILL_AFTER = 10  # seconds a terminated worker gets to exit before it is killed


class NotCancellable(Exception):
    pass


class JobManager:
    def __init__(self, store: Store, home: Path, time_limit: float = TIME_LIMIT):
        self.store = store
        self.time_limit = time_limit
        self.data_dir = home
        self.runs_dir = home / "runs"
        self.staging_dir = self.runs_dir / ".staging"
        self.roots = {"run": self.runs_dir, "analysis": home / "analyses"}  # job kind -> where its jobs are published
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._current: str | None = None
        self._cancel_requested: set[str] = set()
        self._stopping = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.store.mark_interrupted()
        # A service that crashed left its interrupted jobs in staging; publish them with their logs.
        for kind in self.roots:
            self._staging(kind).mkdir(parents=True, exist_ok=True)
            for staging in self._staging(kind).iterdir():
                with contextlib.suppress(NotFound):
                    if self.store.get_job(kind, staging.name)["status"] == "interrupted":
                        self._publish(kind, staging.name)
        self._thread = threading.Thread(target=self._loop, name="qrs-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        with self._lock:
            if self._process:
                self._process.terminate()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)

    def submit(
        self,
        strategy_id: str,
        revision: int,
        doc: StrategyDocument,
        predecessor: str | None = None,
        until: date | None = None,
        trial: tuple[str, int] | None = None,
        family: str | None = None,
        read: str | None = None,
    ) -> dict:
        """Compile and freeze a strategy revision, then queue it, recording what it reads. `predecessor`
        is the run it re-runs, `until` its data cutoff, `trial` its experiment and trial number, and
        `family` the hypothesis family whose evaluation it is, if any; `read` names the kind of read
        when it is neither a run nor a trial (a holdout evaluation). Raises InvalidStrategy or
        DatasetError before anything is queued."""
        code = compile_for_data(doc, self.data_dir, until)
        run_id = uuid.uuid4().hex[:12]
        staging = self.staging_dir / run_id
        staging.mkdir(parents=True)
        (staging / "strategy.json").write_text(doc.model_dump_json(indent=2) + "\n")
        (staging / "strategy.py").write_text(code)
        # Frozen with the run, so its results never depend on operators registered later.
        (staging / "warm_up.json").write_text(json.dumps(graph.effective_warm_up(doc), indent=2, sort_keys=True) + "\n")
        if until is not None:
            (staging / "until.txt").write_text(until.isoformat())
        # Recorded first: a read counts once it may happen, whatever becomes of the run (amendment A2).
        self.store.record_reads(graph.dataset_ids(doc), until, read or ("trial" if trial else "run"), run_id, family)
        run = self.store.create_run(run_id, strategy_id, revision, sha256_hex(code.encode()), predecessor, trial)
        self._wake.set()
        return run

    def submit_analysis(self, doc: AnalysisDocument, family: str | None = None, pending: tuple[str, ...] = ()) -> dict:
        """Check and freeze an analysis document, then queue it, recording what it reads. One in a
        project reads only before its confirmation start, unless it is a `family`'s evaluation; runs
        in `pending` are queued ahead of it (see analysis.check). Raises AnalysisError or MethodError
        before anything is queued."""
        doc = doc if family else analysis.capped(doc, self.store)
        method = analysis.check(doc, self.data_dir, self.store.run_status, pending)
        analysis_id = uuid.uuid4().hex[:12]
        staging = self._staging("analysis") / analysis_id
        staging.mkdir(parents=True)
        (staging / "analysis.json").write_text(doc.model_dump_json(indent=2) + "\n")
        self.store.record_reads(analysis.datasets_read(doc, self.data_dir), analysis.effective_cutoff(doc, self.data_dir), "analysis", analysis_id, family)
        created = self.store.create_analysis(analysis_id, doc.name, method.key, doc.project)
        self._wake.set()
        return created

    def cancel(self, job_id: str, kind: str = "run") -> None:
        with self._lock:
            status = self.store.get_job(kind, job_id)["status"]
            if status == "queued":
                self._publish(kind, job_id)
                self.store.update_job(kind, job_id, status="cancelled", error="Cancelled before it started.", finished_at=now())
            elif status == "running" and self._current == job_id:
                self._cancel_requested.add(job_id)
                if self._process:  # otherwise the worker is starting and is terminated once it exists
                    self._process.terminate()
            else:
                raise NotCancellable(f"{kind.capitalize()} {job_id} is {status} and cannot be cancelled.")

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def job_dir(self, kind: str, job_id: str) -> Path:
        """Where a job is now: published, or still in staging while queued or running."""
        final = self.roots[kind] / job_id
        return final if final.exists() else self._staging(kind) / job_id

    def _staging(self, kind: str) -> Path:
        return self.roots[kind] / ".staging"

    def frozen(self, run_id: str) -> tuple[StrategyDocument, date | None]:
        """The strategy and cutoff a run was queued with, wherever it is in its life."""
        return analysis.frozen_strategy(self.data_dir, run_id), frozen_until(analysis.frozen_folder(self.data_dir, run_id))

    def _publish(self, kind: str, job_id: str) -> Path:
        final = self.roots[kind] / job_id
        os.rename(self._staging(kind) / job_id, final)
        return final

    def _loop(self) -> None:
        while not self._stopping:
            with self._lock:
                job = self.store.next_queued()
                if job:
                    self.store.update_job(*job, status="running", started_at=now())
                    self._current = job[1]
            if job:
                try:
                    self._execute(*job)
                except Exception as e:  # keep serving the queue; the job records what went wrong
                    with self._lock:
                        self._process, self._current = None, None
                    self.store.update_job(*job, status="failed", error=f"Service error: {e}", finished_at=now())
            else:
                self._wake.wait(timeout=1)
                self._wake.clear()

    def _execute(self, kind: str, job_id: str) -> None:
        staging = self._staging(kind) / job_id
        with open(staging / "log.txt", "wb") as log:
            # The worker's stdin stays open for as long as this service lives (see qrs.worker).
            command = [sys.executable, "-m", "qrs.worker", str(staging), str(self.data_dir)]
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
            with self._lock:
                self._process = process
                if job_id in self._cancel_requested:
                    process.terminate()
            try:
                exit_code, timed_out = process.wait(timeout=self.time_limit), False
            except subprocess.TimeoutExpired:
                exit_code, timed_out = _stop(process), True
            process.stdin.close()
        with self._lock:
            self._process, self._current = None, None
            cancelled = job_id in self._cancel_requested
            self._cancel_requested.discard(job_id)
            final = self._publish(kind, job_id)
            problem = verify_outputs(final) if exit_code == 0 else None

            def finish(status: str, **fields) -> None:
                self.store.update_job(kind, job_id, status=status, finished_at=now(), **fields)

            if cancelled:
                finish("cancelled", error="Cancelled while running.")
            elif self._stopping:
                finish("interrupted", error=f"The service stopped while this {kind} was in progress.")
            elif timed_out:
                finish("failed", error=f"Stopped after the time limit of {_duration(self.time_limit)}.")
            elif exit_code == 0 and problem is None:
                finish("succeeded", result_sha256=json.loads((final / "record.json").read_text())["result_sha256"])
            else:
                error_file = final / "error.txt"
                if error_file.exists():
                    error = error_file.read_text()
                elif problem:
                    error = problem
                elif exit_code < 0:
                    error = f"The worker was terminated by signal {-exit_code}."
                else:
                    error = f"The worker exited with status {exit_code}; see the log."
                finish("failed", error=error)


def _stop(process: subprocess.Popen) -> int:
    """Terminate a worker, and kill it if it does not exit soon: code that handles SIGTERM must not
    hold the queue past the time limit."""
    process.terminate()
    try:
        return process.wait(timeout=KILL_AFTER)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait()


def _duration(seconds: float) -> str:
    return f"{seconds / 60:g} minutes" if seconds >= 60 else f"{seconds:g} seconds"
