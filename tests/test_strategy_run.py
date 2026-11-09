"""The EMA crossover template end to end: reproducibility and an independent accounting check."""

import json

import pandas as pd
import pytest

from conftest import cash_flow_ledger
from qrs import compiler, datasets, graph, templates
from qrs.cli import main
from qrs.contracts import Edge, Node, PortRef, StrategyDocument
from qrs.runtime import Run, RunError, execute


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    dataset_id = datasets.create_synthetic(data_dir)
    strategy = data_dir / "strategy.py"
    strategy.write_text(compiler.compile_strategy(templates.instantiate("ema_crossover", dataset_id)))
    out = data_dir / "out"
    return data_dir, strategy, execute(strategy, data_dir, out), out


def test_reruns_reproduce_the_record(demo, tmp_path):
    data_dir, strategy, record, _ = demo
    assert execute(strategy, data_dir, tmp_path / "again") == record
    assert record["datasets"][next(iter(record["datasets"]))]["evidence_class"] == "synthetic"
    assert set(record["operators"]) == {"qrs.dataset@1", "qrs.ema@1", "qrs.cross@1", "qrs.long_flat@1", "qrs.backtest@1"}


def test_fills_follow_signals_at_the_next_open(demo):
    data_dir, _, record, out = demo
    bars = datasets.load(data_dir, next(iter(record["datasets"]))).frame
    fills = pd.read_csv(out / "results/backtest/fills.csv", parse_dates=["session"])
    equity = pd.read_csv(out / "results/backtest/equity.csv", index_col="session", parse_dates=True)
    assert len(fills) >= 10, "the demo should trade several times"
    for fill in fills.itertuples():
        position = equity.index.get_loc(fill.session)
        expected = bars["open"].iloc[position] + (0.25 if fill.side == "BUY" else -0.25)
        assert fill.price == expected
        # The position changes only on the fill's session, i.e. the session after the decision.
        assert equity["position"].iloc[position] != equity["position"].iloc[position - 1]


def test_equity_reconciles_with_independent_cash_flow_accounting(demo):
    """Nautilus marks positions via realised + unrealised P&L. Recompute equity independently as
    cash flows (pay price * qty * multiplier + commission on buys, receive on sells) plus position
    value at the close, and require the two to agree on every session."""
    data_dir, _, record, out = demo
    bars = datasets.load(data_dir, next(iter(record["datasets"])))
    fills = pd.read_csv(out / "results/backtest/fills.csv", parse_dates=["session"])
    equity = pd.read_csv(out / "results/backtest/equity.csv", index_col="session", parse_dates=True)
    summary = json.loads((out / "results/backtest/summary.json").read_text())

    expected = cash_flow_ledger(bars, fills, summary["metrics"]["initial_cash"])["equity"].tolist()
    assert equity["equity"].tolist() == pytest.approx(expected, abs=1e-6)
    assert summary["metrics"]["final_equity"] == pytest.approx(expected[-1], abs=1e-6)
    assert summary["metrics"]["commission"] == pytest.approx(fills["commission"].sum())


def test_a_program_without_a_backtest_is_an_explicit_error(tmp_path):
    # Validation refuses such graphs (test_graph.py); this covers an edited or hand-written program.
    strategy = tmp_path / "strategy.py"
    strategy.write_text("def run(ctx):\n    pass\n")
    with pytest.raises(RunError, match="no backtest result"):
        execute(strategy, tmp_path, tmp_path / "out")


def test_future_data_cannot_change_earlier_decisions(demo, tmp_path):
    """Rewrite every price from session 300 on; everything decided earlier must be unchanged."""
    data_dir, _, record, out = demo
    original = datasets.load(data_dir, next(iter(record["datasets"])))
    cut = original.frame.index[300]
    frame = original.frame.copy()
    frame.loc[cut:] = (frame.loc[cut:] * 1.3 / 0.25).round() * 0.25
    altered_id = datasets.save(tmp_path, frame, name="altered", instrument=original.instrument, evidence_class="synthetic", source="test", redistributable=True)
    strategy = tmp_path / "strategy.py"
    strategy.write_text(compiler.compile_strategy(templates.instantiate("ema_crossover", altered_id)))
    execute(strategy, tmp_path, tmp_path / "out")

    def read(folder):
        fills = pd.read_csv(folder / "results/backtest/fills.csv", parse_dates=["session"])
        equity = pd.read_csv(folder / "results/backtest/equity.csv", index_col="session", parse_dates=True)
        return fills[fills["session"] < cut], equity[equity.index < cut]

    (fills, equity), (altered_fills, altered_equity) = read(out), read(tmp_path / "out")
    assert len(fills) > 0
    pd.testing.assert_frame_equal(altered_fills, fills)
    pd.testing.assert_frame_equal(altered_equity, equity)


def test_cli_refuses_to_write_into_a_used_directory(demo):
    data_dir, strategy, _, out = demo
    before = (out / "record.json").read_bytes()
    doc = data_dir / "strategy.json"
    doc.write_text(templates.instantiate("ema_crossover", "sha256:" + "0" * 64).model_dump_json())
    assert main(["run", str(doc), "--data", str(data_dir), "--out", str(out)]) == 1
    assert (out / "record.json").read_bytes() == before and not (out / "strategy.py").exists()


def demo_dataset(demo) -> tuple[str, pd.DataFrame]:
    """The demo run's dataset and its bars."""
    data_dir, _, record, _ = demo
    dataset_id = next(iter(record["datasets"]))
    return dataset_id, datasets.load(data_dir, dataset_id).frame


def test_a_run_reads_no_bar_on_or_after_its_cutoff(demo):
    data_dir = demo[0]
    dataset_id, frame = demo_dataset(demo)
    until = frame.index[300].date()
    assert Run(data_dir, until).dataset(dataset_id).frame.equals(frame.iloc[:300])
    assert Run(data_dir).dataset(dataset_id).frame.equals(frame)


