"""Graph validation and ordering. A strategy is a DAG of operators connected port to port: an output
may feed any number of inputs, and each input takes exactly one connection of the same type."""

import heapq
from datetime import date
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from qrs import datasets, registry
from qrs.contracts import DatasetManifest, Edge, Issue, StrategyDocument


def validate(doc: StrategyDocument) -> list[Issue]:
    issues: list[Issue] = []
    ops = {}
    seen = set()
    for node in doc.nodes:
        if node.id in seen:
            issues.append(Issue(node=node.id, message=f"Duplicate node ID '{node.id}'."))
        seen.add(node.id)
        op = registry.find(node.operator, node.version)
        if op is None:
            issues.append(Issue(node=node.id, message=registry.unknown_operator_message(node.operator, node.version)))
            continue
        ops[node.id] = op
        try:
            op.params.model_validate(node.params)
        except ValidationError as e:
            for err in e.errors():
                field = ".".join(str(p) for p in err["loc"]) or "params"
                issues.append(Issue(node=node.id, message=f"{field}: {err['msg']}"))

    connected: set[tuple[str, str]] = set()
    for edge in doc.edges:
        src, dst = edge.source, edge.target
        if src.node not in seen or dst.node not in seen:
            issues.append(Issue(message=f"Connection {src.node}.{src.port} -> {dst.node}.{dst.port} refers to a missing node."))
            continue
        if src.node not in ops or dst.node not in ops:
            continue  # the unknown operator is already reported
        out_type = ops[src.node].outputs.get(src.port)
        in_type = ops[dst.node].inputs.get(dst.port)
        if out_type is None:
            issues.append(Issue(node=src.node, message=f"Has no output port '{src.port}'."))
        if in_type is None:
            issues.append(Issue(node=dst.node, message=f"Has no input port '{dst.port}'."))
        if out_type and in_type and out_type != in_type:
            issues.append(Issue(node=dst.node, message=f"Input '{dst.port}' expects {in_type} but {src.node}.{src.port} provides {out_type}."))
        if (dst.node, dst.port) in connected:
            issues.append(Issue(node=dst.node, message=f"Input '{dst.port}' has more than one connection."))
        connected.add((dst.node, dst.port))

    for node_id, op in ops.items():
        for port in op.inputs:
            if (node_id, port) not in connected:
                issues.append(Issue(node=node_id, message=f"Input '{port}' is not connected."))

    ordered = topological_order(doc)
    if len(ordered) < len(seen):
        issues.append(Issue(message=f"The graph has a cycle; these nodes cannot run: {', '.join(sorted(seen - set(ordered)))}."))
    if not doc.nodes:
        issues.append(Issue(message="The strategy has no nodes."))
    elif ops and not any("backtest" in op.outputs.values() for op in ops.values()):
        issues.append(Issue(message="The strategy needs a node with a backtest output, such as Backtest."))
    return issues


def topological_order(doc: StrategyDocument) -> list[str]:
    """Node IDs in dependency order; ties break alphabetically so the order is deterministic.
    Nodes on or downstream of a cycle are omitted."""
    parents = {n.id: set() for n in doc.nodes}
    children: dict[str, set[str]] = {n.id: set() for n in doc.nodes}
    for edge in doc.edges:
        if edge.source.node in parents and edge.target.node in parents:
            parents[edge.target.node].add(edge.source.node)
            children[edge.source.node].add(edge.target.node)
    ready = [n for n, p in parents.items() if not p]
    heapq.heapify(ready)
    ordered = []
    while ready:
        node = heapq.heappop(ready)
        ordered.append(node)
        for child in children[node]:
            parents[child].discard(node)
            if not parents[child]:
                heapq.heappush(ready, child)
    return ordered


def effective_warm_up(doc: StrategyDocument) -> dict[str, int]:
    """Sessions before each node's outputs are valid. Each output port's warm-up is its declared
    warm-up plus the longest warm-up among the output ports wired into the node; a node reports its
    longest output. A label input is added instead: its targets are known only `horizon` sessions
    after the features beside them, so a model fitted on them starts that much later. The document
    must be valid."""
    nodes = {n.id: n for n in doc.nodes}
    edges_in: dict[str, list[Edge]] = {n.id: [] for n in doc.nodes}
    for edge in doc.edges:
        edges_in[edge.target.node].append(edge)
    by_port: dict[tuple[str, str], int] = {}
    by_node: dict[str, int] = {}
    for node_id in topological_order(doc):
        node = nodes[node_id]
        op = registry.get(node.operator, node.version)
        labels, others = [], []
        for edge in edges_in[node_id]:
            warm_up = by_port[(edge.source.node, edge.source.port)]
            (labels if op.inputs[edge.target.port] == "label" else others).append(warm_up)
        upstream = max(others, default=0) + max(labels, default=0)
        for port, own in op.output_warm_up(op.params.model_validate(node.params)).items():
            by_port[(node_id, port)] = own + upstream
        by_node[node_id] = max((by_port[(node_id, p)] for p in op.outputs), default=upstream)
    return by_node


