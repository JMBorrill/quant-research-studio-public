"""Operator contracts, checked for every registered operator, built-in or extension. Outputs become
valid exactly where the declared warm-up says, never depend on later sessions, and share the session
index of their inputs. Operators run through the real runtime, so its port and alignment checks
apply.

How each output type's warm-up is checked:
- series and signal: missing before the warm-up, a value at it
- table: every row before the warm-up has a missing column, and the row at it has none
- positions: no missing-value marker (flat during warm-up), so no position is taken during it
- label: its declared warm-up is its horizon, and its last `horizon` values are missing
- model: its first cutoff falls exactly at the declared warm-up, counted after the sample label's
  horizon (graph.effective_warm_up adds a label input's warm-up)

Causality: rewriting the bars after a session, and every observation published after its close,
leaves every output up to it unchanged, except that a label may look ahead by its horizon and no
further, and only models with a cutoff up to it count. The test operators at the end show these
checks pass causal operators of each new port type and catch leaky ones."""

import dataclasses
import sys
from pathlib import Path

import pandas as pd
import pandas_market_calendars
import pytest
from pydantic import Field

from conftest import INSTRUMENT
from qrs import graph, registry
from qrs.contracts import Edge, Node, PortRef, StrategyDocument
from qrs.datasets import OBSERVATION_FIELDS, session_closes, synthetic_bars
from qrs.operators.data import ObservationsId, aggregate_values, as_of_values
from qrs.ports import PORT_TYPES, BacktestResult, Bars, FittedModel, Label, Models
from qrs.registry import Params, Temporal, operator
from qrs.runtime import Run, RunError

sys.path.insert(0, str(Path(__file__).parents[1] / "examples" / "extension"))
import qrs_roc  # noqa: E402,F401  the example extension is held to the same contract

# NYSE sessions, so the bars have each session's close, as an as-of join needs.
NYSE_SESSIONS = pandas_market_calendars.get_calendar("NYSE").valid_days("2024-01-02", "2024-12-31").tz_localize(None)[:160].rename("session")
BARS = Bars(synthetic_bars(seed=5, sessions=160).set_axis(NYSE_SESSIONS), INSTRUMENT, "sha256:" + "0" * 64, calendar="NYSE")
# Operators under tests.* are deliberately broken helpers for other tests.
OPERATORS = [op for op in registry.all_operators() if not op.id.startswith("tests.")]
HORIZON = 3  # sessions the sample label looks ahead
CUTS = [40, 80, 140]
SAMPLE_CUTOFFS = range(33, 160, 20)  # the sample models', off the CUTS
MISSING = [i >= 120 and i % 7 == 0 for i in range(160)]  # sessions whose sample table lacks a feature


class SampleRun(Run):
    """The real runtime, with every bars dataset replaced by the given bars and every observations
    dataset by the given rows, all of them, including those published after the bars' last close."""

    def __init__(self, bars: Bars, observations: pd.DataFrame):
        super().__init__(data_dir=None)
        self.bars, self.rows = bars, observations

    def dataset(self, dataset_id: str) -> Bars:
        return self.bars

    def observations(self, dataset_id: str, bars: Bars) -> pd.DataFrame:
        return self.rows


def sample_observations(bars: Bars) -> pd.DataFrame:
    """A value about every 5th session, published 30 minutes after its close and revised 3 sessions
    later, and one published an hour before the first close, so a join has a value from session 0."""
    closes = session_closes(bars)
    rows = [(closes.index[0] - pd.Timedelta(days=7), closes.iloc[0] - pd.Timedelta(hours=1), 0.0)]
    for i in range(0, len(closes), 5):
        rows.append((closes.index[i], closes.iloc[i] + pd.Timedelta(minutes=30), float(i)))
        if i + 3 < len(closes):
            rows.append((closes.index[i], closes.iloc[i + 3] + pd.Timedelta(minutes=10), i + 0.5))
    frame = pd.DataFrame(rows, columns=["event", "published", "value"]).assign(revision="", estimated=False)
    return frame[OBSERVATION_FIELDS + ["value"]]


