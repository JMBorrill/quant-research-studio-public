"""SQLite index of strategy revisions, runs, experiments, analyses, research projects and the read
history. The service writes it; `qrs demo` and
`qrs experiment` add strategies and queued trials, which the service then runs.

Strategy revisions are append-only: a save names the revision it was based on and fails if another
save got there first, so a stale editor can never overwrite newer work."""

import json
import sqlite3
import threading
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from qrs import graph, registry
from qrs.contracts import AnalysisDocument, StrategyDocument

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_revisions (
    strategy_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    document TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    PRIMARY KEY (strategy_id, revision)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    result_sha256 TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    predecessor TEXT
);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    document TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cancelled_at TEXT
);
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    method TEXT NOT NULL,
    status TEXT NOT NULL,
    result_sha256 TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    document TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_notes (
    project_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    text TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    PRIMARY KEY (project_id, revision)
);
CREATE TABLE IF NOT EXISTS families (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    document TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    confirmatory INTEGER NOT NULL,
    reads_seen INTEGER NOT NULL,
    registered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS withdrawals (
    family_id TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    reason TEXT NOT NULL,
    withdrawn_at TEXT NOT NULL,
    PRIMARY KEY (family_id, hypothesis)
);
CREATE TABLE IF NOT EXISTS evaluations (
    family_id TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    analysis_id TEXT,
    holdout_run TEXT,
    error TEXT,
    requested_at TEXT NOT NULL,
    PRIMARY KEY (family_id, hypothesis)
);
CREATE TABLE IF NOT EXISTS holdout_evaluations (
    run_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    trial INTEGER NOT NULL,
    requested_at TEXT NOT NULL,
    repeat INTEGER NOT NULL
);
"""
# Columns added to runs after its first release: (name, type). Older stores gain them when opened.
RUN_COLUMNS = [("predecessor", "TEXT"), ("experiment", "TEXT"), ("trial", "INTEGER")]
ANALYSIS_COLUMNS = [("project", "TEXT")]
# An experiment's holdout window (the bars its backtest trades and the holdout start) and whether it
# is exploratory (Phase 4B). Older stores gain them when opened, filled from each experiment's
# document and base revision; their experiments were verified.
EXPERIMENT_COLUMNS = [("bars", "TEXT"), ("holdout_start", "TEXT"), ("exploratory", "INTEGER")]
# The evaluations of experiment ? 's holdout window: its own, and every other experiment's on the
# same bars and holdout start. One whose bars are not known (NULL) shares its window with none.
SAME_WINDOW = "JOIN experiments x ON x.id = ? WHERE e.id = x.id OR (e.bars, e.holdout_start) = (x.bars, x.holdout_start)"
# The read history (Stage 5 amendment A2): one row per dataset a run, trial, holdout evaluation,
# analysis or export may read, and its cutoff (NULL: everything). Written when the read is queued or
# made, so a failed or cancelled one still counts.
READS = """
CREATE TABLE IF NOT EXISTS reads (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset TEXT NOT NULL,
    until TEXT,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    family TEXT,
    recorded_at TEXT NOT NULL
)
"""
# Rows of these tables are inserted, never changed or removed (amendment A2).
APPEND_ONLY = ("projects", "project_notes", "families", "withdrawals", "evaluations", "reads")
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
JOB_TABLES = {"run": "runs", "analysis": "analyses"}  # job kind -> its table; both share the status columns


class NotFound(KeyError):
    pass


class StaleRevision(Exception):
    def __init__(self, current: int):
        self.current = current
        super().__init__(f"The strategy was saved elsewhere (now at revision {current}); reload it before saving.")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        # So INSERT OR REPLACE, which deletes the row it replaces, meets the append-only triggers too.
        self._db.execute("PRAGMA recursive_triggers = ON")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self.home = path.parent
        for table, columns in (("runs", RUN_COLUMNS), ("experiments", EXPERIMENT_COLUMNS), ("analyses", ANALYSIS_COLUMNS)):
            present = {column["name"] for column in self._query(f"PRAGMA table_info({table})")}
            for name, kind in columns:
                if name not in present:
                    self._query(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
        if not self._has_reads():
            self._create_reads(self._backfilled_reads())
        for table in APPEND_ONLY:
            for change in ("UPDATE", "DELETE"):
                self._query(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{change.lower()} BEFORE {change} ON {table}"
                    f" BEGIN SELECT RAISE(ABORT, '{table} are append-only: rows are never changed or removed'); END"
                )
        for row in self._query("SELECT id, document FROM experiments WHERE holdout_start IS NULL"):
            experiment = json.loads(row["document"])
            try:
                _, base = self.get_strategy(experiment["strategy_id"], experiment["revision"])
            except NotFound:
                continue
            bars = graph.traded_bars(base)
            if bars is None:  # drawn through an operator not loaded now: left for an open that loads it
                continue
            self._query("UPDATE experiments SET bars = ?, holdout_start = ?, exploratory = 0 WHERE id = ?", bars, experiment["holdout_start"], row["id"])

    def _query(self, sql: str, *args) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # Strategies

    def create_strategy(self, doc: StrategyDocument) -> tuple[str, int]:
        strategy_id = uuid.uuid4().hex[:12]
        self._query("INSERT INTO strategy_revisions VALUES (?, 1, ?, ?)", strategy_id, doc.model_dump_json(), now())
        return strategy_id, 1

    def save_strategy(self, strategy_id: str, base_revision: int, doc: StrategyDocument) -> int:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT MAX(revision) FROM strategy_revisions WHERE strategy_id = ?", (strategy_id,)).fetchone()
                current = row[0]
                if current is None:
                    raise NotFound(strategy_id)
                if current != base_revision:
                    raise StaleRevision(current)
                self._db.execute("INSERT INTO strategy_revisions VALUES (?, ?, ?, ?)", (strategy_id, current + 1, doc.model_dump_json(), now()))
                self._db.execute("COMMIT")
                return current + 1
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def get_strategy(self, strategy_id: str, revision: int | None = None) -> tuple[int, StrategyDocument]:
        if revision is None:
            rows = self._query("SELECT * FROM strategy_revisions WHERE strategy_id = ? ORDER BY revision DESC LIMIT 1", strategy_id)
        else:
            rows = self._query("SELECT * FROM strategy_revisions WHERE strategy_id = ? AND revision = ?", strategy_id, revision)
        if not rows:
            raise NotFound(strategy_id)
        return rows[0]["revision"], StrategyDocument.model_validate_json(rows[0]["document"])

    def list_strategies(self) -> list[dict]:
        rows = self._query(
            "SELECT r.* FROM strategy_revisions r JOIN (SELECT strategy_id, MAX(revision) AS revision FROM strategy_revisions GROUP BY strategy_id) l"
            " USING (strategy_id, revision) ORDER BY r.saved_at DESC"
        )
        return [{"id": r["strategy_id"], "revision": r["revision"], "name": json.loads(r["document"])["name"], "saved_at": r["saved_at"]} for r in rows]

    # Runs

    def create_run(
        self, run_id: str, strategy_id: str, revision: int, code_sha256: str, predecessor: str | None = None, trial: tuple[str, int] | None = None
    ) -> dict:
        """Queue a run; `trial` is (experiment ID, trial number) for an experiment's trial."""
        experiment, number = trial or (None, None)
        self._query(
            "INSERT INTO runs (id, strategy_id, revision, status, code_sha256, created_at, predecessor, experiment, trial)"
            " VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
            run_id,
            strategy_id,
            revision,
            code_sha256,
            now(),
            predecessor,
            experiment,
            number,
        )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict:
        rows = self._query("SELECT * FROM runs WHERE id = ?", run_id)
        if not rows:
            raise NotFound(run_id)
        return rows[0]

    def run_status(self, run_id: str) -> str | None:
        rows = self._query("SELECT status FROM runs WHERE id = ?", run_id)
        return rows[0]["status"] if rows else None

    def list_runs(self, strategy_id: str | None = None) -> list[dict]:
        if strategy_id:
            return self._query("SELECT * FROM runs WHERE strategy_id = ? ORDER BY created_at DESC", strategy_id)
        return self._query("SELECT * FROM runs ORDER BY created_at DESC")

    def trial_runs(self, experiment_id: str) -> list[dict]:
        return self._query("SELECT * FROM runs WHERE experiment = ? ORDER BY trial", experiment_id)

    def update_run(self, run_id: str, **fields) -> None:
        self.update_job("run", run_id, **fields)

    def update_job(self, kind: str, job_id: str, **fields) -> None:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self._query(f"UPDATE {JOB_TABLES[kind]} SET {assignments} WHERE id = ?", *fields.values(), job_id)

    def get_job(self, kind: str, job_id: str) -> dict:
        rows = self._query(f"SELECT * FROM {JOB_TABLES[kind]} WHERE id = ?", job_id)
        if not rows:
            raise NotFound(job_id)
        return rows[0]

    def next_queued(self) -> tuple[str, str] | None:
        """The job queued first, run or analysis, as (kind, ID). Of jobs queued in the same
        millisecond, runs go first, since an analysis may read a run queued with it (a hypothesis's
        holdout evaluation) and a run never reads an analysis; then jobs of one kind go in the order
        they were inserted."""
        rows = self._query(
            "SELECT 'run' AS kind, id, created_at, rowid AS inserted FROM runs WHERE status = 'queued'"
            " UNION ALL SELECT 'analysis', id, created_at, rowid FROM analyses WHERE status = 'queued'"
            " ORDER BY created_at, kind DESC, inserted LIMIT 1"  # 'run' after 'analysis', so DESC puts runs first
        )
        return (rows[0]["kind"], rows[0]["id"]) if rows else None

    def mark_interrupted(self) -> None:
        """Called at startup: a job still marked running belonged to a service that stopped."""
        for kind, table in JOB_TABLES.items():
            error = f"The service stopped while this {kind} was in progress."
            self._query(f"UPDATE {table} SET status = 'interrupted', error = ?, finished_at = ? WHERE status = 'running'", error, now())

    # Experiments

    def create_experiment(self, experiment_id: str, document: str, bars: str | None, holdout_start: str, exploratory: bool) -> None:
        self._query(
            "INSERT INTO experiments (id, document, created_at, bars, holdout_start, exploratory) VALUES (?, ?, ?, ?, ?, ?)",
            experiment_id,
            document,
            now(),
            bars,
            holdout_start,
            int(exploratory),
        )

    def get_experiment(self, experiment_id: str) -> dict:
        rows = self._query("SELECT * FROM experiments WHERE id = ?", experiment_id)
        if not rows:
            raise NotFound(experiment_id)
        return rows[0]

    def list_experiments(self) -> list[dict]:
        return self._query("SELECT * FROM experiments ORDER BY created_at DESC")

    def cancel_experiment(self, experiment_id: str) -> None:
        self._query("UPDATE experiments SET cancelled_at = COALESCE(cancelled_at, ?) WHERE id = ?", now(), experiment_id)

    def record_holdout_evaluation(self, run_id: str, experiment_id: str, trial: int) -> bool:
        """Record a holdout evaluation; True if its holdout window (the same bars and holdout start)
        was evaluated before, in this or any experiment, verified or exploratory, in which case this
        one is a repeat."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                earlier = f"SELECT 1 FROM holdout_evaluations h JOIN experiments e ON h.experiment_id = e.id {SAME_WINDOW}"
                repeat = self._db.execute(earlier, (experiment_id,)).fetchone() is not None
                self._db.execute("INSERT INTO holdout_evaluations VALUES (?, ?, ?, ?, ?)", (run_id, experiment_id, trial, now(), int(repeat)))
                self._db.execute("COMMIT")
                return repeat
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def window_experiments(self, experiment_id: str) -> list[dict]:
        """Every experiment on the experiment's holdout window (the same bars and holdout start), itself included."""
        return self._query(f"SELECT e.* FROM experiments e {SAME_WINDOW} ORDER BY e.created_at", experiment_id)

    def holdout_evaluation(self, run_id: str) -> dict | None:
        rows = self._query("SELECT * FROM holdout_evaluations WHERE run_id = ?", run_id)
        return rows[0] if rows else None

    def evaluated_holdout(self, run_id: str) -> bool:
        return bool(self._query("SELECT 1 FROM holdout_evaluations WHERE run_id = ?", run_id))

    def window_evaluations(self, experiment_id: str) -> list[dict]:
        """Every holdout evaluation of the experiment's window, its own and other experiments', in the
        order they were recorded, with whether its experiment is exploratory."""
        return self._query(
            f"SELECT h.*, e.exploratory FROM holdout_evaluations h JOIN experiments e ON h.experiment_id = e.id {SAME_WINDOW} ORDER BY h.rowid",
            experiment_id,
        )

    # Analyses

    def create_analysis(self, analysis_id: str, name: str, method: str, project: str | None = None) -> dict:
        self._query(
            "INSERT INTO analyses (id, name, method, status, created_at, project) VALUES (?, ?, ?, 'queued', ?, ?)", analysis_id, name, method, now(), project
        )
        return self.get_analysis(analysis_id)

    def get_analysis(self, analysis_id: str) -> dict:
        return self.get_job("analysis", analysis_id)

    def list_analyses(self) -> list[dict]:
        return self._query("SELECT * FROM analyses ORDER BY created_at DESC")

    # The read history (Stage 5 amendment A2)

    def record_reads(self, dataset_ids, until: date | None, kind: str, source: str, family: str | None = None) -> None:
        cutoff = until.isoformat() if until else None
        for dataset_id in sorted(set(dataset_ids)):
            self._query(
                "INSERT INTO reads (dataset, until, kind, source, family, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                dataset_id,
                cutoff,
                kind,
                source,
                family,
                now(),
            )

    def reads(self, dataset_ids, after: int = 0, upto: int | None = None) -> list[dict]:
        """The reads of these datasets recorded after sequence number `after`, and to `upto` if given,
        oldest first."""
        ids = sorted(set(dataset_ids))
        marks = ", ".join("?" * len(ids))
        last = self.last_read() if upto is None else upto
        return self._query(f"SELECT * FROM reads WHERE seq > ? AND seq <= ? AND dataset IN ({marks}) ORDER BY seq", after, last, *ids)

    def last_read(self) -> int:
        return self._query("SELECT COALESCE(MAX(seq), 0) AS seq FROM reads")[0]["seq"]

    def _has_reads(self) -> bool:
        return bool(self._query("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reads'"))

    def _create_reads(self, rows: list[tuple]) -> None:
        """Create the read history with its backfilled rows in one transaction, so a failure leaves no
        table, and the next open backfills again; a store another process upgraded meanwhile is kept."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                if not self._db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reads'").fetchone():
                    self._db.execute(READS)
                    stamp = now()
                    self._db.executemany("INSERT INTO reads (dataset, until, kind, source, recorded_at) VALUES (?, ?, ?, ?, ?)", [(*r, stamp) for r in rows])
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def _backfilled_reads(self) -> list[tuple]:
        """The reads of a store from before the read history, as (dataset, until, kind, source): each
        run's from its frozen strategy and cutoff, each analysis's from its document, and each
        extraction's, a full read of its documents, from the features it made. A job whose
        datasets cannot be told (its directory is gone, a file there does not parse, or it names an
        operator not loaded now) is an unknown read: of every dataset present, in full, or for a trial
        to its experiment's holdout start, the most a trial reads."""
        from qrs import analysis, datasets  # the store opens without them otherwise
        from qrs.runtime import frozen_until

        present = datasets.list_manifests(self.home)
        holdouts = {row["run_id"] for row in self._query("SELECT run_id FROM holdout_evaluations")}
        rows = []

        def add(dataset_ids, until: date | None, kind: str, source: str) -> None:
            rows.extend((d, until.isoformat() if until else None, kind, source) for d in sorted(set(dataset_ids)))

        for run in self._query("SELECT r.*, e.holdout_start FROM runs r LEFT JOIN experiments e ON r.experiment = e.id ORDER BY r.created_at"):
            kind = "holdout" if run["id"] in holdouts else "trial" if run["experiment"] else "run"
            try:
                doc = analysis.frozen_strategy(self.home, run["id"])
                if any(registry.find(n.operator, n.version) is None for n in doc.nodes):
                    raise LookupError(run["id"])
                add(graph.dataset_ids(doc), frozen_until(analysis.frozen_folder(self.home, run["id"])), kind, run["id"])
            except (LookupError, OSError, ValueError):
                until = date.fromisoformat(run["holdout_start"]) if kind == "trial" and run["holdout_start"] else None
                add(present, until, f"unknown {kind}", run["id"])
        for row in self._query("SELECT id FROM analyses ORDER BY created_at"):
            try:
                doc = AnalysisDocument.model_validate_json((self.home / "analyses" / row["id"] / "analysis.json").read_text())
                add(analysis.datasets_read(doc, self.home), doc.until, "analysis", row["id"])
            except (OSError, ValueError):
                add(present, None, "unknown analysis", row["id"])
        for dataset_id, manifest in present.items():
            derivation = getattr(manifest, "derivation", None)
            if derivation:
                add([derivation.documents], None, "extract", dataset_id)
        return rows

    # Projects and families (Stage 5)

    def create_project(self, document: str) -> str:
        project_id = uuid.uuid4().hex[:12]
        self._query("INSERT INTO projects VALUES (?, ?, ?)", project_id, document, now())
        return project_id

    def get_project(self, project_id: str) -> dict:
        rows = self._query("SELECT * FROM projects WHERE id = ?", project_id)
        if not rows:
            raise NotFound(project_id)
        return rows[0]

    def list_projects(self) -> list[dict]:
        return self._query("SELECT * FROM projects ORDER BY created_at DESC")

    def add_note(self, project_id: str, text: str) -> int:
        """Save a new revision of the project's notes; earlier revisions are kept."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                revision = self._db.execute("SELECT COALESCE(MAX(revision), 0) + 1 FROM project_notes WHERE project_id = ?", (project_id,)).fetchone()[0]
                self._db.execute("INSERT INTO project_notes VALUES (?, ?, ?, ?)", (project_id, revision, text, now()))
                self._db.execute("COMMIT")
                return revision
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def notes(self, project_id: str) -> list[dict]:
        return self._query("SELECT * FROM project_notes WHERE project_id = ? ORDER BY revision", project_id)

    def register_family(self, project_id: str, document: str, sha256: str, confirmatory: bool, reads_seen: int) -> str:
        family_id = uuid.uuid4().hex[:12]
        self._query("INSERT INTO families VALUES (?, ?, ?, ?, ?, ?, ?)", family_id, project_id, document, sha256, int(confirmatory), reads_seen, now())
        return family_id

    def get_family(self, family_id: str) -> dict:
        rows = self._query("SELECT * FROM families WHERE id = ?", family_id)
        if not rows:
            raise NotFound(family_id)
        return rows[0]

    def families(self, project_id: str) -> list[dict]:
        return self._query("SELECT * FROM families WHERE project_id = ? ORDER BY registered_at", project_id)

    def withdraw(self, family_id: str, hypothesis: str, reason: str) -> None:
        self._query("INSERT INTO withdrawals VALUES (?, ?, ?, ?)", family_id, hypothesis, reason, now())

    def withdrawals(self, family_id: str) -> list[dict]:
        return self._query("SELECT * FROM withdrawals WHERE family_id = ? ORDER BY withdrawn_at", family_id)

    def record_evaluation(self, family_id: str, hypothesis: str, analysis_id: str | None, holdout_run: str | None, error: str | None) -> None:
        """Bind a hypothesis to its one evaluation, or to why it could not be made; a second raises."""
        self._query("INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?)", family_id, hypothesis, analysis_id, holdout_run, error, now())

    def evaluations(self, family_id: str) -> list[dict]:
        return self._query("SELECT * FROM evaluations WHERE family_id = ? ORDER BY requested_at", family_id)

    def project_analyses(self, project_id: str) -> list[dict]:
        return self._query("SELECT * FROM analyses WHERE project = ? ORDER BY created_at", project_id)
