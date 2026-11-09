"""Rules turn series into signals and combine signals. A signal is a nullable boolean series that is
missing (NA) wherever any input it depends on is missing, so warm-up flows through any combination
of rules, however deeply nested. Position policies treat a missing signal as no signal. Every rule
builds its output with signal(), which applies that rule once."""

import operator as op
from typing import Literal

import pandas as pd
from pydantic import Field

from qrs.registry import Params, Temporal, operator

Comparison = Literal["<", "<=", ">", ">="]
COMPARISONS = {"<": op.lt, "<=": op.le, ">": op.gt, ">=": op.ge}


def signal(values: pd.Series, *inputs: pd.Series) -> pd.Series:
    """`values` as a signal, missing wherever any of `inputs` is missing."""
    missing = inputs[0].isna()
    for other in inputs[1:]:
        missing = missing | other.isna()
    return values.astype("boolean").mask(missing)


class CrossParams(Params):
    direction: Literal["above", "below"] = "above"


@operator(
    "qrs.cross",
    1,
    title="Cross",
    description=(
        "True on the session where A crosses B. Above: previous A <= previous B and A > B. "
        "Below: previous A >= previous B and A < B. Missing unless both sessions have valid values."
    ),
    family="rules",
    inputs={"a": "series", "b": "series"},
    outputs={"signal": "signal"},
    params=CrossParams,
    temporal=Temporal(warm_up=lambda p: 1),  # compares with the previous session
)
def cross(ctx, inputs, params: CrossParams):
    a, b = inputs["a"], inputs["b"]
    prev_a, prev_b = a.shift(1), b.shift(1)
    if params.direction == "above":
        crossed = (prev_a <= prev_b) & (a > b)
    else:
        crossed = (prev_a >= prev_b) & (a < b)
    return {"signal": signal(crossed, a, b, prev_a, prev_b)}


class CompareParams(Params):
    comparison: Comparison = ">"


@operator(
    "qrs.compare",
    1,
    title="Compare",
    description="True where A compared with B holds (<, <=, >, >=) on the same session. Missing where either is missing.",
    family="rules",
    inputs={"a": "series", "b": "series"},
    outputs={"signal": "signal"},
    params=CompareParams,
    temporal=Temporal(warm_up=lambda p: 0),
)
def compare(ctx, inputs, params: CompareParams):
    a, b = inputs["a"], inputs["b"]
    return {"signal": signal(COMPARISONS[params.comparison](a, b), a, b)}


class ThresholdParams(Params):
    comparison: Comparison = ">"
    value: float = Field(0.0, description="The constant A is compared with.")


@operator(
    "qrs.threshold",
    1,
    title="Threshold",
    description="True where A compared with a constant holds (<, <=, >, >=). Missing where A is missing.",
    family="rules",
    inputs={"a": "series"},
    outputs={"signal": "signal"},
    params=ThresholdParams,
    temporal=Temporal(warm_up=lambda p: 0),
)
def threshold(ctx, inputs, params: ThresholdParams):
    a = inputs["a"]
    return {"signal": signal(COMPARISONS[params.comparison](a, params.value), a)}


@operator(
    "qrs.all",
    1,
    title="All",
    description="True where both A and B are true. Missing where either is missing; chain nodes for more inputs.",
    family="rules",
    inputs={"a": "signal", "b": "signal"},
    outputs={"signal": "signal"},
    params=Params,
    temporal=Temporal(warm_up=lambda p: 0),
)
def all_(ctx, inputs, params):
    a, b = inputs["a"], inputs["b"]
    return {"signal": signal(a & b, a, b)}


@operator(
    "qrs.any",
    1,
    title="Any",
    description="True where A or B is true. Missing where either is missing; chain nodes for more inputs.",
    family="rules",
    inputs={"a": "signal", "b": "signal"},
    outputs={"signal": "signal"},
    params=Params,
    temporal=Temporal(warm_up=lambda p: 0),
)
def any_(ctx, inputs, params):
    a, b = inputs["a"], inputs["b"]
    return {"signal": signal(a | b, a, b)}


@operator(
    "qrs.not",
    1,
    title="Not",
    description="True where A is false. Missing where A is missing.",
    family="rules",
    inputs={"a": "signal"},
    outputs={"signal": "signal"},
    params=Params,
    temporal=Temporal(warm_up=lambda p: 0),
)
def not_(ctx, inputs, params):
    a = inputs["a"]
    return {"signal": signal(~a, a)}


class LagParams(Params):
    sessions: int = Field(1, ge=1, le=1000, description="How many sessions back.")


@operator(
    "qrs.lag",
    1,
    title="Lag",
    description="A's value from `sessions` sessions earlier, e.g. to compare a series with its own past. Warm-up: sessions.",
    family="rules",
    inputs={"a": "series"},
    outputs={"series": "series"},
    params=LagParams,
    temporal=Temporal(warm_up=lambda p: p.sessions),
)
def lag(ctx, inputs, params: LagParams):
    return {"series": inputs["a"].shift(params.sessions)}
