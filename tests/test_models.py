"""Walk-forward ridge fit and predict (Stage 4 workstream 3): hand-calculated and exact rational
predictions, every model checked against an independent NumPy fit, leakage probes, and the fitted
models a run records. The ridge template's fills, signals and threshold margin are in
tests/test_templates.py, and the causality harness covers both operators."""

import json
import os
import subprocess
import sys
from datetime import date
from fractions import Fraction

import numpy as np
import pandas as pd
import pytest

from conftest import INSTRUMENT, Recording, metrics_match, ridge_lstsq, run_recorded, walk_forward_rows
from qrs import compiler, datasets, runtime, templates
from qrs.importer import import_csv
from qrs.ports import Bars, Label
from qrs.runtime import Run, RunError, execute

INDEX = pd.bdate_range("2026-01-05", periods=12, name="session")


def fit_and_predict(table: pd.DataFrame, label: Label, **params) -> tuple:
    run = Run(data_dir=None)
    models = run.op("fit", "qrs.fit_ridge", 1, {"features": table, "target": label}, params)["model"]
    return models, run.op("predict", "qrs.predict", 1, {"model": models, "features": table}, {})["series"]


def test_a_prediction_worked_by_hand():
    # Rows 0-2 (x = 1, 2, 3; y = 1, 3, 2) are known at the close of session 3 (horizon 1). Their mean
    # is 2 and variance 2/3; standardised, Σz² = 3 (one per row), so the coefficient is
    # Σz(y - ȳ) / (3 + α) = (1/s)(1 + 0 + 0) / 4 with α = 1, and at x = 4 the prediction is
    # ȳ + ((4 - 2)/s)(1/(4s)) = 2 + 2 / (4 × 2/3) = 2.75.
    index = INDEX[:4]
    table = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0]}, index=index)
    models, predictions = fit_and_predict(table, Label(pd.Series([1.0, 3.0, 2.0, np.nan], index=index), 1), min_rows=3)
    assert [m.cutoff for m in models.fitted] == [index[3]] and models.fitted[0].intercept == 2.0
    assert predictions.iloc[:3].isna().all() and metrics_match(predictions.iloc[3], 2.75)


# Two features with a gap (session 8), a target known 2 sessions later, and three refits.
X1 = [1, 2, 4, 3, 5, 7, 6, 8, None, 9, 11, 10]
X2 = [2, 1, 1, 3, 2, 4, 5, 3, 6, 4, 5, 7]
Y = [1, 3, 2, 5, 4, 6, 8, 7, 9, 8, None, None]
HORIZON, ALPHA = 2, Fraction(1, 2)


def as_float(values: list) -> list[float]:
    return [np.nan if v is None else float(v) for v in values]


TABLE = pd.DataFrame({"x1": as_float(X1), "x2": as_float(X2)}, index=INDEX)
LABEL = Label(pd.Series(as_float(Y), index=INDEX), HORIZON)


def solve(a: list[list[Fraction]], b: list[Fraction]) -> list[Fraction]:
    """Gauss-Jordan elimination in exact arithmetic (the matrices here are positive definite)."""
    rows = [[*row, rhs] for row, rhs in zip(a, b, strict=True)]
    for i in range(len(rows)):
        rows[i] = [v / rows[i][i] for v in rows[i]]
        for j in range(len(rows)):
            if j != i:
                rows[j] = [vj - rows[j][i] * vi for vj, vi in zip(rows[j], rows[i], strict=True)]
    return [row[-1] for row in rows]


