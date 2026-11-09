"""Strategy schema, graph validation and deterministic compilation."""

import dataclasses
import random
from pathlib import Path

import pytest
from pydantic import ValidationError

from qrs import compiler, datasets, graph, registry, templates
from qrs.cli import main
from qrs.contracts import Edge, Node, PortRef, StrategyDocument
from qrs.registry import Params, Temporal, operator

DATASET = "sha256:" + "a" * 64


@operator(
    "tests.identity",
    1,
    title="Identity",
    description="Passes a series through.",
    inputs={"x": "series"},
    outputs={"y": "series"},
    family="tests",
    params=Params,
    temporal=Temporal(warm_up=lambda p: 0),
)
def identity(ctx, inputs, params):
    return {"y": inputs["x"]}


def ema_doc() -> StrategyDocument:
    return templates.instantiate("ema_crossover", DATASET)


def messages(doc):
    return [(i.node, i.message) for i in graph.validate(doc)]


def edge(src, sport, dst, dport):
    return Edge(source=PortRef(node=src, port=sport), target=PortRef(node=dst, port=dport))


def test_template_is_valid_and_uses_fan_out_and_multiple_inputs():
    doc = ema_doc()
    assert messages(doc) == []
    consumers = [e.target.node for e in doc.edges if e.source.node == "bars"]
    assert sorted(consumers) == ["backtest", "fast", "slow"]
    assert {e.target.port for e in doc.edges if e.target.node == "cross_up"} == {"a", "b"}
    assert doc.template.id == "ema_crossover" and len(doc.template.sha256) == 64


def test_type_mismatch_duplicate_and_missing_inputs_are_reported():
    doc = ema_doc()
    doc.edges = [e for e in doc.edges if not (e.target.node == "cross_up" and e.target.port == "b")]
    doc.edges.append(edge("bars", "bars", "cross_up", "b"))  # bars into a series port
    doc.edges.append(edge("slow", "series", "cross_down", "a"))  # second connection into one input
    doc.edges = [e for e in doc.edges if e.target.node != "position" or e.target.port != "exit"]
    assert set(messages(doc)) == {
        ("cross_up", "Input 'b' expects series but bars.bars provides bars."),
        ("cross_down", "Input 'a' has more than one connection."),
        ("position", "Input 'exit' is not connected."),
    }


def test_unknown_operator_version_params_and_ports():
    doc = ema_doc()
    doc.nodes[1].params["period"] = 0
    doc.nodes.append(Node(id="mystery", operator="qrs.nothing", version=1))
    doc.nodes.append(Node(id="future", operator="qrs.ema", version=2))
    doc.edges.append(edge("fast", "nope", "backtest", "bars"))
    found = messages(doc)
    assert ("fast", "period: Input should be greater than or equal to 1") in found
    assert ("mystery", "Unknown operator qrs.nothing.") in found
    assert ("future", "Operator qrs.ema has no version 2 (available: 1).") in found
    assert ("fast", "Has no output port 'nope'.") in found


def test_effective_warm_up_adds_each_node_to_its_longest_input():
    # EMA 20 and EMA 50 need 19 and 49 sessions; a cross also needs the previous session.
    assert graph.effective_warm_up(ema_doc()) == {"bars": 0, "fast": 19, "slow": 49, "cross_up": 50, "cross_down": 50, "position": 50, "backtest": 50}


def test_data_too_short_for_the_warm_up_is_reported(tmp_path):
    # A signal is first possible at session 50 and executes at the next open: 52 sessions needed.
    short, enough = datasets.create_synthetic(tmp_path, sessions=51), datasets.create_synthetic(tmp_path, sessions=52)
    assert [(i.node, i.message) for i in graph.check_data(templates.instantiate("ema_crossover", short), tmp_path)] == [
        ("backtest", "A warm-up of 50 sessions leaves no session to trade: it needs 52 sessions; the data has 51.")
    ]
    assert graph.check_data(templates.instantiate("ema_crossover", enough), tmp_path) == []


def test_only_what_feeds_a_backtest_counts_towards_its_warm_up(tmp_path):
    doc = templates.instantiate("ema_crossover", datasets.create_synthetic(tmp_path, sessions=300))
    doc.nodes.append(Node(id="unused", operator="qrs.ema", version=1, params={"period": 500}))
    doc.edges.append(edge("bars", "bars", "unused", "bars"))
    assert graph.validate(doc) == [] and graph.check_data(doc, tmp_path) == []


@operator(
    "tests.two_speeds",
    1,
    title="Two speeds",
    description="Outputs valid after different warm-ups.",
    family="tests",
    inputs={"bars": "bars"},
    outputs={"quick": "series", "slow": "series"},
    params=Params,
    temporal=Temporal(warm_up=lambda p: {"quick": 2, "slow": 9}),
)
def two_speeds(ctx, inputs, params):
    close = inputs["bars"].frame["close"]
    return {"quick": close.rolling(3).mean(), "slow": close.rolling(10).mean()}


