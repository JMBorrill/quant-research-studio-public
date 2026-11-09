"""Runs a compiled strategy module and writes its reproducible run record. The studio worker, the
CLI and exported bundles all call execute(), so every route shares one execution path."""

import dataclasses
import hashlib
import importlib.util
import json
import platform
from datetime import date
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pandas as pd

from qrs import __version__, datasets, registry
from qrs.hashing import canonical_json, sha256_file, sha256_hex
from qrs.ports import PORT_TYPES, BacktestResult, Bars, Label, Models

RUNTIME_PACKAGES = ("nautilus_trader", "numpy", "pandas", "pandas_market_calendars", "pyarrow", "pydantic")
PACKAGE_DIR = Path(__file__).parent


class RunError(RuntimeError):
    pass


class Run:
    """The context passed to every operator: dataset access, and a record of what was used. It is the
    only route to data, so a cutoff set here (`until`) holds for every read: no bar on or after it
    is returned, and no observation published after the close of the last session before it."""

    def __init__(self, data_dir: Path, until: date | None = None):
        self.data_dir = data_dir
        self.until = until
        self.datasets: dict[str, datasets.Manifest] = {}
        self.operators: dict[str, registry.Operator] = {}
        self.results: dict[str, BacktestResult] = {}  # node ID -> backtest output
        self.models: dict[str, Models] = {}  # node ID -> fitted models
        self.records: dict[str, pd.DataFrame] = {}  # node ID -> table kept by a record node
        self._node: str | None = None  # the node running now

    def dataset(self, dataset_id: str) -> Bars:
        bars = datasets.load(self.data_dir, dataset_id)
        self.datasets[dataset_id] = datasets.read_manifest(self.data_dir, dataset_id)
        if self.until is not None:
            bars = dataclasses.replace(bars, frame=bars.frame[bars.frame.index < pd.Timestamp(self.until)])
        return bars

    def observations(self, dataset_id: str, bars: Bars) -> pd.DataFrame:
        """An observations dataset's rows published by the close of the bars' last session. No
        session of these bars may use a later one, and bars read through this run stop before
        `until`, so nothing published after the close of the last session before it is returned."""
        rows = datasets.load_observations(self.data_dir, dataset_id)
        self.datasets[dataset_id] = datasets.read_manifest(self.data_dir, dataset_id)
        return rows[rows["published"] <= datasets.session_closes(bars).iloc[-1]]

    def op(self, node: str, operator_id: str, version: int, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        """Run one operator, rejecting inputs and outputs that do not match its declared ports."""
        op = registry.get(operator_id, version)
        try:
            _check_ports(op.inputs, inputs, "input")
            _check_alignment(_labelled(inputs, "input"))
            self._node = node
            outputs = op.run(self, inputs, op.params.model_validate(params))
            _check_ports(op.outputs, outputs, "output")
            _check_alignment(_labelled(inputs, "input") | _labelled(outputs, "output"))
        except Exception as e:
            raise RunError(f"Node '{node}' ({op.key}): {e}") from e
        self.operators[op.key] = op
        for value in outputs.values():
            if isinstance(value, BacktestResult):
                self.results[node] = value
            elif isinstance(value, Models):
                self.models[node] = value
        return outputs

    def keep(self, table: pd.DataFrame) -> None:
        """Save a table with the run, under the node running now (qrs.record)."""
        self.records[self._node] = table


def _check_ports(declared: dict[str, str], values: dict[str, Any], kind: str) -> None:
    if set(values) != set(declared):
        raise TypeError(f"{kind}s {sorted(values)} do not match the declared {kind}s {sorted(declared)}")
    for name, port_type in declared.items():
        if not PORT_TYPES[port_type].check(values[name]):
            raise TypeError(f"{kind} '{name}' is not a valid {port_type} value")


def _labelled(values: dict[str, Any], kind: str) -> dict[str, Any]:
    return {f"{kind} '{name}'": value for name, value in values.items()}


def _session_index(value: Any) -> pd.Index | None:
    if isinstance(value, Bars):
        return value.frame.index
    if isinstance(value, pd.Series | pd.DataFrame):
        return value.index
    if isinstance(value, Label):
        return value.values.index
    if isinstance(value, Models):
        return value.sessions
    return None  # a backtest result has no session index of its own


def _check_alignment(values: dict[str, Any]) -> None:
    """For both alignments, all of an operator's inputs and outputs that carry a session index must
    share one. An "as_of" operator reads its observations through the run context, not a port."""
    indexes = {name: index for name, v in values.items() if (index := _session_index(v)) is not None}
    first = next(iter(indexes.values()), None)
    misaligned = [name for name, index in indexes.items() if not index.equals(first)]
    if misaligned:
        raise ValueError(f"{', '.join(misaligned)} must share the session index of {next(iter(indexes))}.")


def source_files(package: Path = PACKAGE_DIR) -> list[Path]:
    return sorted(p for p in package.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def runtime_identity() -> dict[str, Any]:
    digest = hashlib.sha256()
    for path in source_files():
        digest.update(path.relative_to(PACKAGE_DIR).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return {
        "qrs": __version__,
        "qrs_source_sha256": digest.hexdigest(),
        "python": platform.python_version(),
        "lock_sha256": sha256_file(PACKAGE_DIR / "runtime.lock"),
        "packages": {name: version(name) for name in RUNTIME_PACKAGES},
    }


def evidence_class(record: dict[str, Any]) -> str:
    """A run's evidence class: the weakest among the datasets it read (Stage 4 decision 10), so a run
    that read retrospective data is never shown as point-in-time."""
    return datasets.weakest(d["evidence_class"] for d in record["datasets"].values())


def backtest_nodes(record: dict[str, Any]) -> list[str]:
    """The nodes whose backtest results a run record lists, under results/<node>/."""
    return sorted({path.split("/")[1] for path in record["outputs"] if path.startswith("results/")})


def verify_outputs(run_dir: Path) -> str | None:
    """Why a run or analysis directory's outputs do not match its record.json, or None if they do."""
    record_path = run_dir / "record.json"
    if not record_path.exists():
        return "The worker finished without writing record.json."
    for path, digest in json.loads(record_path.read_text())["outputs"].items():
        if not (run_dir / path).is_file() or sha256_file(run_dir / path) != digest:
            return f"Output {path} does not match the hash in record.json."
    return None


def frozen_until(run_dir: Path) -> date | None:
    """The cutoff a queued run was frozen with (its until.txt), if any."""
    until = run_dir / "until.txt"
    return date.fromisoformat(until.read_text()) if until.exists() else None


def execute(strategy_file: Path, data_dir: Path, out_dir: Path, until: date | None = None) -> dict[str, Any]:
    """Execute a compiled strategy file, reading no data on or after `until` if it is given; write
    results and record.json into out_dir."""
    if (out_dir / "results").exists():
        raise RunError(f"{out_dir} already holds results; choose a new output directory.")
    spec = importlib.util.spec_from_file_location("qrs_compiled_strategy", strategy_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = Run(data_dir, until)
    module.run(ctx)
    if not ctx.results:
        raise RunError("The strategy produced no backtest result.")

    outputs = {}
    for node, result in sorted(ctx.results.items()):
        folder = out_dir / "results" / node
        folder.mkdir(parents=True)
        result.fills.to_csv(folder / "fills.csv", index=False)
        result.equity.to_csv(folder / "equity.csv")
        (folder / "summary.json").write_text(json.dumps({"metrics": result.metrics, "assumptions": result.assumptions}, indent=2, sort_keys=True) + "\n")
        for f in sorted(folder.iterdir()):
            outputs[f.relative_to(out_dir).as_posix()] = sha256_file(f)
    for node, models in sorted(ctx.models.items()):
        # Every fitted model with its training provenance, so the record's result hash covers them.
        path = out_dir / "models" / f"{node}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps([dataclasses.asdict(m) for m in models.fitted], default=lambda t: t.date().isoformat(), indent=2) + "\n")
        outputs[path.relative_to(out_dir).as_posix()] = sha256_file(path)
    for node, table in sorted(ctx.records.items()):
        # Written only by record nodes, so runs without them keep their outputs and result hash.
        path = out_dir / "records" / f"{node}.parquet"
        path.parent.mkdir(exist_ok=True)
        table.to_parquet(path)
        outputs[path.relative_to(out_dir).as_posix()] = sha256_file(path)

    record = {
        "kind": "qrs/run-record",
        "version": 1,
        "code_sha256": sha256_file(strategy_file),
        "runtime": runtime_identity(),
        "operators": {key: {"module": op.module, "source_sha256": sha256_file(Path(op.source_file()))} for key, op in sorted(ctx.operators.items())},
        "datasets": {i: {"name": m.name, "data_sha256": m.data_sha256, "evidence_class": m.evidence_class} for i, m in sorted(ctx.datasets.items())},
        "outputs": outputs,
        "result_sha256": sha256_hex(canonical_json(outputs)),
    }
    if until is not None:  # recorded only when set, so records of runs without one are unchanged
        record["until"] = until.isoformat()
    (out_dir / "record.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record