def exact_predictions(window: str) -> list[Fraction | None]:
    """Each session's prediction from the latest model fitted by its close, in exact arithmetic.
    Ridge with penalty α on standardised coefficients is ridge on the centred raw features with
    penalty α × variance on each, so no square root is needed: (XcᵀXc + α diag(v)) b = Xcᵀ(y - ȳ),
    and the prediction is ȳ + Σ (x - mean) b. A missing feature takes its mean, contributing 0."""
    xs = [[Fraction(v) for v in (a, b)] if a is not None else None for a, b in zip(X1, X2, strict=True)]
    usable = [x is not None and y is not None for x, y in zip(xs, Y, strict=True)]
    out: list[Fraction | None] = [None] * len(Y)
    for cutoff, rows in walk_forward_rows(usable, HORIZON, min_rows=4, every=3, window=window):
        n = len(rows)
        means = [sum(xs[t][j] for t in rows) / n for j in range(2)]
        centred = [[xs[t][j] - means[j] for j in range(2)] for t in rows]
        variances = [sum(c[j] ** 2 for c in centred) / n for j in range(2)]
        y_mean = sum(Fraction(Y[t]) for t in rows) / n
        y_centred = [Fraction(Y[t]) - y_mean for t in rows]
        gram = [[sum(c[i] * c[j] for c in centred) + (ALPHA * variances[i] if i == j else 0) for j in range(2)] for i in range(2)]
        b = solve(gram, [sum(c[i] * y for c, y in zip(centred, y_centred, strict=True)) for i in range(2)])
        for session in range(cutoff, len(Y)):
            x = [Fraction(v) if v is not None else means[j] for j, v in enumerate((X1[session], X2[session]))]
            out[session] = y_mean + sum((x[j] - means[j]) * b[j] for j in range(2))
    return out


@pytest.mark.parametrize("window", ["expanding", "rolling"])
def test_walk_forward_ridge_matches_an_exact_rational_calculation(window):
    models, predictions = fit_and_predict(TABLE, LABEL, min_rows=4, every=3, window=window, alpha=float(ALPHA))
    usable = TABLE.notna().all(axis=1) & LABEL.values.notna()
    # Cutoffs 5, 8 and 11: at 11, row 8 (a missing feature) is skipped, and rows 10 and 11 are unknown.
    expected_rows = walk_forward_rows(usable.tolist(), HORIZON, 4, 3, window)
    assert [(m.cutoff, m.rows) for m in models.fitted] == [(INDEX[c], tuple(INDEX[rows])) for c, rows in expected_rows]
    assert [c for c, _ in expected_rows] == [5, 8, 11]
    exact = exact_predictions(window)
    assert predictions.iloc[:5].isna().all() and all(e is None for e in exact[:5])
    mismatched = {s: (predictions.iloc[s], float(e)) for s, e in enumerate(exact) if e is not None and not metrics_match(predictions.iloc[s], float(e))}
    assert mismatched == {}
    # Each model against an independent least-squares fit on the rows decision 5 gives it.
    for model, (_, rows) in zip(models.fitted, expected_rows, strict=True):
        reference = ridge_lstsq(TABLE.to_numpy()[rows], LABEL.values.to_numpy()[rows], float(ALPHA))
        for ours, theirs in zip((model.means, model.scales, model.coefficients, model.intercept), reference, strict=True):
            np.testing.assert_allclose(ours, theirs, rtol=1e-9, atol=1e-12)


def test_a_constant_feature_gets_no_weight_even_when_its_value_is_inexact_in_binary():
    # 0.1 has no exact binary form, so its computed mean and deviation are off by a rounding error.
    models, _ = fit_and_predict(TABLE.assign(x2=0.1), LABEL, min_rows=4, every=3)
    assert all((m.means[1], m.scales[1], m.coefficients[1]) == (0.1, 1.0, 0.0) for m in models.fitted)
    alone, alone_predictions = fit_and_predict(TABLE[["x1"]], LABEL, min_rows=4, every=3)
    assert [m.coefficients[0] for m in models.fitted] == [m.coefficients[0] for m in alone.fitted]
    # Constant over the first model's rows (0-3), then moving: that model's predictions ignore it.
    _, predictions = fit_and_predict(TABLE.assign(x2=[0.1] * 5 + [0.2] * 7), LABEL, min_rows=4, every=3)
    pd.testing.assert_series_equal(predictions.iloc[5:8], alone_predictions.iloc[5:8])


