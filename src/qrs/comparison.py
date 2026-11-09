"""Run comparison. Two or more succeeded runs on the same dataset are compared over a common window
that starts at the latest effective warm-up among them, so no run is judged on sessions where
another could not yet trade. Each run's metrics are recomputed from its own ledger (fills and
equity) sliced to that window, with the equity at the window's first session as the baseline.
Differing costs, execution assumptions, runtime or operator code are listed as mismatches, never
compared silently. Everything is read from the run directories, which freeze what each run used."""

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from qrs import registry
from qrs.analytics import metrics
from qrs.contracts import Node, RunInfo, StrategyDocument
from qrs.runtime import backtest_nodes, evidence_class


class ComparisonError(ValueError):
    pass


@dataclass
class Ledger:
    """What one run's backtest produced, and what it was produced with."""

    info: RunInfo
    node: str
    strategy: StrategyDocument
    record: dict
    warm_up: int
    fills: pd.DataFrame
    equity: pd.DataFrame
    assumptions: dict


def load(info: RunInfo, folder: Path) -> Ledger:
    if info.status != "succeeded":
        raise ComparisonError(f"Run {info.id} is {info.status}; only succeeded runs can be compared.")
    record = json.loads((folder / "record.json").read_text())
    nodes = backtest_nodes(record)
    if len(nodes) != 1:
        raise ComparisonError(f"Run {info.id} has {len(nodes)} backtests; only runs with one backtest can be compared.")
    warm_up_file = folder / "warm_up.json"
    if not warm_up_file.exists():
        raise ComparisonError(f"Run {info.id} predates recorded warm-ups; re-run it to compare it.")
    results = folder / "results" / nodes[0]
    return Ledger(
        info=info,
        node=nodes[0],
        strategy=StrategyDocument.model_validate_json((folder / "strategy.json").read_text()),
        record=record,
        warm_up=json.loads(warm_up_file.read_text())[nodes[0]],
        fills=pd.read_csv(results / "fills.csv", parse_dates=["session"]),
        equity=pd.read_csv(results / "equity.csv", index_col="session", parse_dates=True),
        assumptions=json.loads((results / "summary.json").read_text())["assumptions"],
    )


def compare(runs: list[tuple[RunInfo, Path]]) -> dict:
    """Compare runs, each given with its run directory, or raise ComparisonError saying why not."""
    ids = [info.id for info, _ in runs]
    if len(ids) < 2 or len(set(ids)) < len(ids):
        raise ComparisonError("Choose at least two different runs to compare.")
    ledgers = [load(info, folder) for info, folder in runs]
    return {**common_window(ledgers), "parameters": parameter_differences([r.strategy for r in ledgers]), "mismatches": mismatches(ledgers)}


def common_window(runs: list[Ledger]) -> dict:
    """The window from the latest effective warm-up among the runs, the first session on which every
    run can have a signal, and each run's metrics over it. The runs must have traded one dataset
    over the same sessions."""
    check_same_dataset(runs)
    check_same_sessions(runs)
    start = max(r.warm_up for r in runs)
    return {"window": window(runs[0].equity.index, start), "runs": [in_window(r, start) for r in runs]}


def window(sessions: pd.DatetimeIndex, start: int) -> dict:
    return {"start": sessions[start].date().isoformat(), "end": sessions[-1].date().isoformat(), "sessions": len(sessions) - start}


def check_same_dataset(runs: list[Ledger]) -> None:
    """Every backtest must have traded the same dataset. A strategy may read other datasets too, for
    signals, so what matters is the dataset feeding each backtest's bars."""
    traded = [traded_dataset(r) for r in runs]
    if len(set(traded)) > 1:
        names = "; ".join(f"{r.info.id}: {r.record['datasets'][d]['name']} ({d[7:19]})" for r, d in zip(runs, traded, strict=True))
        raise ComparisonError(f"Only runs on the same dataset can be compared ({names}).")


def check_same_sessions(runs: list[Ledger]) -> None:
    """A run cut off at a date (an experiment's trial, at its holdout start) ends earlier than one
    that is not, so no single window covers both: a full run's metrics would include the sessions
    the other never read, the holdout among them."""
    ends = [r.equity.index[-1] for r in runs]
    if len(set(ends)) > 1:
        names = "; ".join(f"{r.info.id}: to {end.date()}" for r, end in zip(runs, ends, strict=True))
        raise ComparisonError(f"Only runs over the same sessions can be compared; a cutoff ends a run early ({names}).")