OBSERVATIONS = sample_observations(BARS)


def rewritten_observations(rows: pd.DataFrame, cut: int) -> pd.DataFrame:
    """The same observations with every value published after the close of session `cut` changed."""
    later = rows["published"] > session_closes(BARS).iloc[cut]
    return rows.assign(value=rows["value"].where(~later, rows["value"] * 1.3 + 7))


def sample(port_type: str, bars: Bars):
    """An input of each port type, derived from the bars. All but the label are causal and valid
    from the first session, though the table's range is missing every 7th session from 120, as a
    model's features may be; the label looks HORIZON sessions ahead."""
    close = bars.frame["close"]
    if port_type == "model":
        return sample_models(bars)
    if port_type == "backtest":
        op = registry.get("qrs.backtest", 1)
        return op.run(None, {"bars": bars, "positions": sample("positions", bars)}, op.params.model_validate({}))["result"]
    return {
        "bars": bars,
        "series": close.copy(),
        "signal": (close > close.shift(1)).astype("boolean"),
        "positions": (close > close.rolling(5).mean()).astype("int64"),
        "table": pd.DataFrame({"close": close, "range": (bars.frame["high"] - bars.frame["low"]).mask(MISSING)}),
        "label": Label(close.shift(-HORIZON) - close, HORIZON),
    }[port_type]


def sample_models(bars: Bars) -> Models:
    """Models fitted at SAMPLE_CUTOFFS, each on the rows whose sample label is known by its cutoff.
    Their coefficients are not zero, so a prediction that read later features would show it."""
    table, label = sample("table", bars), sample("label", bars)
    fitted = []
    for cutoff in SAMPLE_CUTOFFS:
        rows = table.iloc[: cutoff - HORIZON + 1]
        fitted.append(
            FittedModel(
                cutoff=table.index[cutoff],
                rows=tuple(rows.index),
                horizon=HORIZON,
                features=tuple(table.columns),
                means=tuple(rows.mean()),
                scales=tuple(rows.std(ddof=0)),
                coefficients=(0.5, -0.25),
                intercept=float(label.values.loc[rows.index].mean()),
            )
        )
    return Models(table.index, tuple(fitted))


def params_for(op: registry.Operator) -> dict:
    return {name: BARS.dataset_id for name in op.dataset_params()}


def run_op(op: registry.Operator, bars: Bars, observations: pd.DataFrame = OBSERVATIONS) -> dict:
    inputs = {name: sample(port_type, bars) for name, port_type in op.inputs.items()}
    return SampleRun(bars, observations).op("node", op.id, op.version, inputs, params_for(op))


def rewritten_after(bars: Bars, cut: int) -> Bars:
    """The same bars with every price after session `cut` changed."""
    frame = bars.frame.copy()
    frame.iloc[cut + 1 :] = (frame.iloc[cut + 1 :] * 1.3 / 0.25).round() * 0.25
    return dataclasses.replace(bars, frame=frame)


def up_to(value, session) -> list:
    """What an output holds up to `session`, which rewriting later sessions must leave unchanged."""
    if isinstance(value, Bars):
        return [value.frame.loc[:session]]
    if isinstance(value, BacktestResult):
        return [value.fills[value.fills["session"] <= session], value.equity.loc[:session]]
    if isinstance(value, Label):  # a target looks `horizon` sessions ahead, and no further
        known = value.values.index.get_loc(session) - value.horizon
        return [value.values.iloc[: max(known + 1, 0)]]
    if isinstance(value, Models):
        return [[m for m in value.fitted if m.cutoff <= session]]
    return [value.loc[:session]]


def sample_warm_up(op: registry.Operator) -> int:
    """The warm-up of the op's sample inputs, combined as graph.effective_warm_up does: a label's on
    top of the longest other. Only labels (their horizon) and models (the first cutoff) have one."""
    own = {"label": HORIZON, "model": SAMPLE_CUTOFFS[0]}
    others = [own.get(t, 0) for t in op.inputs.values() if t != "label"]
    labels = [own[t] for t in op.inputs.values() if t == "label"]
    return max(others, default=0) + max(labels, default=0)


