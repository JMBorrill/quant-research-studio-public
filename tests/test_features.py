"""Feature tables, returns and forward-return labels (Stage 4 workstream 2) against hand-calculated
values. The prices make every return exact in binary, so values are compared exactly. Operators run
through the real runtime, so its port and alignment checks apply."""

import sys

import pandas as pd
import pytest

from conftest import EXTENSION_DIR, INSTRUMENT, ohlc
from qrs import compiler, datasets, graph, templates
from qrs.contracts import Edge, Node, PortRef, StrategyDocument
from qrs.ports import Bars
from qrs.runtime import Run, RunError, execute

sys.path.insert(0, str(EXTENSION_DIR))
import qrs_roc  # noqa: E402,F401  registers ext.candle, the example custom table operator

INDEX = pd.bdate_range("2026-01-05", periods=5, name="session")
OPENS = [16, 24, 30, 15, 30]
CLOSES = [16, 20, 30, 15, 22.5]
BARS = Bars(ohlc(OPENS, CLOSES, INDEX), INSTRUMENT, "sha256:" + "0" * 64)  # highs/lows 0.5 outside


def run(op_id: str, inputs: dict, **params) -> dict:
    return Run(data_dir=None).op("node", op_id, 1, inputs, params)


def values(series: pd.Series) -> list:
    return [None if pd.isna(v) else v for v in series]


def test_returns_look_back_period_sessions():
    # 20/16 - 1 = 0.25, 30/20 - 1 = 0.5, 15/30 - 1 = -0.5, 22.5/15 - 1 = 0.5
    assert values(run("qrs.returns", {"bars": BARS})["series"]) == [None, 0.25, 0.5, -0.5, 0.5]
    # 30/16 - 1 = 0.875, 15/20 - 1 = -0.25, 22.5/30 - 1 = -0.25
    assert values(run("qrs.returns", {"bars": BARS}, period=2)["series"]) == [None, None, 0.875, -0.25, -0.25]
    # Opens: 24/16 - 1 = 0.5, 30/24 - 1 = 0.25, 15/30 - 1 = -0.5, 30/15 - 1 = 1
    assert values(run("qrs.returns", {"bars": BARS}, column="open")["series"]) == [None, 0.5, 0.25, -0.5, 1.0]


def test_a_forward_return_is_the_return_horizon_sessions_later_and_unknown_at_the_end():
    one = run("qrs.forward_return", {"bars": BARS})["label"]
    assert one.horizon == 1 and values(one.values) == [0.25, 0.5, -0.5, 0.5, None]
    two = run("qrs.forward_return", {"bars": BARS}, horizon=2)["label"]
    assert two.horizon == 2 and values(two.values) == [0.875, -0.25, -0.25, None, None]
    # The target at a session is the past return `horizon` sessions later: one calculation for both.
    pd.testing.assert_series_equal(two.values, run("qrs.returns", {"bars": BARS}, period=2)["series"].shift(-2))


def test_a_table_starts_with_one_named_column_and_merge_adds_more():
    close = run("qrs.column", {"bars": BARS})["series"]
    table = run("qrs.table", {"series": close}, name="close")["table"]
    assert table.columns.tolist() == ["close"] and table["close"].tolist() == CLOSES
    change = run("qrs.returns", {"bars": BARS})["series"]
    wider = run("qrs.merge", {"table": table, "series": change}, name="change")["table"]
    assert wider.columns.tolist() == ["close", "change"]
    assert wider["close"].tolist() == CLOSES and values(wider["change"]) == [None, 0.25, 0.5, -0.5, 0.5]
    assert table.columns.tolist() == ["close"]  # merging leaves its input unchanged


def test_merge_refuses_a_name_already_in_the_table_and_names_must_be_lower_case_identifiers():
    close = run("qrs.column", {"bars": BARS})["series"]
    table = run("qrs.table", {"series": close}, name="close")["table"]
    with pytest.raises(RunError, match="already has a column 'close'"):
        run("qrs.merge", {"table": table, "series": close}, name="close")
    for bad in ["", "Close", "1st", "a-b"]:
        with pytest.raises(RunError, match="name"):
            run("qrs.table", {"series": close}, name=bad)