def test_fit_refuses_infinite_features_or_targets():
    infinite = TABLE.copy()
    infinite.iloc[3, 0] = np.inf
    with pytest.raises(RunError, match="'x1' is infinite on 2026-01-08; features and targets must be finite or missing"):
        fit_and_predict(infinite, LABEL, min_rows=4, every=3)
    with pytest.raises(RunError, match="'target' is infinite on 2026-01-05"):
        fit_and_predict(TABLE, Label(LABEL.values.where(LABEL.values.index != INDEX[0], -np.inf), HORIZON), min_rows=4, every=3)


def test_predict_uses_the_latest_model_fitted_by_each_close_and_imputes_training_means():
    models, predictions = fit_and_predict(TABLE, LABEL, min_rows=4, every=3)
    for session in range(len(INDEX)):
        model = [m for m in models.fitted if m.cutoff <= INDEX[session]][-1] if session >= 5 else None
        if model is None:
            assert np.isnan(predictions.iloc[session])
            continue
        x = TABLE.iloc[session].fillna(pd.Series(model.means, index=model.features))  # session 8 lacks x1
        expected = model.intercept + sum((x.iloc[j] - model.means[j]) / model.scales[j] * model.coefficients[j] for j in range(2))
        assert metrics_match(predictions.iloc[session], expected), session


def test_predict_refuses_features_other_than_those_fitted():
    models, _ = fit_and_predict(TABLE, LABEL, min_rows=4, every=3)
    renamed = TABLE.rename(columns={"x2": "other"})
    with pytest.raises(RunError, match=r"fitted on features \['x1', 'x2'\], but this table has \['x1', 'other'\]"):
        Run(data_dir=None).op("predict", "qrs.predict", 1, {"model": models, "features": renamed}, {})


# Leakage probes.


def test_outliers_after_a_cutoff_leave_its_model_and_earlier_predictions_unchanged():
    """Train-only preprocessing and purging: features after a cutoff, and targets not yet known at
    it, replaced by huge outliers change no model fitted by then, nor any prediction up to it."""
    bars = Bars(datasets.synthetic_bars(seed=3, sessions=200), INSTRUMENT, "sha256:" + "0" * 64)
    run = Run(data_dir=None)
    change = run.op("change", "qrs.returns", 1, {"bars": bars}, {"period": 5})["series"]
    table = run.op("features", "qrs.table", 1, {"series": change}, {"name": "change"})["table"].assign(level=bars.frame["close"])
    label = run.op("target", "qrs.forward_return", 1, {"bars": bars}, {"horizon": 5})["label"]
    models, predictions = fit_and_predict(table, label, min_rows=30, every=15)
    for i, model in enumerate(models.fitted):
        cut = table.index.get_loc(model.cutoff)
        features, targets = table.copy(), label.values.copy()
        features.iloc[cut + 1 :] = 1e9
        targets.iloc[cut - label.horizon + 1 :] = 1e9  # targets of rows after cut - horizon are not known at cut
        altered, altered_predictions = fit_and_predict(features, Label(targets, label.horizon), min_rows=30, every=15)
        assert altered.fitted[: i + 1] == models.fitted[: i + 1]
        pd.testing.assert_series_equal(altered_predictions.iloc[: cut + 1], predictions.iloc[: cut + 1])
        assert altered.fitted[i + 1 :] != models.fitted[i + 1 :] or i == len(models.fitted) - 1  # yet they reach the next model


def ridge_dataset(data_dir) -> str:
    declaration = templates.load("ridge_forecast").fixture.declaration
    return import_csv(data_dir, (templates.TEMPLATE_DIR / "ridge_forecast.csv").read_bytes(), declaration)


def ridge_strategy(data_dir, dataset_id: str, until: date | None = None):
    strategy = data_dir / f"strategy-{dataset_id[7:19]}.py"
    strategy.write_text(compiler.compile_for_data(templates.instantiate("ridge_forecast", dataset_id), data_dir, until))
    return strategy


def ridge_run(data_dir, until: date | None = None) -> tuple[dict, Recording]:
    """The ridge template on its fixture: its run record (in full/ or until/), and every node's outputs."""
    strategy = ridge_strategy(data_dir, ridge_dataset(data_dir), until)
    return execute(strategy, data_dir, data_dir / ("until" if until else "full"), until), run_recorded(strategy, data_dir)