def cuts_for(op: registry.Operator) -> list[int]:
    """Where to rewrite later sessions: the fixed CUTS, plus where leaks around models and revisions
    show. For an operator taking models, that is the session before each sample model's cutoff,
    where a model used too early would differ. For one fitting models, it is each cutoff, which a
    leak beyond it would change. For an as-of join, it is the session after each CUT, between a
    sample value's release and its revision, where a revision read before it is published differs."""
    cuts = set(CUTS)
    if op.temporal.alignment == "as_of":
        cuts |= {c + 1 for c in CUTS}
    if "model" in op.inputs.values():
        cuts |= {c - 1 for c in SAMPLE_CUTOFFS}
    outputs = [name for name, kind in op.outputs.items() if kind == "model"]
    if outputs:
        fitted = run_op(op, BARS)
        cuts |= {BARS.frame.index.get_loc(m.cutoff) for name in outputs for m in fitted[name].fitted}
    return sorted(cuts)


def check_warm_up(op: registry.Operator) -> None:
    declared = op.output_warm_up(op.params.model_validate(params_for(op)))
    upstream = sample_warm_up(op)
    for name, value in run_op(op, BARS).items():
        warm_up = declared[name] + upstream
        kind = op.outputs[name]
        if kind in ("series", "signal"):
            assert value.iloc[:warm_up].isna().all() and pd.notna(value.iloc[warm_up]), f"{name}: first valid value is not at session {warm_up}"
        elif kind == "table":
            assert value.iloc[:warm_up].isna().any(axis=1).all() and value.iloc[warm_up].notna().all(), (
                f"{name}: first complete row is not at session {warm_up}"
            )
        elif kind == "positions":
            assert not value.iloc[:warm_up].any(), f"{name} takes a position during the {warm_up}-session warm-up"
        elif kind == "label":
            assert value.horizon == warm_up, f"{name}: declares a warm-up of {warm_up} but a horizon of {value.horizon}"
            assert value.values.iloc[-warm_up:].isna().all(), f"{name}: has values for its last {warm_up} sessions, which it cannot know yet"
        elif kind == "model":
            assert value.fitted and value.fitted[0].cutoff == value.sessions[warm_up], f"{name}: the first cutoff is not at session {warm_up}"


def check_causal(op: registry.Operator, cut: int) -> None:
    session = BARS.frame.index[cut]
    original, changed = run_op(op, BARS), run_op(op, rewritten_after(BARS, cut), rewritten_observations(OBSERVATIONS, cut))
    for name in op.outputs:
        for before, after in zip(up_to(original[name], session), up_to(changed[name], session), strict=True):
            if isinstance(before, pd.DataFrame):
                pd.testing.assert_frame_equal(before, after)
            elif isinstance(before, pd.Series):
                pd.testing.assert_series_equal(before, after)
            else:
                assert before == after, f"{name} changed when sessions after {session.date()} were rewritten"


@pytest.mark.parametrize("op", OPERATORS, ids=lambda op: op.key)
def test_outputs_become_valid_exactly_after_the_declared_warm_up(op):
    check_warm_up(op)


@pytest.mark.parametrize("op", OPERATORS, ids=lambda op: op.key)
def test_outputs_never_depend_on_later_sessions(op):
    for cut in cuts_for(op):
        check_causal(op, cut)


@operator(
    "tests.reindexed",
    1,
    title="Reindexed",
    description="Returns a series on a shifted index.",
    family="tests",
    inputs={"bars": "bars"},
    outputs={"series": "series"},
    params=Params,
    temporal=Temporal(warm_up=lambda p: 0),
)
def reindexed(ctx, inputs, params):
    close = inputs["bars"].frame["close"]
    return {"series": close.set_axis(close.index + pd.Timedelta(days=1))}


