"""The plan's exit-gate combination test: every built-in indicator output paired with every rule that
turns series into signals, combined through all/any/not into both position policies. Each strategy
must validate, pass the data check, run, trade only after its warm-up, and ignore future data. An
indicator of a series (Stage 5's rolling deviation) is fed the bars' one-session return."""

import pandas as pd
import pytest

from qrs import compiler, datasets, graph, registry
from qrs.contracts import Edge, Node, PortRef, StrategyDocument
from qrs.runtime import execute

INDICATOR_OUTPUTS = [
    (op.id, port)
    for op in registry.all_operators()
    if op.id.startswith("qrs.") and op.family == "indicators" and op.id != "qrs.column"
    for port, port_type in op.outputs.items()
    if port_type == "series"
]
SERIES_RULES = ["compare with price", "threshold at median", "cross price", "compare with own lag"]
CUT = 120  # sessions from here on are rewritten for the future-data check


def node(node_id: str, operator_id: str, **params) -> Node:
    return Node(id=node_id, operator=operator_id, version=1, params=params)


def wire(source: tuple[str, str], target: tuple[str, str]) -> Edge:
    return Edge(source=PortRef(node=source[0], port=source[1]), target=PortRef(node=target[0], port=target[1]))


def of_series(operator_id: str) -> bool:
    return registry.get(operator_id, 1).inputs == {"series": "series"}


def median_of(indicator: tuple[str, str], data_dir, dataset_id: str) -> float:
    """A fixed threshold for an indicator, taken once from the original data."""
    op = registry.get(indicator[0], 1)
    bars = datasets.load(data_dir, dataset_id)
    given = {"series": bars.frame["close"].pct_change()} if of_series(indicator[0]) else {"bars": bars}
    values = op.run(None, given, op.params.model_validate({}))[indicator[1]]
    return float(values.median())


def strategy(dataset_id: str, indicator: tuple[str, str], rule: str, policy: str, threshold: float) -> StrategyDocument:
    operator_id, port = indicator
    nodes = [node("bars", "qrs.dataset", dataset_id=dataset_id), node("x", operator_id), node("price", "qrs.column"), node("backtest", "qrs.backtest")]
    edges = [wire(("bars", "bars"), (n, "bars")) for n in ("price", "backtest")]
    if of_series(operator_id):
        nodes.append(node("change", "qrs.returns"))
        edges += [wire(("bars", "bars"), ("change", "bars")), wire(("change", "series"), ("x", "series"))]
    else:
        edges.append(wire(("bars", "bars"), ("x", "bars")))
    x = ("x", port)
    if rule == "compare with price":
        nodes.append(node("s", "qrs.compare", comparison=">"))
        edges += [wire(x, ("s", "a")), wire(("price", "series"), ("s", "b"))]
    elif rule == "threshold at median":
        nodes.append(node("s", "qrs.threshold", comparison=">", value=threshold))
        edges.append(wire(x, ("s", "a")))
    elif rule == "cross price":
        nodes.append(node("s", "qrs.cross", direction="above"))
        edges += [wire(x, ("s", "a")), wire(("price", "series"), ("s", "b"))]
    else:
        nodes += [node("past", "qrs.lag", sessions=1), node("s", "qrs.compare", comparison=">")]
        edges += [wire(x, ("past", "a")), wire(x, ("s", "a")), wire(("past", "series"), ("s", "b"))]
    # not_s = not(s); both = all(s, s) exercises all; never = all(s, not_s) never fires.
    nodes += [node("not_s", "qrs.not"), node("both", "qrs.all"), node("never", "qrs.all")]
    edges += [wire(("s", "signal"), ("not_s", "a")), wire(("s", "signal"), ("both", "a")), wire(("s", "signal"), ("both", "b"))]
    edges += [wire(("s", "signal"), ("never", "a")), wire(("not_s", "signal"), ("never", "b"))]
    if policy == "qrs.long_flat":
        nodes += [node("exit", "qrs.any"), node("position", policy)]
        edges += [wire(("not_s", "signal"), ("exit", "a")), wire(("never", "signal"), ("exit", "b"))]
        edges += [wire(("both", "signal"), ("position", "entry")), wire(("exit", "signal"), ("position", "exit"))]
    else:
        nodes.append(node("position", policy))
        edges += [
            wire(("both", "signal"), ("position", "long")),
            wire(("not_s", "signal"), ("position", "short")),
            wire(("never", "signal"), ("position", "exit")),
        ]
    edges.append(wire(("position", "positions"), ("backtest", "positions")))
    return StrategyDocument(name=f"{operator_id}.{port} / {rule} / {policy}", nodes=nodes, edges=edges)


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    original = tmp_path_factory.mktemp("original")
    dataset_id = datasets.create_synthetic(original, sessions=200)
    bars = datasets.load(original, dataset_id)
    frame = bars.frame.copy()
    frame.iloc[CUT:] = (frame.iloc[CUT:] * 1.2 / 0.25).round() * 0.25
    altered = tmp_path_factory.mktemp("altered")
    altered_id = datasets.save(altered, frame, name="altered", instrument=bars.instrument, evidence_class="synthetic", source="test", redistributable=True)
    return (original, dataset_id), (altered, altered_id), bars.frame.index


def test_every_indicator_output_is_covered():
    assert len(INDICATOR_OUTPUTS) == 12 and len(INDICATOR_OUTPUTS) * len(SERIES_RULES) == 48


@pytest.mark.parametrize("rule", SERIES_RULES)
@pytest.mark.parametrize("indicator", INDICATOR_OUTPUTS, ids=lambda i: ".".join(i))
def test_indicator_and_rule_combinations_run_and_ignore_future_data(data, indicator, rule):
    (home, dataset_id), (altered_home, altered_id), sessions = data
    policy = ["qrs.long_flat", "qrs.long_short"][(INDICATOR_OUTPUTS.index(indicator) + SERIES_RULES.index(rule)) % 2]
    threshold = median_of(indicator, home, dataset_id)  # the same constant for both runs
    results = []
    for data_dir, ident in ((home, dataset_id), (altered_home, altered_id)):
        doc = strategy(ident, indicator, rule, policy, threshold)
        assert graph.validate(doc) == []
        out = data_dir / "runs" / doc.name.replace("/", "_").replace(" ", "")
        out.mkdir(parents=True)
        (out / "strategy.py").write_text(compiler.compile_for_data(doc, data_dir))
        execute(out / "strategy.py", data_dir, out)
        results.append(
            (
                pd.read_csv(out / "results/backtest/fills.csv", parse_dates=["session"]),
                pd.read_csv(out / "results/backtest/equity.csv", index_col="session", parse_dates=True),
            )
        )
    (fills, equity), (altered_fills, altered_equity) = results
    warm_up = graph.effective_warm_up(doc)["backtest"]
    assert fills.empty or fills["session"].min() > sessions[warm_up], "a fill before the first signal could be executed"
    before = sessions[CUT]
    pd.testing.assert_frame_equal(altered_fills[altered_fills["session"] < before], fills[fills["session"] < before])
    pd.testing.assert_frame_equal(altered_equity[altered_equity.index < before], equity[equity.index < before])