def test_the_custom_candle_table_has_each_sessions_range_and_body():
    table = run("ext.candle", {"bars": BARS})["table"]
    assert table.columns.tolist() == ["range", "body"]
    assert table["range"].tolist() == [1.0, 5.0, 1.0, 1.0, 8.5]  # max(open, close) - min(open, close) + 1
    assert table["body"].tolist() == [0.0, -4.0, 0.0, 0.0, -7.5]  # close - open


def node(node_id: str, operator_id: str, **params) -> Node:
    return Node(id=node_id, operator=operator_id, version=1, params=params)


def wire(source: tuple[str, str], target: tuple[str, str]) -> Edge:
    return Edge(source=PortRef(node=source[0], port=source[1]), target=PortRef(node=target[0], port=target[1]))


def with_features(doc: StrategyDocument) -> StrategyDocument:
    """The strategy plus a feature table (SMA 20, then a 5-session return merged in) and a 3-session
    forward-return label, read from its bars. Nothing reads them, so its trades are unchanged."""
    nodes = [
        node("trend", "qrs.sma", period=20),
        node("change", "qrs.returns", period=5),
        node("features", "qrs.table", name="trend"),
        node("more", "qrs.merge", name="change"),
        node("target", "qrs.forward_return", horizon=3),
    ]
    edges = [
        wire(("bars", "bars"), ("trend", "bars")),
        wire(("bars", "bars"), ("change", "bars")),
        wire(("bars", "bars"), ("target", "bars")),
        wire(("trend", "series"), ("features", "series")),
        wire(("features", "table"), ("more", "table")),
        wire(("change", "series"), ("more", "series")),
    ]
    return doc.model_copy(update={"nodes": [*doc.nodes, *nodes], "edges": [*doc.edges, *edges]})


def test_warm_ups_of_tables_and_labels():
    warm_up = graph.effective_warm_up(with_features(templates.instantiate("ema_crossover", BARS.dataset_id)))
    # SMA(20) is valid from session 19 and the 5-session return from 5, so the merged table is
    # complete from 19; a forward return declares its horizon.
    assert (warm_up["change"], warm_up["features"], warm_up["more"], warm_up["target"]) == (5, 19, 19, 3)


def test_tables_and_labels_connect_only_to_inputs_of_their_own_type():
    doc = with_features(templates.instantiate("ema_crossover", BARS.dataset_id))
    misuses = {
        (("features", "table"), ("cross_up", "a")): ("cross_up", "Input 'a' expects series but features.table provides table."),
        (("target", "label"), ("more", "series")): ("more", "Input 'series' expects series but target.label provides label."),
        (("change", "series"), ("more", "table")): ("more", "Input 'table' expects table but change.series provides series."),
    }
    for (source, target), issue in misuses.items():
        edges = [e for e in doc.edges if (e.target.node, e.target.port) != target] + [wire(source, target)]
        assert issue in [(i.node, i.message) for i in graph.validate(doc.model_copy(update={"edges": edges}))]


def test_feature_and_label_nodes_compile_and_run_without_changing_the_strategys_results(tmp_path):
    dataset_id = datasets.create_synthetic(tmp_path)
    plain, featured = templates.instantiate("ema_crossover", dataset_id), with_features(templates.instantiate("ema_crossover", dataset_id))
    records = {}
    for name, doc in (("plain", plain), ("featured", featured)):
        (tmp_path / f"{name}.py").write_text(compiler.compile_for_data(doc, tmp_path))
        records[name] = execute(tmp_path / f"{name}.py", tmp_path, tmp_path / name)
    assert {"qrs.returns@1", "qrs.table@1", "qrs.merge@1", "qrs.forward_return@1"} <= set(records["featured"]["operators"])
    assert records["featured"]["result_sha256"] == records["plain"]["result_sha256"]