@operator(
    "tests.reindexed_bars",
    1,
    title="Reindexed bars",
    description="Returns its bars on a shifted index.",
    family="tests",
    inputs={"bars": "bars"},
    outputs={"bars": "bars"},
    params=Params,
    temporal=Temporal(warm_up=lambda p: 0),
)
def reindexed_bars(ctx, inputs, params):
    bars = inputs["bars"]
    return {"bars": Bars(bars.frame.set_axis(bars.frame.index + pd.Timedelta(days=1)), bars.instrument, bars.dataset_id)}


def test_the_runtime_rejects_misaligned_inputs_and_outputs():
    a = sample("series", BARS)
    with pytest.raises(RunError, match="input 'b' must share the session index of input 'a'"):
        SampleRun(BARS, OBSERVATIONS).op("cross", "qrs.cross", 1, {"a": a, "b": a.iloc[1:]}, {})
    with pytest.raises(RunError, match="output 'series' must share the session index of input 'bars'"):
        SampleRun(BARS, OBSERVATIONS).op("shifted", "tests.reindexed", 1, {"bars": BARS}, {})
    # An output named like its input is still compared with the input.
    with pytest.raises(RunError, match="output 'bars' must share the session index of input 'bars'"):
        SampleRun(BARS, OBSERVATIONS).op("shifted", "tests.reindexed_bars", 1, {"bars": BARS}, {})
    # Tables and labels carry the session index too.
    late_target = Label(sample("label", BARS).values.iloc[1:], HORIZON)
    with pytest.raises(RunError, match="input 'target' must share the session index of input 'features'"):
        SampleRun(BARS, OBSERVATIONS).op("fit", "tests.fit_mean", 1, {"features": sample("table", BARS), "target": late_target}, {})


def test_registration_accepts_only_supported_alignments():
    with pytest.raises(ValueError, match="alignment 'resample'; supported: same_index, as_of"):
        operator(
            "tests.resample",
            1,
            title="Resample",
            description="",
            family="tests",
            inputs={},
            outputs={},
            params=Params,
            temporal=Temporal(warm_up=lambda p: 0, alignment="resample"),
        )
    assert registry.get("qrs.as_of", 1).temporal.alignment == "as_of"


# Test operators for the new port types: causal ones, and leaky or misdeclared ones that each break
# one check.


class HorizonParams(Params):
    horizon: int = Field(3, ge=1)


class FitParams(Params):
    min_rows: int = Field(10, ge=1)
    every: int = Field(5, ge=1)


def bars_operator(op_id: str, outputs: dict[str, str], warm_up, params=Params, alignment="same_index"):
    return operator(
        op_id,
        1,
        title=op_id,
        description="",
        family="tests",
        inputs={"bars": "bars"},
        outputs=outputs,
        params=params,
        temporal=Temporal(warm_up=warm_up, alignment=alignment),
    )


@bars_operator("tests.table_from_bars", {"table": "table"}, lambda p: 4)
def table_from_bars(ctx, inputs, params):
    close = inputs["bars"].frame["close"]
    return {"table": pd.DataFrame({"close": close, "mean5": close.rolling(5).mean()})}


@bars_operator("tests.table_peeks", {"table": "table"}, lambda p: 0)
def table_peeks(ctx, inputs, params):
    close = inputs["bars"].frame["close"]
    return {"table": pd.DataFrame({"close": close, "next": close.shift(-1)})}


@bars_operator("tests.table_understated", {"table": "table"}, lambda p: 2)
def table_understated(ctx, inputs, params):
    return table_from_bars(ctx, inputs, params)  # complete only from session 4


@bars_operator("tests.forward_change", {"label": "label"}, lambda p: p.horizon, HorizonParams)
def forward_change(ctx, inputs, params):
    close = inputs["bars"].frame["close"]
    return {"label": Label(close.shift(-params.horizon) - close, params.horizon)}


