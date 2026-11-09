"""Walk-forward ridge regression (Stage 4). Fit refits at cutoffs, each time on the rows whose targets
are known by that session's close; predict uses, at each session, the latest model fitted by its close.
Imputation and standardisation come from each model's training rows only.

Models are hashed in the run record, so they are computed without BLAS, whose results differ in the
last bits between machines: sums of products are exactly rounded (math.fsum) and the small system is
solved in plain Python floats."""

import math
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field

from qrs.ports import FittedModel, Models
from qrs.registry import Params, Temporal, operator


class FitRidgeParams(Params):
    min_rows: int = Field(60, ge=2, le=10000, description="Training rows needed for the first fit; a rolling window keeps this many.")
    every: int = Field(20, ge=1, le=1000, description="Sessions between fits.")
    window: Literal["expanding", "rolling"] = Field("expanding", description="Train on every known row, or on the latest min_rows.")
    alpha: float = Field(1.0, gt=0, le=1e6, description="Ridge penalty on the standardised coefficients.")


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Solve a·x = b by Gaussian elimination. a is symmetric positive definite (a Gram matrix plus
    α > 0 on its diagonal), so no pivoting is needed."""
    n = len(b)
    rows = [[*a[i], b[i]] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            factor = rows[j][i] / rows[i][i]
            rows[j] = [vj - factor * vi for vj, vi in zip(rows[j], rows[i], strict=True)]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (rows[i][n] - math.fsum(rows[i][k] * x[k] for k in range(i + 1, n))) / rows[i][i]
    return x


def ridge(features: pd.DataFrame, target: pd.Series, cutoff: pd.Timestamp, horizon: int, alpha: float) -> FittedModel:
    """Closed-form ridge on standardised features: coefficients = (ZᵀZ + αI)⁻¹ Zᵀ(y − ȳ), with the
    intercept ȳ unpenalised. Z uses the rows' means and population standard deviations."""
    x, y = features.to_numpy(dtype=float), target.to_numpy(dtype=float)
    means, scales = x.mean(axis=0), x.std(axis=0)
    # A feature constant over the rows is centred on its value exactly, so it gets no weight. Its
    # computed mean and deviation can be off by a rounding error, which a later change would amplify.
    constant = np.ptp(x, axis=0) == 0
    means[constant], scales[constant] = x[0, constant], 1.0
    z, centred = (x - means) / scales, y - y.mean()
    p = z.shape[1]
    gram = [[math.fsum(z[:, i] * z[:, j]) + (alpha if i == j else 0.0) for j in range(p)] for i in range(p)]
    coefficients = solve(gram, [math.fsum(z[:, i] * centred) for i in range(p)])
    return FittedModel(
        cutoff=cutoff,
        rows=tuple(features.index),
        horizon=horizon,
        features=tuple(features.columns),
        means=tuple(means.tolist()),
        scales=tuple(scales.tolist()),
        coefficients=tuple(coefficients),
        intercept=float(y.mean()),
    )


@operator(
    "qrs.fit_ridge",
    1,
    title="Fit ridge",
    description=(
        "Walk-forward ridge regression of the target on the features. At the close of each cutoff it trains on the rows with "
        "complete features whose target is known by then, so a target's horizon purges it until it is known. The first cutoff "
        "is the first session with min_rows such rows; it refits every `every` sessions. Warm-up: min_rows - 1, after the "
        "features' warm-up plus the target's horizon."
    ),
    family="models",
    inputs={"features": "table", "target": "label"},
    outputs={"model": "model"},
    params=FitRidgeParams,
    temporal=Temporal(warm_up=lambda p: p.min_rows - 1),
)
def fit_ridge(ctx, inputs, params: FitRidgeParams):
    table, label = inputs["features"], inputs["target"]
    for name, values in [*table.items(), ("target", label.values)]:
        if np.isinf(values).any():
            raise ValueError(f"'{name}' is infinite on {values.index[np.isinf(values)][0].date()}; features and targets must be finite or missing.")
    usable = np.flatnonzero(table.notna().all(axis=1).to_numpy() & label.values.notna().to_numpy())
    fitted, next_fit = [], 0
    for cutoff in range(len(table)):
        known = usable[: np.searchsorted(usable, cutoff - label.horizon, side="right")]  # row + horizon <= cutoff
        if cutoff < next_fit or len(known) < params.min_rows:
            continue
        rows = known[-params.min_rows :] if params.window == "rolling" else known
        fitted.append(ridge(table.iloc[rows], label.values.iloc[rows], table.index[cutoff], label.horizon, params.alpha))
        next_fit = cutoff + params.every
    return {"model": Models(table.index, tuple(fitted))}


@operator(
    "qrs.predict",
    1,
    title="Predict",
    description=(
        "Each session's prediction from the latest model fitted by its close, on that session's features. A missing feature "
        "takes the model's training mean. Missing before the first cutoff. Warm-up: none beyond the model's."
    ),
    family="models",
    inputs={"model": "model", "features": "table"},
    outputs={"series": "series"},
    params=Params,
    temporal=Temporal(warm_up=lambda p: 0),
)
def predict(ctx, inputs, params):
    models, table = inputs["model"], inputs["features"]
    out = np.full(len(table), np.nan)
    if models.fitted and models.fitted[0].features != tuple(table.columns):
        raise ValueError(f"The model was fitted on features {list(models.fitted[0].features)}, but this table has {list(table.columns)}.")
    cutoffs = table.index.get_indexer([m.cutoff for m in models.fitted])
    latest = np.searchsorted(cutoffs, np.arange(len(table)), side="right") - 1  # -1 before the first cutoff
    x = table.to_numpy(dtype=float)
    for i, model in enumerate(models.fitted):
        sessions = latest == i
        values = np.where(np.isnan(x[sessions]), model.means, x[sessions])
        out[sessions] = model.intercept + (((values - model.means) / model.scales) * model.coefficients).sum(axis=1)
    return {"series": pd.Series(out, index=table.index)}
