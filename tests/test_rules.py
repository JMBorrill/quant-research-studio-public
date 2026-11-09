"""Rules and position policies: truth tables with missing values, nesting, conflicts, warm-up,
future-data isolation, and a hand-calculated long/short reversal through the futures backtest."""

import pandas as pd
import pytest

from conftest import CLOSES, INSTRUMENT, OPENS, SESSIONS, ohlc, run_recorded
from qrs import compiler, datasets, graph, registry
from qrs.analytics import metrics
from qrs.contracts import Edge, Node, PortRef, StrategyDocument
from qrs.ports import Bars
from qrs.runtime import execute
from qrs.simulation import simulate

NA = pd.NA
INDEX = pd.bdate_range("2026-01-05", periods=9, name="session")


def run(op_id, inputs, **params):
    op = registry.get(op_id, 1)
    return op.run(None, inputs, op.params.model_validate(params))


def series(*values):
    return pd.Series(values, index=INDEX[: len(values)], dtype=float)


def signal(*values):
    return pd.Series(values, index=INDEX[: len(values)], dtype="boolean")


@pytest.mark.parametrize(
    "comparison, expected", [(">", [NA, False, NA, True]), (">=", [NA, True, NA, True]), ("<", [NA, False, NA, False]), ("<=", [NA, True, NA, False])]
)
def test_compare_is_missing_where_either_side_is(comparison, expected):
    a, b = series(float("nan"), 1, 2, 3), series(1, 1, float("nan"), 2)
    assert run("qrs.compare", {"a": a, "b": b}, comparison=comparison)["signal"].tolist() == expected


@pytest.mark.parametrize(
    "comparison, expected", [(">", [NA, False, False, True]), (">=", [NA, False, True, True]), ("<", [NA, True, False, False]), ("<=", [NA, True, True, False])]
)
def test_threshold_compares_with_a_constant(comparison, expected):
    a = series(float("nan"), 1, 2, 3)
    assert run("qrs.threshold", {"a": a}, comparison=comparison, value=2)["signal"].tolist() == expected


def test_all_any_not_truth_tables_are_missing_until_every_input_is_valid():
    # Unlike three-valued logic, "missing or true" stays missing: no signal before every input is valid.
    a = signal(True, True, False, False, NA, NA, True, False, NA)
    b = signal(True, False, True, False, True, False, NA, NA, NA)
    assert run("qrs.all", {"a": a, "b": b})["signal"].tolist() == [True, False, False, False, NA, NA, NA, NA, NA]
    assert run("qrs.any", {"a": a, "b": b})["signal"].tolist() == [True, True, True, False, NA, NA, NA, NA, NA]
    assert run("qrs.not", {"a": a})["signal"].tolist() == [False, False, True, True, NA, NA, False, True, NA]


def test_lag_shifts_into_the_past_only():
    out = run("qrs.lag", {"a": series(1, 2, 3, 4)}, sessions=2)["series"]
    assert out.isna().tolist()[:2] == [True, True] and out.iloc[2:].tolist() == [1, 2]


@pytest.mark.parametrize("sessions", [0, -1])
def test_a_lag_that_would_read_ahead_is_refused_before_anything_runs(sessions):
    doc = nested_strategy("sha256:" + "0" * 64)
    next(n for n in doc.nodes if n.id == "past").params["sessions"] = sessions
    assert ("past", "sessions: Input should be greater than or equal to 1") in [(i.node, i.message) for i in graph.validate(doc)]


def test_long_short_conflicts_reversals_and_sizing():
    # Session: 0 nothing, 1 long, 2 long again (no pyramiding), 3 short (reversal), 4 long and short
    # (conflict -> flat), 5 exit, 6 short, 7 long and exit (exit wins), 8 missing short (no signal).
    long = signal(False, True, True, False, True, False, False, True, False)
    short = signal(False, False, False, True, True, False, True, False, NA)
    exit_ = signal(False, False, False, False, False, True, False, True, False)
    out = run("qrs.long_short", {"long": long, "short": short, "exit": exit_}, quantity=2)["positions"]
    assert out.tolist() == [0, 2, 2, -2, 0, 0, -2, 0, 0] and out.dtype == "int64"