@bars_operator("tests.label_understated", {"label": "label"}, lambda p: 1)
def label_understated(ctx, inputs, params):
    close = inputs["bars"].frame["close"]
    return {"label": Label(close.shift(-2) - close, 1)}  # looks 2 sessions ahead, claims 1


@bars_operator("tests.label_misdeclared", {"label": "label"}, lambda p: 2)
def label_misdeclared(ctx, inputs, params):
    return forward_change(ctx, inputs, HorizonParams())  # horizon 3


@bars_operator("tests.label_filled", {"label": "label"}, lambda p: 3)
def label_filled(ctx, inputs, params):
    close = inputs["bars"].frame["close"]
    return {"label": Label((close.shift(-3) - close).fillna(0.0), 3)}  # invents targets it cannot know


class ObservationParams(Params):
    dataset_id: ObservationsId


@bars_operator("tests.as_of_peeks", {"series": "series"}, lambda p: 0, ObservationParams, alignment="as_of")
def as_of_peeks(ctx, inputs, params):
    bars = inputs["bars"]
    an_hour_late = session_closes(bars) + pd.Timedelta(hours=1)  # uses values published up to an hour after each close
    return {"series": as_of_values(ctx.observations(params.dataset_id, bars), "value", an_hour_late, pd.Timedelta(days=31))}


@bars_operator("tests.as_of_revised_early", {"series": "series"}, lambda p: 0, ObservationParams, alignment="as_of")
def as_of_revised_early(ctx, inputs, params):
    bars = inputs["bars"]
    rows = ctx.observations(params.dataset_id, bars)
    final = rows.sort_values("published").groupby("event").tail(1)  # each event's last revision, whenever published
    first = rows.groupby("event")["published"].min()
    # Uses that revision from the event's first release: its value is known before it was published.
    return {"series": as_of_values(final.assign(published=final["event"].map(first)), "value", session_closes(bars), pd.Timedelta(days=31))}


@bars_operator("tests.aggregate_revised_early", {"series": "series"}, lambda p: 0, ObservationParams, alignment="as_of")
def aggregate_revised_early(ctx, inputs, params):
    bars = inputs["bars"]
    rows = ctx.observations(params.dataset_id, bars)
    final = rows.sort_values("published").groupby("event").tail(1)
    first = rows.groupby("event")["published"].min()
    # Dates each event's last revision from its first release, so the revision counts before it exists.
    revised_early = final.assign(published=final["event"].map(first))
    return {"series": aggregate_values(revised_early, "value", session_closes(bars), pd.Timedelta(days=31), "mean", 1)}


def fit_means(table: pd.DataFrame, label: Label, params: FitParams, count_unknown=False, train_unknown=False) -> Models:
    """A stand-in model (the mean target) at each cutoff, as Stage 4 decision 5 says: at the close of
    session c, on complete feature rows whose target is known by then. The two leaks the checks must
    catch, each recording only known rows so that the model port check passes:
    - `count_unknown` places the first cutoff before enough targets are known (an understated
      warm-up)
    - `train_unknown` also learns from targets not known yet"""
    complete = table.notna().all(axis=1).to_numpy()
    fitted, next_cutoff = [], 0
    for c in range(len(table)):
        known = [t for t in range(c - label.horizon + 1) if complete[t]]
        everything = [t for t in range(c + 1) if complete[t]]
        if c < next_cutoff or len(everything if count_unknown else known) < params.min_rows:
            continue
        learned = everything if train_unknown else known
        features = table.iloc[learned]
        fitted.append(
            FittedModel(
                cutoff=table.index[c],
                rows=tuple(table.index[known]),
                horizon=label.horizon,
                features=tuple(table.columns),
                means=tuple(features.mean()),
                scales=tuple(features.std(ddof=0)),
                coefficients=(0.0,) * len(table.columns),
                intercept=float(label.values.iloc[learned].mean()),
            )
        )
        next_cutoff = c + params.every
    return Models(table.index, tuple(fitted))