def test_a_cutoff_run_matches_the_same_strategy_on_data_that_ends_before_it(demo, tmp_path):
    data_dir, strategy, record, _ = demo
    dataset_id, frame = demo_dataset(demo)
    until = frame.index[300].date()
    cut = execute(strategy, data_dir, tmp_path / "cut", until)
    assert cut["until"] == until.isoformat() and "until" not in record

    manifest = datasets.read_manifest(data_dir, dataset_id)
    short_id = datasets.save(
        tmp_path, frame.iloc[:300], name="short", instrument=manifest.instrument, evidence_class="synthetic", source="test", redistributable=True
    )
    short = tmp_path / "short.py"
    short.write_text(compiler.compile_strategy(templates.instantiate("ema_crossover", short_id)))
    assert execute(short, tmp_path, tmp_path / "short")["result_sha256"] == cut["result_sha256"]


def test_cli_run_until_records_the_cutoff_and_refuses_one_that_leaves_no_session(demo, tmp_path, capsys):
    data_dir = demo[0]
    dataset_id, frame = demo_dataset(demo)
    doc = tmp_path / "strategy.json"
    doc.write_text(templates.instantiate("ema_crossover", dataset_id).model_dump_json())
    command = ["run", str(doc), "--data", str(data_dir)]
    assert main([*command, "--out", str(tmp_path / "a"), "--until", str(frame.index[300].date())]) == 0
    assert json.loads((tmp_path / "a" / "record.json").read_text())["until"] == str(frame.index[300].date())
    # The EMA warm-up of 50 needs 52 sessions; a cutoff at session 51 leaves 51.
    assert main([*command, "--out", str(tmp_path / "b"), "--until", str(frame.index[51].date())]) == 1
    assert "leaves no session to trade" in capsys.readouterr().err and not (tmp_path / "b").exists()
    assert main([*command, "--out", str(tmp_path / "c"), "--until", str(frame.index[52].date())]) == 0


def test_simulation_failures_name_the_node(tmp_path):
    dataset_id = datasets.create_synthetic(tmp_path)
    doc = templates.instantiate("ema_crossover", dataset_id)
    next(n for n in doc.nodes if n.id == "backtest").params["initial_cash"] = 100.0
    strategy = tmp_path / "strategy.py"
    strategy.write_text(compiler.compile_strategy(doc))
    with pytest.raises(RunError, match=r"Node 'backtest' \(qrs.backtest@1\): Insufficient capital on"):
        execute(strategy, tmp_path, tmp_path / "out")


def wire(source: tuple[str, str], target: tuple[str, str]) -> Edge:
    return Edge(source=PortRef(node=source[0], port=source[1]), target=PortRef(node=target[0], port=target[1]))


def indicator_strategy(dataset_id: str, nodes: list[Node], entry: tuple, exit_: tuple) -> StrategyDocument:
    """bars -> the given indicator nodes -> cross entry/exit -> long/flat -> backtest."""
    doc = templates.instantiate("ema_crossover", dataset_id)
    doc.nodes = [n for n in doc.nodes if n.id in ("bars", "position", "backtest")] + nodes
    doc.nodes += [
        Node(id="up", operator="qrs.cross", version=1, params={"direction": "above"}),
        Node(id="down", operator="qrs.cross", version=1, params={"direction": "below"}),
    ]
    doc.edges = [e for e in doc.edges if e.target.node == "backtest" or e.source.node == "position"]
    doc.edges += [wire(("bars", "bars"), (n.id, "bars")) for n in nodes]
    doc.edges += [wire(entry[0], ("up", "a")), wire(entry[1], ("up", "b")), wire(exit_[0], ("down", "a")), wire(exit_[1], ("down", "b"))]
    doc.edges += [wire(("up", "signal"), ("position", "entry")), wire(("down", "signal"), ("position", "exit"))]
    return doc


@pytest.mark.parametrize(
    "name, nodes, entry, exit_, warm_up",
    [
        # MACD line crossing its signal: the signal is valid from 26 + 9 - 2 = 33, and a cross needs the session before.
        ("macd", [Node(id="m", operator="qrs.macd", version=1)], (("m", "macd"), ("m", "signal")), (("m", "macd"), ("m", "signal")), 34),
        # Price crossing back above the lower band, out when it crosses below the middle band: 19 + 1.
        (
            "bollinger",
            [Node(id="bb", operator="qrs.bollinger", version=1), Node(id="price", operator="qrs.column", version=1)],
            (("price", "series"), ("bb", "lower")),
            (("price", "series"), ("bb", "middle")),
            20,
        ),
    ],
)
def test_new_indicators_run_end_to_end_after_their_warm_up(tmp_path, name, nodes, entry, exit_, warm_up):
    dataset_id = datasets.create_synthetic(tmp_path)
    doc = indicator_strategy(dataset_id, nodes, entry, exit_)
    assert graph.validate(doc) == [] and graph.effective_warm_up(doc)["backtest"] == warm_up
    strategy = tmp_path / "strategy.py"
    strategy.write_text(compiler.compile_for_data(doc, tmp_path))
    record = execute(strategy, tmp_path, tmp_path / "out")
    fills = pd.read_csv(tmp_path / "out/results/backtest/fills.csv", parse_dates=["session"])
    sessions = datasets.load(tmp_path, dataset_id).frame.index
    assert len(fills) > 0, f"the {name} strategy should trade"
    assert fills["session"].min() > sessions[warm_up], "a signal decided at the first valid session fills at the next open"
    assert any(key.startswith(nodes[0].operator) for key in record["operators"])