def test_long_flat_ignores_missing_signals():
    entry, exit_ = signal(NA, True, NA, False), signal(NA, NA, True, NA)
    assert run("qrs.long_flat", {"entry": entry, "exit": exit_}, quantity=3)["positions"].tolist() == [0, 3, 0, 0]


COSTS = {"initial_cash": 100_000.0, "commission_per_contract": 2.5, "slippage_ticks": 1}


def golden_long_short(quantity: int):
    # Decisions at the closes of sessions 1 (long), 3 (short) and 4 (exit) on the conftest bars.
    long = pd.Series([False, True, False, False, False, False], index=SESSIONS, dtype="boolean")
    short = pd.Series([False, False, False, True, False, False], index=SESSIONS, dtype="boolean")
    exit_ = pd.Series([False, False, False, False, True, False], index=SESSIONS, dtype="boolean")
    targets = run("qrs.long_short", {"long": long, "short": short, "exit": exit_}, quantity=quantity)["positions"]
    bars = Bars(ohlc(OPENS, CLOSES), INSTRUMENT, "sha256:" + "0" * 64)
    fills, equity = simulate(bars, targets, **COSTS)
    return targets, fills, equity, metrics(fills, equity, COSTS["initial_cash"])


def test_long_short_reversal_reconciles_with_hand_calculated_futures_accounting():
    # Multiplier 50, tick 0.25, 1 tick of slippage, 2.50 per contract, quantity 2.
    # Session 2 open 102: BUY 2 at 102.25. Close 104: 99,995 + (104 - 102.25) * 100 = 100,170.
    # Session 3 close 103: 99,995 + (103 - 102.25) * 100 = 100,070.
    # Session 4 open 103: SELL 4 at 102.75 closes the long (+0.50 * 100 = +50) and opens a short of 2;
    #   commission 10 -> cash 100,035. Close 105: (102.75 - 105) * 100 = -225 -> 99,810.
    # Session 5 open 105: BUY 2 at 105.25 closes the short (-2.50 * 100 = -250); commission 5 -> 99,780.
    targets, fills, equity, m = golden_long_short(quantity=2)
    assert targets.tolist() == [0, 2, 2, -2, 0, 0]
    assert fills[["side", "quantity", "price", "commission", "slippage"]].values.tolist() == [
        ["BUY", 2, 102.25, 5.0, 25.0],
        ["SELL", 4, 102.75, 10.0, 50.0],
        ["BUY", 2, 105.25, 5.0, 25.0],
    ]
    assert equity["position"].tolist() == [0, 0, 2, 2, -2, 0]
    assert equity["equity"].tolist() == [100_000, 100_000, 100_170, 100_070, 99_810, 99_780]
    # Gross at the opens: long 102 -> 103 = +100, short 103 -> 105 = -200.
    assert (m["net_pnl"], m["gross_pnl"], m["commission"], m["slippage"], m["round_trips"]) == (-220, -100, 20, 100, 1)


def test_position_size_scales_pnl_and_costs_linearly():
    _, _, one, single = golden_long_short(quantity=1)
    _, _, two, double = golden_long_short(quantity=2)
    assert ((two["equity"] - 100_000) == 2 * (one["equity"] - 100_000)).all()
    assert (double["net_pnl"], double["commission"], double["slippage"]) == (2 * single["net_pnl"], 2 * single["commission"], 2 * single["slippage"])


def wire(source: tuple[str, str], target: tuple[str, str]) -> Edge:
    return Edge(source=PortRef(node=source[0], port=source[1]), target=PortRef(node=target[0], port=target[1]))


def node(node_id: str, operator_id: str, **params) -> Node:
    return Node(id=node_id, operator=operator_id, version=1, params=params)


