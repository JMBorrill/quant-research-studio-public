"""Data sources: bars from a dataset, and observations joined onto the bars' sessions as of each
session's close (Stage 4 decision 9)."""

from typing import Annotated, Literal

import numpy as np
import pandas as pd
from pydantic import Field

from qrs import datasets
from qrs.registry import DATASET_REF, Params, Temporal, operator

ID_PATTERN = r"^sha256:[0-9a-f]{64}$"
DatasetId = Annotated[str, Field(pattern=ID_PATTERN, description="Content ID of an immutable dataset.", json_schema_extra={DATASET_REF: datasets.BARS})]
ObservationsId = Annotated[
    str, Field(pattern=ID_PATTERN, description="Content ID of an immutable observations dataset.", json_schema_extra={DATASET_REF: datasets.OBSERVATIONS})
]


class DatasetParams(Params):
    dataset_id: DatasetId


@operator(
    "qrs.dataset",
    1,
    title="Dataset",
    description="Daily OHLC bars from an immutable dataset, verified against its checksums.",
    inputs={},
    family="sources",
    outputs={"bars": "bars"},
    params=DatasetParams,
    temporal=Temporal(warm_up=lambda p: 0),
)
def dataset(ctx, inputs, params: DatasetParams):
    return {"bars": ctx.dataset(params.dataset_id)}


class AsOfParams(Params):
    dataset_id: ObservationsId
    column: str = Field("value", min_length=1, description="The observations' value column to join.")
    max_age_days: int = Field(31, ge=1, le=3660, description="Days after its publication that a value may still be used.")


@operator(
    "qrs.as_of",
    1,
    title="As of",
    description=(
        "Joins an observations column onto the bars' sessions. At each session's close, from the bars' exchange calendar, "
        "it takes the latest event published by then, in its latest revision published by then. Missing when nothing is "
        "published yet, or when that revision was published more than max_age_days before the close. Warm-up: none; "
        "missing until the first publication."
    ),
    family="sources",
    inputs={"bars": "bars"},
    outputs={"series": "series"},
    params=AsOfParams,
    temporal=Temporal(warm_up=lambda p: 0, alignment="as_of"),
)
def as_of(ctx, inputs, params: AsOfParams):
    bars = inputs["bars"]
    rows = ctx.observations(params.dataset_id, bars)
    columns = datasets.value_columns(rows)
    if params.column not in columns:
        raise ValueError(f"The observations have no column '{params.column}'; they have {columns}.")
    return {"series": as_of_values(rows, params.column, datasets.session_closes(bars), pd.Timedelta(days=params.max_age_days))}


def as_of_values(rows: pd.DataFrame, column: str, closes: pd.Series, max_age: pd.Timedelta) -> pd.Series:
    """At each close: the latest event published by then, in its latest revision published by then,
    or missing if nothing is published yet or that revision is older than max_age. A later revision
    of an older event never replaces a newer event, and nothing is carried forward. Of revisions of
    one event published at the same moment, the earliest row stands."""
    if rows.empty:
        return pd.Series(np.nan, index=closes.index, dtype=float)
    # As datetime64 in microseconds, UTC for publications and closes (a time-zone-aware column gives
    # timestamp objects otherwise): the unit dates are parsed in, which reaches years nanoseconds
    # cannot, such as an event in 1209.
    events, published, times = (x.to_numpy(dtype="datetime64[us]") for x in (rows["event"], rows["published"], closes))
    # Each row's rank by event, then publication, then the earlier row: the row used at a close is the
    # highest-ranked of those published by then, a running maximum in order of publication.
    rank = np.empty(len(rows), dtype=np.int64)
    rank[np.lexsort((-np.arange(len(rows)), published, events))] = np.arange(len(rows))
    by_publication = np.argsort(published, kind="stable")
    best = np.maximum.accumulate(rank[by_publication])
    known = np.searchsorted(published[by_publication], times, side="right")
    used = np.argsort(rank)[best[np.maximum(known - 1, 0)]]
    fresh = (known > 0) & (times - published[used] <= max_age.to_timedelta64())
    return pd.Series(np.where(fresh, rows[column].to_numpy(dtype=float)[used], np.nan), index=closes.index, dtype=float)


class AggregateParams(Params):
    dataset_id: ObservationsId
    column: str = Field("value", min_length=1, description="The observations' value column to aggregate.")
    window_days: int = Field(3, ge=1, le=366, description="Days before each close in which a version must have been published to count.")
    statistic: Literal["mean", "sum", "count"] = "mean"
    min_count: int = Field(1, ge=1, le=10_000, description="Fewest values needed; with fewer, the aggregate is missing.")


@operator(
    "qrs.as_of_aggregate",
    1,
    title="As-of aggregate",
    description=(
        "Aggregates an observations column at each of the bars' closes, from the bars' exchange calendar. It takes each "
        "document's latest version published by the close (each event's, for observations without documents), keeps "
        "those published within window_days before it, and returns their values' mean, sum or count; missing with "
        "fewer than min_count values, so a count or sum with none is missing, not 0. A latest version with no value "
        "counts as none, never as an earlier version's value. Warm-up: none."
    ),
    family="sources",
    inputs={"bars": "bars"},
    outputs={"series": "series"},
    params=AggregateParams,
    temporal=Temporal(warm_up=lambda p: 0, alignment="as_of"),
)
def as_of_aggregate(ctx, inputs, params: AggregateParams):
    bars = inputs["bars"]
    rows = ctx.observations(params.dataset_id, bars)
    columns = datasets.value_columns(rows)
    if params.column not in columns:
        raise ValueError(f"The observations have no column '{params.column}'; they have {columns}.")
    closes = datasets.session_closes(bars)
    window = pd.Timedelta(days=params.window_days)
    return {"series": aggregate_values(rows, params.column, closes, window, params.statistic, params.min_count)}


def aggregate_values(rows: pd.DataFrame, column: str, closes: pd.Series, window: pd.Timedelta, statistic: str, min_count: int) -> pd.Series:
    """At each close: each document's (or event's) latest version published by then, if published
    within `window` before the close; their values' statistic, or missing with fewer than
    `min_count`. A later version never changes an earlier close's value."""
    key = "document" if "document" in rows else "event"
    rows = rows.sort_values("published", kind="stable")
    values = []
    for close in closes:
        # A document's latest version by the close is in the window if any of its versions is, so
        # the window's rows suffice: the last of each document's there is its latest.
        start, end = rows["published"].searchsorted(close - window), rows["published"].searchsorted(close, side="right")
        recent = rows.iloc[start:end].drop_duplicates(key, keep="last")[column].dropna()
        if len(recent) < min_count:
            values.append(float("nan"))
        else:
            values.append({"mean": recent.mean, "sum": recent.sum, "count": recent.count}[statistic]())
    return pd.Series(values, index=closes.index, dtype=float)
