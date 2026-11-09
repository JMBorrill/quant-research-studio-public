"""Records: derived tables a run saves beside its backtest for research (Stage 5 decision 2)."""

from qrs.registry import Params, Temporal, operator


@operator(
    "qrs.record",
    1,
    title="Record",
    description=(
        "Saves a table with the run as records/<node>.parquet, covered by its result hash, so analyses can read the "
        "features, signals or predictions a strategy computed. It has no output and changes no trade. Warm-up: none."
    ),
    family="evaluation",
    inputs={"table": "table"},
    outputs={},
    params=Params,
    temporal=Temporal(warm_up=lambda p: 0),
)
def record(ctx, inputs, params: Params):
    ctx.keep(inputs["table"])
    return {}