def fit_operator(op_id: str):
    return operator(
        op_id,
        1,
        title=op_id,
        description="",
        family="tests",
        inputs={"features": "table", "target": "label"},
        outputs={"model": "model"},
        params=FitParams,
        temporal=Temporal(warm_up=lambda p: p.min_rows - 1),
    )


@fit_operator("tests.fit_mean")
def fit_mean(ctx, inputs, params):
    return {"model": fit_means(inputs["features"], inputs["target"], params)}


@fit_operator("tests.fit_unpurged")
def fit_unpurged(ctx, inputs, params):
    return {"model": fit_means(inputs["features"], inputs["target"], params, train_unknown=True)}


@fit_operator("tests.fit_early")
def fit_early(ctx, inputs, params):
    return {"model": fit_means(inputs["features"], inputs["target"], params, count_unknown=True)}


def predictions(models: Models, table: pd.DataFrame, early: bool) -> pd.Series:
    """A stand-in prediction (the model's intercept) from the latest model whose cutoff is at or
    before each session. With `early`, after the first model, each new one is used a session before
    its cutoff: the leak the checks must catch."""
    cutoffs = [table.index.get_loc(m.cutoff) for m in models.fitted]
    out = pd.Series(float("nan"), index=table.index)
    for session in range(len(table)):
        usable = [i for i, c in enumerate(cutoffs) if c <= session or (early and i > 0 and c == session + 1)]
        if usable:
            out.iloc[session] = models.fitted[usable[-1]].intercept
    return out


def predict_operator(op_id: str):
    return operator(
        op_id,
        1,
        title=op_id,
        description="",
        family="tests",
        inputs={"model": "model", "features": "table"},
        outputs={"series": "series"},
        params=Params,
        temporal=Temporal(warm_up=lambda p: 0),
    )


@predict_operator("tests.predict_latest")
def predict_latest(ctx, inputs, params):
    return {"series": predictions(inputs["model"], inputs["features"], early=False)}


@predict_operator("tests.predict_peeks")
def predict_peeks(ctx, inputs, params):
    """qrs.predict on the next session's features."""
    predict = registry.get("qrs.predict", 1)
    return predict.run(ctx, {**inputs, "features": inputs["features"].shift(-1)}, params)


@predict_operator("tests.predict_imputes_ahead")
def predict_imputes_ahead(ctx, inputs, params):
    """qrs.predict with missing features filled by their means over every session, later ones included."""
    table, predict = inputs["features"], registry.get("qrs.predict", 1)
    return predict.run(ctx, {**inputs, "features": table.fillna(table.mean())}, params)


@predict_operator("tests.predict_early")
def predict_early(ctx, inputs, params):
    return {"series": predictions(inputs["model"], inputs["features"], early=True)}


def check_all(op: registry.Operator) -> None:
    check_warm_up(op)
    for cut in cuts_for(op):
        check_causal(op, cut)


def test_every_port_type_has_a_valid_sample():
    for port_type, spec in PORT_TYPES.items():
        assert spec.check(sample(port_type, BARS)), port_type


@pytest.mark.parametrize("op_id", ["tests.table_from_bars", "tests.forward_change", "tests.fit_mean", "tests.predict_latest"])
def test_the_checks_pass_causal_operators_of_each_new_port_type(op_id):
    check_all(registry.get(op_id, 1))


@pytest.mark.parametrize(
    "op_id",
    [
        "tests.table_peeks",
        "tests.table_understated",
        "tests.label_understated",
        "tests.label_misdeclared",
        "tests.label_filled",
        "tests.fit_unpurged",
        "tests.fit_early",
        "tests.predict_early",
        "tests.predict_peeks",
        "tests.predict_imputes_ahead",
        "tests.as_of_peeks",
        "tests.as_of_revised_early",
        "tests.aggregate_revised_early",
    ],
)
def test_the_checks_catch_leaky_or_misdeclared_operators_of_each_new_port_type(op_id):
    with pytest.raises(AssertionError):
        check_all(registry.get(op_id, 1))


