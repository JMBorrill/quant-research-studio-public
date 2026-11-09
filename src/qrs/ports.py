"""Typed port values: what flows along an edge, and the runtime check for each port type.
Connections are allowed only between ports of the same type."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from qrs.contracts import Instrument


@dataclass(frozen=True)
class Bars:
    frame: pd.DataFrame  # open/high/low/close, indexed by session date
    instrument: Instrument
    dataset_id: str
    calendar: str | None = None  # the exchange calendar giving each session's close, if one was declared


@dataclass(frozen=True)
class BacktestResult:
    fills: pd.DataFrame
    equity: pd.DataFrame
    metrics: dict[str, Any]
    assumptions: dict[str, Any]


@dataclass(frozen=True)
class Label:
    """A training target and its horizon: the value at a session is known only at the close
    `horizon` sessions later, so it may train a model but never drive a decision."""

    values: pd.Series  # float64 on the session index
    horizon: int


@dataclass(frozen=True)
class FittedModel:
    """One walk-forward model, fitted at the close of `cutoff` on the sessions in `rows`. Each row's
    label must be known by then: the row plus `horizon` sessions is at or before the cutoff."""

    cutoff: pd.Timestamp
    rows: tuple[pd.Timestamp, ...]
    horizon: int
    features: tuple[str, ...]
    means: tuple[float, ...]  # imputation and centring, from the training rows only
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float


@dataclass(frozen=True)
class Models:
    sessions: pd.DatetimeIndex  # the session index the models were fitted on
    fitted: tuple[FittedModel, ...]  # in cutoff order


@dataclass(frozen=True)
class PortType:
    description: str
    check: Callable[[Any], bool]


def _series_of(kind: str) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, pd.Series) and isinstance(v.index, pd.DatetimeIndex) and v.dtype == kind


def _is_table(v: Any) -> bool:
    return (
        isinstance(v, pd.DataFrame)
        and isinstance(v.index, pd.DatetimeIndex)
        and len(v.columns) > 0
        and v.columns.is_unique
        and all(isinstance(c, str) and c for c in v.columns)
        and all(dtype == "float64" for dtype in v.dtypes)
    )


def _is_label(v: Any) -> bool:
    return isinstance(v, Label) and _series_of("float64")(v.values) and isinstance(v.horizon, int) and v.horizon >= 1


def _is_models(v: Any) -> bool:
    """Valid models are in cutoff order, and each trained only on rows whose label it could know."""
    if not (isinstance(v, Models) and isinstance(v.sessions, pd.DatetimeIndex) and all(isinstance(m, FittedModel) for m in v.fitted)):
        return False
    cutoffs = [m.cutoff for m in v.fitted]
    if not all(c in v.sessions for c in cutoffs) or any(a >= b for a, b in zip(cutoffs, cutoffs[1:], strict=False)):
        return False
    for m in v.fitted:
        rows = v.sessions.get_indexer(list(m.rows))
        if not (isinstance(m.horizon, int) and m.horizon >= 1 and len(rows) and (rows >= 0).all() and (rows[1:] > rows[:-1]).all()):
            return False
        if rows[-1] + m.horizon > v.sessions.get_loc(m.cutoff):
            return False
    return True


PORT_TYPES: dict[str, PortType] = {
    "bars": PortType("Daily OHLC bars for one instrument.", lambda v: isinstance(v, Bars)),
    "series": PortType("Float series on the session index; NaN during warm-up.", _series_of("float64")),
    "signal": PortType("Nullable boolean series on the session index; missing during warm-up.", _series_of("boolean")),
    "positions": PortType("Target position in contracts, decided at each session close.", _series_of("int64")),
    "backtest": PortType("Simulation ledger and metrics.", lambda v: isinstance(v, BacktestResult)),
    "table": PortType("Named float feature columns on the session index; NaN during warm-up.", _is_table),
    "label": PortType("A training target on the session index and its horizon; it can only train a model.", _is_label),
    "model": PortType("Walk-forward fitted models, each with its cutoff and training rows.", _is_models),
}