def traded_dataset(run: Ledger) -> str:
    """The dataset ID of the node feeding the backtest's bars, from the frozen strategy."""
    nodes = {n.id: n for n in run.strategy.nodes}
    bars = next(e.source.node for e in run.strategy.edges if e.target.node == run.node and e.target.port == "bars")
    op = registry.find(nodes[bars].operator, nodes[bars].version)
    if op is None or not op.dataset_params():
        raise ComparisonError(f"Run {run.info.id}: cannot tell which dataset its backtest traded.")
    return nodes[bars].params[op.dataset_params()[0]]


def in_window(run: Ledger, start: int) -> dict:
    """The run's metrics over the sessions from `start`. Fills on the first session were decided
    before the window and are already in its baseline equity, so only later fills count."""
    equity = run.equity.iloc[start:]
    return measured(run, start, run.fills[run.fills["session"] > equity.index[0]], equity, opening=equity.iloc[0])


def from_close_before(run: Ledger, start: int) -> dict:
    """The run's metrics over the sessions from `start`, measured from the close before it: that
    close's equity is the baseline and its position is carried in, so the first session's price
    change, fills and costs all count. The holdout is measured this way."""
    equity = run.equity.iloc[start:]
    return measured(run, start, run.fills[run.fills["session"] >= equity.index[0]], equity, opening=run.equity.iloc[start - 1])


def measured(run: Ledger, start: int, fills: pd.DataFrame, equity: pd.DataFrame, opening: pd.Series) -> dict:
    """The run's metrics over `equity`, from the equity and position of the `opening` session. Its
    equity is the baseline for drawdowns too: no change for a window whose first close it is, and
    a drawdown from the first session for one measured from the close before (the holdout)."""
    baseline = float(opening["equity"])
    return {
        "id": run.info.id,
        "strategy_id": run.info.strategy_id,
        "revision": run.info.revision,
        "name": run.strategy.name,
        "evidence_class": evidence_class(run.record),
        "node": run.node,
        "warm_up": run.warm_up,
        "excluded_sessions": start,
        "position_at_start": int(opening["position"]),
        "metrics": metrics(fills, equity, baseline, drawdown_from_initial=True),
        "equity_change": (equity["equity"] - baseline).tolist(),
    }


def parameter_differences(strategies: list[StrategyDocument]) -> list[dict]:
    """Each node's operator or parameter that differs between the strategies, defaults filled in."""
    by_id = [{n.id: n for n in s.nodes} for s in strategies]
    rows = []
    for node_id in dict.fromkeys(n.id for s in strategies for n in s.nodes):
        nodes = [nodes.get(node_id) for nodes in by_id]
        operators = [f"{n.operator}@{n.version}" if n else None for n in nodes]
        if len(set(operators)) > 1:
            rows.append({"node": node_id, "field": "operator", "values": operators})
            continue  # different operators have different parameters
        params = [effective_params(n) for n in nodes]
        for name in dict.fromkeys(name for p in params for name in p):
            values = [p.get(name) for p in params]
            if any(v != values[0] for v in values):
                rows.append({"node": node_id, "field": name, "values": values})
    return rows


def effective_params(node: Node) -> dict:
    """The node's parameters with the operator's defaults filled in, so an omitted default and the
    same value written out compare equal. An operator no longer registered keeps them as saved."""
    op = registry.find(node.operator, node.version)
    return op.params.model_validate(node.params).model_dump(mode="json") if op else node.params


def mismatches(runs: list[Ledger]) -> list[dict]:
    """Settings that differ between runs and could explain differing results: costs and execution
    assumptions (each backtest's summary), the runtime (qrs source, Python, dependency lock and
    packages), and the code of any operator that more than one run used."""
    settings = [{**r.assumptions, **runtime(r.record["runtime"])} for r in runs]
    operator_code = [{f"operator code: {key}": op["source_sha256"] for key, op in r.record["operators"].items()} for r in runs]
    return differing(settings, ignore_absent=False) + differing(operator_code, ignore_absent=True)


def runtime(identity: dict) -> dict:
    flat = {f"runtime: {name}": value for name, value in identity.items() if name != "packages"}
    return flat | {f"runtime: {name}": version for name, version in identity["packages"].items()}


def differing(settings: list[dict], ignore_absent: bool) -> list[dict]:
    """Each setting whose values differ across runs. With `ignore_absent`, a run without the setting
    (one that used no such operator) is left out of the comparison but still shown."""
    rows = []
    for name in dict.fromkeys(name for s in settings for name in s):
        values = [s.get(name) for s in settings]
        compared = {json.dumps(v, sort_keys=True) for v in values if not (ignore_absent and v is None)}
        if len(compared) > 1:
            rows.append({"name": name, "values": values})
    return rows