def test_an_operator_that_takes_a_label_may_output_only_models():
    with pytest.raises(ValueError, match="takes a label, so every output must be a model"):
        operator(
            "tests.label_to_series",
            1,
            title="",
            description="",
            family="tests",
            inputs={"target": "label"},
            outputs={"series": "series"},
            params=Params,
            temporal=Temporal(warm_up=lambda p: 0),
        )
    assert registry.find("tests.label_to_series", 1) is None


def node(node_id: str, operator_id: str, **params) -> Node:
    return Node(id=node_id, operator=operator_id, version=1, params=params)


def wire(source: tuple[str, str], target: tuple[str, str]) -> Edge:
    return Edge(source=PortRef(node=source[0], port=source[1]), target=PortRef(node=target[0], port=target[1]))


def test_a_label_cannot_feed_a_series_input():
    doc = StrategyDocument(
        name="leak",
        nodes=[node("bars", "qrs.dataset", dataset_id=BARS.dataset_id), node("target", "tests.forward_change"), node("rule", "qrs.threshold")],
        edges=[wire(("bars", "bars"), ("target", "bars")), wire(("target", "label"), ("rule", "a"))],
    )
    assert ("rule", "Input 'a' expects series but target.label provides label.") in [(i.node, i.message) for i in graph.validate(doc)]


def test_a_fit_nodes_effective_warm_up_is_its_first_cutoff():
    # Features are complete from session 4 (a 5-session mean); targets are known 3 sessions later.
    # The 10th complete row, session 13, is known at the close of session 16: the first cutoff.
    doc = StrategyDocument(
        name="fit",
        nodes=[
            node("bars", "qrs.dataset", dataset_id=BARS.dataset_id),
            node("features", "tests.table_from_bars"),
            node("target", "tests.forward_change", horizon=3),
            node("fit", "tests.fit_mean", min_rows=10, every=5),
        ],
        edges=[
            wire(("bars", "bars"), ("features", "bars")),
            wire(("bars", "bars"), ("target", "bars")),
            wire(("features", "table"), ("fit", "features")),
            wire(("target", "label"), ("fit", "target")),
        ],
    )
    assert graph.effective_warm_up(doc)["fit"] == 4 + 3 + 10 - 1 == 16

    run = SampleRun(BARS, OBSERVATIONS)
    features = run.op("features", "tests.table_from_bars", 1, {"bars": BARS}, {})["table"]
    target = run.op("target", "tests.forward_change", 1, {"bars": BARS}, {"horizon": 3})["label"]
    models = run.op("fit", "tests.fit_mean", 1, {"features": features, "target": target}, {"min_rows": 10, "every": 5})["model"].fitted
    sessions = BARS.frame.index
    assert (models[0].cutoff, models[0].rows) == (sessions[16], tuple(sessions[4:14]))
    assert [m.cutoff for m in models[:3]] == list(sessions[[16, 21, 26]])


def test_the_model_port_refuses_training_rows_whose_targets_are_not_known_by_the_cutoff():
    good = sample_models(BARS)
    assert PORT_TYPES["model"].check(good)
    first = good.fitted[0]
    one_too_many = BARS.frame.index[: BARS.frame.index.get_loc(first.cutoff) - HORIZON + 2]  # last row + 3 > cutoff
    leaky = Models(good.sessions, (dataclasses.replace(first, rows=tuple(one_too_many)), *good.fitted[1:]))
    assert not PORT_TYPES["model"].check(leaky)
    no_horizon = Models(good.sessions, (dataclasses.replace(first, rows=(first.cutoff,), horizon=0), *good.fitted[1:]))
    assert not PORT_TYPES["model"].check(no_horizon)
    unordered = Models(good.sessions, (dataclasses.replace(first, rows=first.rows[::-1]), *good.fitted[1:]))
    assert not PORT_TYPES["model"].check(unordered)


def test_a_label_looking_further_ahead_than_the_cut_has_nothing_to_compare():
    long_label = Label(sample("label", BARS).values, 45)
    assert up_to(long_label, BARS.frame.index[40])[0].empty
