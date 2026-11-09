"""Example extension: operators registered from outside the qrs package. Rate of change outputs a
series; Candle shape outputs a feature table (Stage 4), which Merge can widen.

Make it available by putting this directory on PYTHONPATH and listing the module in
QRS_OPERATOR_MODULES, e.g.

    PYTHONPATH=examples/extension QRS_OPERATOR_MODULES=qrs_roc qrs serve

The studio palette, parameter form, validation, compiler, worker and export pick it up from the
registry; none of them change. Compiled strategies that use it import this module themselves."""

from typing import Literal

import pandas as pd
from pydantic import Field

from qrs.registry import Params, Temporal, operator


class RocParams(Params):
    period: int = Field(10, ge=1, le=1000, description="Sessions between the two prices compared.")
    column: Literal["open", "high", "low", "close"] = "close"


@operator(
    "ext.roc",
    1,
    title="Rate of change",
    description="Percentage change from `period` sessions earlier: 100 × (price / earlier price - 1). Warm-up: period sessions.",
    family="indicators",
    inputs={"bars": "bars"},
    outputs={"series": "series"},
    params=RocParams,
    temporal=Temporal(warm_up=lambda p: p.period),
)
def roc(ctx, inputs, params: RocParams):
    prices = inputs["bars"].frame[params.column]
    return {"series": 100 * (prices / prices.shift(params.period) - 1)}


@operator(
    "ext.candle",
    1,
    title="Candle shape",
    description="A feature table of each session's range (high - low) and body (close - open), in price units. No warm-up.",
    family="features",
    inputs={"bars": "bars"},
    outputs={"table": "table"},
    params=Params,
    temporal=Temporal(warm_up=lambda p: 0),
)
def candle(ctx, inputs, params):
    frame = inputs["bars"].frame
    return {"table": pd.DataFrame({"range": frame["high"] - frame["low"], "body": frame["close"] - frame["open"]})}