def test_rewriting_prices_after_each_cutoff_leaves_the_ridge_templates_models_and_decisions_unchanged(tmp_path):
    _, original = ridge_run(tmp_path)
    bars = original.outputs["bars"]["bars"]
    fitted = original.outputs["fit"]["model"].fitted
    for model in fitted:
        cut = bars.frame.index.get_loc(model.cutoff)
        frame = bars.frame.copy()
        frame.iloc[cut + 1 :] = (frame.iloc[cut + 1 :] * 1.3 / 0.25).round() * 0.25
        rewritten = datasets.save(
            tmp_path, frame, name="rewritten", instrument=bars.instrument, evidence_class="synthetic", source="test", redistributable=True
        )
        changed = run_recorded(ridge_strategy(tmp_path, rewritten), tmp_path)
        assert [m for m in changed.outputs["fit"]["model"].fitted if m.cutoff <= model.cutoff] == [m for m in fitted if m.cutoff <= model.cutoff]
        for node, port in [("predict", "series"), ("entry", "signal"), ("position", "positions")]:
            pd.testing.assert_series_equal(changed.outputs[node][port].iloc[: cut + 1], original.outputs[node][port].iloc[: cut + 1])
    assert len(fitted) == 7


def test_a_run_cutoff_keeps_exactly_the_models_fitted_before_it(tmp_path):
    full, _ = ridge_run(tmp_path)
    until = date(2026, 8, 3)
    cut, _ = ridge_run(tmp_path, until)
    models = {name: json.loads((tmp_path / name / "models" / "fit.json").read_text()) for name in ("full", "until")}
    assert cut["until"] == "2026-08-03" and models["until"] and all(m["cutoff"] < "2026-08-03" for m in models["until"])
    assert models["until"] == [m for m in models["full"] if m["cutoff"] < "2026-08-03"]
    assert len(models["until"]) < len(models["full"])


def test_a_run_records_every_fitted_model_with_its_training_provenance(tmp_path):
    record, recording = ridge_run(tmp_path)
    path = tmp_path / "full" / "models" / "fit.json"
    assert record["outputs"]["models/fit.json"] == runtime.sha256_file(path)
    saved = json.loads(path.read_text())
    fitted = recording.outputs["fit"]["model"].fitted
    assert [m["cutoff"] for m in saved] == [str(m.cutoff.date()) for m in fitted]
    for entry, model in zip(saved, fitted, strict=True):
        assert set(entry) == {"cutoff", "rows", "horizon", "features", "means", "scales", "coefficients", "intercept"}
        assert entry["rows"] == [str(r.date()) for r in model.rows] and entry["features"] == ["change", "rsi", "macd_histogram"]
        saved_numbers = (entry["horizon"], tuple(entry["means"]), tuple(entry["scales"]), tuple(entry["coefficients"]), entry["intercept"])
        assert saved_numbers == (model.horizon, model.means, model.scales, model.coefficients, model.intercept)
    # Results and comparisons read backtests only.
    assert runtime.backtest_nodes(record) == ["backtest"]


def test_a_model_runs_result_hash_does_not_depend_on_the_machines_blas(tmp_path):
    """Models are hashed in the record, so they must not depend on the BLAS kernels a machine runs:
    the ridge template run under OpenBLAS's kernels for older x86 CPUs gives the same result hash.
    (NumPy on macOS uses Accelerate, where the variable has no effect.)"""
    record, _ = ridge_run(tmp_path)
    document = tmp_path / "strategy.json"
    document.write_text(templates.instantiate("ridge_forecast", ridge_dataset(tmp_path)).model_dump_json())
    for kernel in ("Prescott", "Sandybridge"):
        command = [sys.executable, "-m", "qrs.cli", "run", str(document), "--data", str(tmp_path), "--out", str(tmp_path / kernel)]
        done = subprocess.run(command, env={**os.environ, "OPENBLAS_CORETYPE": kernel}, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        assert json.loads((tmp_path / kernel / "record.json").read_text())["result_sha256"] == record["result_sha256"], kernel
