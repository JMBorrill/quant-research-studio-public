import importlib.util
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qrs import datasets
from qrs.api import create_app
from qrs.contracts import Instrument
from qrs.ports import Bars
from qrs.runtime import Run

INSTRUMENT = Instrument(symbol="GLD", currency="USD", multiplier=50, tick_size=0.25, margin_init=0.10, margin_maint=0.05, expiry=date(2030, 12, 31))

# Six sessions used by the hand-calculated cases (Friday 9th to Monday 12th skips the weekend).
SESSIONS = pd.DatetimeIndex(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12"], name="session")
OPENS = [100.00, 101.00, 102.00, 104.00, 103.00, 105.00]
CLOSES = [100.50, 102.00, 104.00, 103.00, 105.00, 105.50]


def ohlc(opens, closes, index=SESSIONS) -> pd.DataFrame:
    frame = pd.DataFrame({"open": opens, "close": closes}, index=index, dtype=float)
    frame["high"] = frame[["open", "close"]].max(axis=1) + 0.5
    frame["low"] = frame[["open", "close"]].min(axis=1) - 0.5
    return frame[["open", "high", "low", "close"]]


@pytest.fixture
def golden_bars() -> Bars:
    return Bars(frame=ohlc(OPENS, CLOSES), instrument=INSTRUMENT, dataset_id="sha256:" + "0" * 64)


IMPORT_DECLARATION = {
    "name": "imported",
    "columns": {"date": "Date", "open": "Open", "high": "High", "low": "Low", "close": "Close"},
    "date_format": "%Y-%m-%d",
    "price_unit": "currency",
    "calendar": None,
    "instrument": INSTRUMENT.model_dump(mode="json"),
    "evidence_class": "retrospective",
    "source": "tests",
    "redistributable": True,
}


def bars_csv(frame: pd.DataFrame, price_unit: str = "currency") -> bytes:
    """Bars as a CSV file with Date, Open, High, Low and Close columns, prices in currency or cents."""
    prices = frame[["open", "high", "low", "close"]]
    text = (prices * 100).round().astype(int).astype(str) if price_unit == "cents" else prices.map(lambda p: f"{p:.2f}")
    text = text.rename(columns=str.title).set_axis(frame.index.strftime("%Y-%m-%d").rename("Date"))
    return text.to_csv(lineterminator="\n").encode()


def cash_flow_ledger(bars: Bars, fills: pd.DataFrame, initial_cash: float) -> pd.DataFrame:
    """Each session's position and equity, recomputed without the simulator: cash flows (pay price *
    quantity * multiplier + commission on buys, receive on sells) plus the position's value at the close."""
    multiplier = bars.instrument.multiplier
    cash, held, rows = initial_cash, 0, []
    for session, close in bars.frame["close"].items():
        for f in fills[fills["session"] == session].itertuples():
            sign = 1 if f.side == "BUY" else -1
            cash -= sign * f.quantity * f.price * multiplier + f.commission
            held += sign * f.quantity
        rows.append({"position": held, "equity": cash + held * close * multiplier})
    return pd.DataFrame(rows, index=bars.frame.index)


def metrics_match(a: float, b: float) -> bool:
    """Decision 6: two metric values from different platforms match within a relative 1e-9, or within
    1e-6 in the metric's own units, so near-zero values stay comparable."""
    return abs(a - b) <= max(1e-9 * max(abs(a), abs(b)), 1e-6)


def walk_forward_rows(usable: list[bool], horizon: int, min_rows: int, every: int, window: str = "expanding") -> list[tuple[int, list[int]]]:
    """Each walk-forward model's cutoff and training rows, as Stage 4 decision 5 states them: at the
    close of session c, the usable rows t (complete features, a target) with t + horizon <= c. The
    first cutoff is the first with min_rows of them, then one every `every` sessions; a rolling window
    keeps the latest min_rows."""
    models, next_fit = [], 0
    for cutoff in range(len(usable)):
        rows = [t for t in range(len(usable)) if usable[t] and t + horizon <= cutoff]
        if cutoff >= next_fit and len(rows) >= min_rows:
            models.append((cutoff, rows[-min_rows:] if window == "rolling" else rows))
            next_fit = cutoff + every
    return models


def ridge_lstsq(x: np.ndarray, y: np.ndarray, alpha: float) -> tuple[np.ndarray, ...]:
    """An independent ridge fit: means, scales, coefficients and intercept. Features are standardised
    by their means and population deviations (one constant over the rows is centred on its value,
    with scale 1), and [Z; √α I] b = [y - ȳ; 0] is solved by least squares, not by its normal
    equations."""
    means, scales = x.mean(axis=0), x.std(axis=0)
    constant = np.ptp(x, axis=0) == 0
    means[constant], scales[constant] = x[0, constant], 1.0
    z = (x - means) / scales
    augmented = np.vstack([z, np.sqrt(alpha) * np.eye(z.shape[1])])
    coefficients = np.linalg.lstsq(augmented, np.r_[y - y.mean(), np.zeros(z.shape[1])], rcond=None)[0]
    return means, scales, coefficients, y.mean()


class Recording(Run):
    """A run that keeps every node's outputs."""

    def __init__(self, data_dir: Path, until: date | None = None):
        super().__init__(data_dir, until)
        self.outputs = {}

    def op(self, node, *args):
        self.outputs[node] = super().op(node, *args)
        return self.outputs[node]


def run_recorded(strategy: Path, data_dir: Path, until: date | None = None) -> Recording:
    """Run a compiled strategy file in this process, keeping every node's outputs."""
    spec = importlib.util.spec_from_file_location(strategy.stem, strategy)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.run(recording := Recording(data_dir, until))
    return recording


def run_bundle(bundle: bytes, where: Path) -> dict:
    """Run an exported bundle outside the studio, without this checkout on the path; its record."""
    zipfile.ZipFile(io.BytesIO(bundle)).extractall(where / "bundle")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "QRS_OPERATOR_MODULES")}
    done = subprocess.run([sys.executable, "run.py", str(where / "out")], cwd=where / "bundle", env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads((where / "out" / "record.json").read_text())


# The service, with real workers (tests/test_service.py, tests/test_comparison.py).

TESTS_DIR = Path(__file__).parent
EXTENSION_DIR = TESTS_DIR.parent / "examples" / "extension"


@pytest.fixture
def home(tmp_path, monkeypatch):
    # Workers import extension modules used by compiled strategies through PYTHONPATH.
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(TESTS_DIR), str(EXTENSION_DIR), os.environ.get("PYTHONPATH", "")]))
    monkeypatch.syspath_prepend(str(EXTENSION_DIR))
    datasets.create_synthetic(tmp_path)
    return tmp_path


def client_for(home: Path, **options) -> TestClient:
    return TestClient(create_app(home, **options), base_url="http://localhost")


def dataset_id(client) -> str:
    return client.get("/api/datasets").json()[0]["id"]


def new_strategy(client, dataset: str | None = None, template_id: str = "ema_crossover") -> dict:
    response = client.post("/api/strategies", json={"template_id": template_id, "dataset_id": dataset or dataset_id(client)})
    assert response.status_code == 200, response.text
    return response.json()


def save(client, strategy, document) -> int:
    response = client.put(f"/api/strategies/{strategy['id']}", json={"revision": strategy["revision"], "document": document})
    assert response.status_code == 200, response.text
    return response.json()["revision"]


def wait(client, run_id, until=("succeeded", "failed", "cancelled", "interrupted"), timeout=60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in until:
            return run
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not reach {until}: {run}")


def start(client, strategy, revision=None) -> dict:
    response = client.post(f"/api/strategies/{strategy['id']}/runs", json={"revision": revision or strategy["revision"]})
    assert response.status_code == 200, response.text
    return response.json()
