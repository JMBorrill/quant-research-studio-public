"""Feature tables and training labels for model fitting (Stage 4). A table gathers named feature
columns; a label is a target that looks ahead, which only a fit operator may take (see qrs.ports)."""

from pydantic import Field

from qrs.operators.indicators import PriceColumn, prices, refuse_continuous, series, simple_returns
from qrs.ports import Label
from qrs.registry import Params, Temporal, operator

COLUMN_NAME = r"^[a-z][a-z0-9_]*$"


class TableParams(Params):
    name: str = Field("feature_1", pattern=COLUMN_NAME, description="The column's name, unique within its table.")


@operator(
    "qrs.table",
    1,
    title="Feature table",
    description="Starts a feature table with one named column. Add columns with Merge. Warm-up: its series'.",
    family="features",
    inputs={"series": "series"},
    outputs={"table": "table"},
    params=TableParams,
    temporal=Temporal(warm_up=lambda p: 0),
)
def table(ctx, inputs, params: TableParams):
    return {"table": inputs["series"].to_frame(params.name)}


class MergeParams(Params):
    name: str = Field("feature_2", pattern=COLUMN_NAME, description="The new column's name, unique within the table.")


@operator(
    "qrs.merge",
    1,
    title="Merge",
    description="Adds a series to a feature table as a new named column. Chain merges for more columns. Warm-up: the longer of its inputs'.",
    family="features",
    inputs={"table": "table", "series": "series"},
    outputs={"table": "table"},
    params=MergeParams,
    temporal=Temporal(warm_up=lambda p: 0),
)
def merge(ctx, inputs, params: MergeParams):
    existing = inputs["table"]
    if params.name in existing.columns:
        raise ValueError(f"The table already has a column '{params.name}'; give the new one another name.")
    return {"table": existing.assign(**{params.name: inputs["series"]})}


class ForwardReturnParams(Params):
    horizon: int = Field(1, ge=1, le=1000, description="Sessions ahead.")
    column: PriceColumn = "close"


@operator(
    "qrs.forward_return",
    1,
    title="Forward return",
    description=(
        "Training target: the return over the next `horizon` sessions, as a fraction: price `horizon` sessions "
        "later / price - 1. Known only `horizon` sessions later, so it can only train a model. Warm-up: horizon."
    ),
    family="labels",
    inputs={"bars": "bars"},
    outputs={"label": "label"},
    params=ForwardReturnParams,
    temporal=Temporal(warm_up=lambda p: p.horizon),
)
def forward_return(ctx, inputs, params: ForwardReturnParams):
    refuse_continuous(inputs["bars"])
    past = simple_returns(series(inputs, prices(inputs, params.column)), params.horizon)
    return {"label": Label(past.shift(-params.horizon), params.horizon)}