def test_warm_up_follows_the_output_port_each_edge_comes_from():
    doc = templates.instantiate("ema_crossover", DATASET)
    doc.nodes.append(Node(id="speeds", operator="tests.two_speeds", version=1))
    doc.edges = [e for e in doc.edges if e.target.node not in ("cross_up", "cross_down")]
    doc.edges += [edge("bars", "bars", "speeds", "bars")]
    doc.edges += [edge("speeds", "quick", "cross_up", "a"), edge("fast", "series", "cross_up", "b")]  # 2 vs 19
    doc.edges += [edge("speeds", "quick", "cross_down", "a"), edge("speeds", "quick", "cross_down", "b")]
    warm_up = graph.effective_warm_up(doc)
    assert (warm_up["speeds"], warm_up["cross_up"], warm_up["cross_down"], warm_up["backtest"]) == (9, 20, 3, 20)


def test_a_warm_up_must_name_every_output():
    op = registry.get("tests.two_speeds", 1)
    broken = dataclasses.replace(op, temporal=Temporal(warm_up=lambda p: {"quick": 2}))
    with pytest.raises(ValueError, match=r"declares warm-up for \['quick'\] but its outputs are \['quick', 'slow'\]"):
        broken.output_warm_up(Params())


def test_operator_info_exposes_family_and_alignment():
    info = {op.id: op.info() for op in registry.all_operators()}
    assert (info["qrs.ema"].family, info["qrs.ema"].alignment) == ("indicators", "same_index")
    assert {info[i].family for i in ("qrs.dataset", "qrs.cross", "qrs.long_flat", "qrs.backtest")} == {"sources", "rules", "positions", "evaluation"}


def test_a_strategy_must_produce_a_backtest():
    doc = ema_doc()
    doc.nodes = [n for n in doc.nodes if n.id == "bars"]
    doc.edges = []
    assert messages(doc) == [(None, "The strategy needs a node with a backtest output, such as Backtest.")]


def test_only_known_templates_can_be_instantiated():
    with pytest.raises(KeyError, match="Unknown template"):
        templates.instantiate("../../schemas/qrs.schema")


def test_cycles_are_rejected():
    doc = StrategyDocument(
        name="loop",
        nodes=[Node(id="a", operator="tests.identity", version=1), Node(id="b", operator="tests.identity", version=1)],
        edges=[edge("a", "y", "b", "x"), edge("b", "y", "a", "x")],
    )
    assert (None, "The graph has a cycle; these nodes cannot run: a, b.") in messages(doc)


@pytest.mark.parametrize("bad_id", ["ctx", "class", "Upper", "1st", "has-dash"])
def test_node_ids_must_be_safe_python_names(bad_id):
    with pytest.raises(ValidationError):
        Node(id=bad_id, operator="qrs.ema", version=1)


def test_unsupported_schema_version_is_rejected():
    with pytest.raises(ValidationError):
        StrategyDocument.model_validate({**ema_doc().model_dump(), "version": 2})


def test_compilation_is_deterministic_and_ignores_layout_order_and_names():
    doc = ema_doc()
    code = compiler.compile_strategy(doc)
    shuffled = doc.model_copy(deep=True)
    random.Random(1).shuffle(shuffled.nodes)
    random.Random(2).shuffle(shuffled.edges)
    shuffled.name = "Renamed"
    shuffled.layout = {}
    shuffled.template = None
    assert compiler.compile_strategy(shuffled) == code
    # Defaults are written out, so omitting a parameter compiles identically.
    del shuffled.nodes[[n.id for n in shuffled.nodes].index("fast")].params["column"]
    assert compiler.compile_strategy(shuffled) == code
    assert "ctx.op('fast', 'qrs.ema', 1, {'bars': bars['bars']}, {'period': 20, 'column': 'close'})" in code


def test_parameter_changes_change_the_code():
    doc = ema_doc()
    other = doc.model_copy(deep=True)
    other.nodes[1].params["period"] = 21
    assert compiler.compile_strategy(other) != compiler.compile_strategy(doc)


def test_invalid_strategies_do_not_compile():
    doc = ema_doc()
    doc.nodes[0].params["dataset_id"] = ""
    with pytest.raises(compiler.InvalidStrategy, match="dataset_id: String should match pattern"):
        compiler.compile_strategy(doc)


def test_committed_schema_matches_the_python_contracts(tmp_path):
    main(["schema", "--out", str(tmp_path / "schema.json")])
    committed = Path(__file__).parents[1] / "schemas" / "qrs.schema.json"
    assert (tmp_path / "schema.json").read_text() == committed.read_text(), "run `qrs schema` and `npm run gen:types`"