def nested_strategy(dataset_id: str) -> StrategyDocument:
    """long = all(any(fast > slow, not(rsi < 30)), fast > fast 3 sessions ago); short = not(long);
    exit = all(long, short), which never fires but is valid only when both are."""
    nodes = [
        node("bars", "qrs.dataset", dataset_id=dataset_id),
        node("fast", "qrs.ema", period=5),
        node("slow", "qrs.sma", period=20),
        node("rsi", "qrs.rsi", period=14),
        node("past", "qrs.lag", sessions=3),
        node("trend", "qrs.compare", comparison=">"),
        node("oversold", "qrs.threshold", comparison="<", value=30),
        node("not_oversold", "qrs.not"),
        node("either", "qrs.any"),
        node("rising", "qrs.compare", comparison=">"),
        node("long", "qrs.all"),
        node("short", "qrs.not"),
        node("never", "qrs.all"),
        node("position", "qrs.long_short", quantity=2),
        node("backtest", "qrs.backtest"),
    ]
    edges = [wire(("bars", "bars"), (n, "bars")) for n in ("fast", "slow", "rsi", "backtest")] + [
        wire(("fast", "series"), ("past", "a")),
        wire(("fast", "series"), ("trend", "a")),
        wire(("slow", "series"), ("trend", "b")),
        wire(("rsi", "series"), ("oversold", "a")),
        wire(("oversold", "signal"), ("not_oversold", "a")),
        wire(("trend", "signal"), ("either", "a")),
        wire(("not_oversold", "signal"), ("either", "b")),
        wire(("fast", "series"), ("rising", "a")),
        wire(("past", "series"), ("rising", "b")),
        wire(("either", "signal"), ("long", "a")),
        wire(("rising", "signal"), ("long", "b")),
        wire(("long", "signal"), ("short", "a")),
        wire(("long", "signal"), ("never", "a")),
        wire(("short", "signal"), ("never", "b")),
        wire(("long", "signal"), ("position", "long")),
        wire(("short", "signal"), ("position", "short")),
        wire(("never", "signal"), ("position", "exit")),
        wire(("position", "positions"), ("backtest", "positions")),
    ]
    return StrategyDocument(name="nested rules", nodes=nodes, edges=edges)


def test_nested_rules_become_valid_where_the_graph_says_and_run_end_to_end(tmp_path):
    dataset_id = datasets.create_synthetic(tmp_path)
    doc = nested_strategy(dataset_id)
    assert graph.validate(doc) == []
    warm_up = graph.effective_warm_up(doc)
    # rsi 14, fast 4 + lag 3 = 7, slow 19: every rule above the SMA waits for it.
    assert [warm_up[n] for n in ("oversold", "rising", "trend", "either", "long", "short", "never", "backtest")] == [14, 7, 19, 19, 19, 19, 19, 19]
    strategy = tmp_path / "strategy.py"
    strategy.write_text(compiler.compile_for_data(doc, tmp_path))
    ctx = run_recorded(strategy, tmp_path)
    for node, outputs in ctx.outputs.items():
        if "signal" in outputs:
            first_valid = int(outputs["signal"].notna().to_numpy().argmax())
            assert first_valid == warm_up[node], node
    positions = ctx.outputs["position"]["positions"]
    assert (positions.iloc[: warm_up["position"]] == 0).all() and set(positions.unique()) == {-2, 0, 2}

    record = execute(strategy, tmp_path, tmp_path / "out")
    assert {"qrs.all@1", "qrs.any@1", "qrs.not@1", "qrs.lag@1", "qrs.compare@1", "qrs.threshold@1", "qrs.long_short@1"} <= set(record["operators"])


def test_nested_rules_never_see_future_data(tmp_path):
    original = datasets.create_synthetic(tmp_path / "a")
    bars = datasets.load(tmp_path / "a", original)
    cut = bars.frame.index[250]
    frame = bars.frame.copy()
    frame.loc[cut:] = (frame.loc[cut:] * 0.7 / 0.25).round() * 0.25
    altered = datasets.save(tmp_path / "b", frame, name="altered", instrument=bars.instrument, evidence_class="synthetic", source="test", redistributable=True)
    outputs = []
    for home, dataset_id in ((tmp_path / "a", original), (tmp_path / "b", altered)):
        strategy = home / "strategy.py"
        strategy.write_text(compiler.compile_for_data(nested_strategy(dataset_id), home))
        execute(strategy, home, home / "out")
        fills = pd.read_csv(home / "out/results/backtest/fills.csv", parse_dates=["session"])
        equity = pd.read_csv(home / "out/results/backtest/equity.csv", index_col="session", parse_dates=True)
        outputs.append((fills[fills["session"] < cut], equity[equity.index < cut]))  # prices from `cut` on differ
    (fills, equity), (altered_fills, altered_equity) = outputs
    assert len(fills) > 0
    pd.testing.assert_frame_equal(altered_fills, fills)
    pd.testing.assert_frame_equal(altered_equity, equity)