def check_data(doc: StrategyDocument, data_dir: Path, until: date | None = None) -> list[Issue]:
    """Each dataset parameter must name a dataset of its kind, and an as-of join needs bars whose
    exchange calendar gives each of their sessions' close. Each backtest must leave at least one
    session to trade after the warm-up of everything feeding it, on the bars feeding it (only the
    sessions before `until`, if it is given); observations do not count. Raises DatasetError for a
    dataset that is missing or changed. The document must be valid."""
    nodes = {n.id: n for n in doc.nodes}
    parents: dict[str, list[str]] = {n.id: [] for n in doc.nodes}
    for edge in doc.edges:
        parents[edge.target.node].append(edge.source.node)
    warm_up = effective_warm_up(doc)
    sessions: dict[str, int] = {}  # node -> fewest sessions among the bars upstream of it
    upstream: dict[str, dict[str, DatasetManifest]] = {}  # node -> the bars upstream of it, by dataset ID
    issues = []
    for node_id in topological_order(doc):
        node = nodes[node_id]
        op = registry.get(node.operator, node.version)
        bars = {}
        for param in op.dataset_params():
            manifest, expected = datasets.read_manifest(data_dir, node.params[param]), op.dataset_kind(param)
            if manifest.kind != expected:
                issues.append(Issue(node=node_id, message=f"{param}: the dataset holds {datasets.KINDS[manifest.kind]}, not {datasets.KINDS[expected]}."))
            elif manifest.kind == datasets.BARS:
                bars[node.params[param]] = manifest
        counts = [_sessions(data_dir, dataset_id, manifest, until) for dataset_id, manifest in bars.items()]
        counts += [sessions[p] for p in parents[node_id] if p in sessions]
        if counts:
            sessions[node_id] = min(counts)
        upstream[node_id] = bars | {k: v for p in parents[node_id] for k, v in upstream[p].items()}
        if op.temporal.alignment == "as_of":
            issues += [Issue(node=node_id, message=m) for m in _unknown_closes(data_dir, upstream[node_id])]
        # A signal at the first valid session is executed at the next session's open.
        needed = warm_up[node_id] + 2
        continuous = sorted(m.name for m in upstream[node_id].values() if m.instrument.continuous is not None)
        if "backtest" in op.outputs.values() and continuous:
            # Amendment A3: a continuous series' prices are adjusted research data, not prices anyone could trade at.
            issues.append(Issue(node=node_id, message=f"A backtest cannot trade a continuous series ({', '.join(continuous)}): its prices are not executable."))
        if "backtest" in op.outputs.values() and node_id in sessions and needed > sessions[node_id]:
            message = f"A warm-up of {warm_up[node_id]} sessions leaves no session to trade: it needs {needed} sessions; the data has {sessions[node_id]}."
            issues.append(Issue(node=node_id, message=message))
    return issues


def _unknown_closes(data_dir: Path, bars: dict[str, DatasetManifest]) -> list[str]:
    """Why an as-of join could not know the close of every session of these bars: they have no
    exchange calendar, or a bar falls on a day their calendar has no session."""
    names = sorted(m.name for m in bars.values() if datasets.bars_calendar(m) is None)
    if names:
        return [f"An as-of join needs each session's close from the bars' exchange calendar, but these bars have none: {', '.join(names)}."]
    problems = []
    for dataset_id, manifest in bars.items():
        try:
            datasets.session_closes(datasets.load(data_dir, dataset_id))
        except datasets.DatasetError as e:
            problems.append(f"{manifest.name}: {e}")
    return problems


def _sessions(data_dir: Path, dataset_id: str, manifest: DatasetManifest, until: date | None) -> int:
    if until is None:
        return manifest.rows
    return int((datasets.load(data_dir, dataset_id).frame.index < pd.Timestamp(until)).sum())


def traded_bars(doc: StrategyDocument) -> str | None:
    """The bars dataset a strategy's backtest trades: the one its bars input is drawn from, through
    any operators (such as extensions) that pass bars on. None if no bars dataset supplies them."""
    nodes = {n.id: n for n in doc.nodes}
    feeding = {(e.target.node, e.target.port): e.source.node for e in doc.edges}
    node_id = next((n.id for n in doc.nodes if (op := registry.find(n.operator, n.version)) and "backtest" in op.outputs.values()), None)
    while node_id is not None:  # from the backtest up its bars inputs; a valid document is acyclic
        node = nodes[node_id]
        op = registry.find(node.operator, node.version)
        if op is None:
            return None
        for param in op.dataset_params():
            if op.dataset_kind(param) == datasets.BARS:
                return node.params.get(param)
        bars_inputs = [port for port, kind in op.inputs.items() if kind == "bars"]
        node_id = feeding.get((node_id, bars_inputs[0])) if bars_inputs else None
    return None


def dataset_ids(doc: StrategyDocument) -> list[str]:
    """Dataset IDs referenced by node parameters (fields marked as dataset references)."""
    ids = set()
    for node in doc.nodes:
        op = registry.find(node.operator, node.version)
        for name in op.dataset_params() if op else []:
            if isinstance(node.params.get(name), str):
                ids.add(node.params[name])
    return sorted(ids)


def upstream_bars(doc: StrategyDocument, node_id: str) -> list[str]:
    """The bars datasets feeding a node through any of its ancestors. An operator's inputs and outputs
    share one session index, so a node's sessions are theirs."""
    nodes = {n.id: n for n in doc.nodes}
    parents: dict[str, list[str]] = {n.id: [] for n in doc.nodes}
    for edge in doc.edges:
        parents[edge.target.node].append(edge.source.node)
    found, seen, stack = set(), set(), [node_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        op = registry.find(nodes[current].operator, nodes[current].version)
        found |= {nodes[current].params.get(p) for p in (op.dataset_params() if op else []) if op.dataset_kind(p) == datasets.BARS}
        stack += parents[current]
    return sorted(found - {None})
