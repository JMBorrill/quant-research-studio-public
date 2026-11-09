"""Templates: their files, the fills and metrics each gives on its fixture, and an exact reference built
from each template's plain-English rules. The reference uses rational arithmetic, so it is independent of the
operators' floating-point code and a tie such as a close equal to the middle band is unambiguous. The ridge
template's reference computes its features and targets exactly, then fits each model by least squares in
NumPy; its predictions stay at least 1e-6 from the threshold, so rounding cannot change a decision.
The templates that read features (Phase 4B) are checked against their documents and the fixture
provider's answers, read from the files directly, and the exchange's closes from its calendar, not
through the importer, the extraction or the as-of operators.
App/CLI/export parity for every template is in tests/test_service.py."""

import csv
import json
from datetime import datetime, timedelta
from fractions import Fraction

import numpy as np
import pandas as pd
import pandas_market_calendars
import pytest

from conftest import Recording, metrics_match, ridge_lstsq, run_recorded, walk_forward_rows
from qrs import compiler, datasets, graph, registry, templates
from qrs.contracts import Fill, StrategyDocument
from qrs.hashing import sha256_hex
from qrs.runtime import execute

TEMPLATE_IDS = [t["id"] for t in templates.list_templates()]


def mean(values: list[Fraction]) -> Fraction:
    return sum(values, Fraction(0)) / len(values)


def sma(closes: list[Fraction], n: int) -> list[Fraction | None]:
    return [mean(closes[i - n + 1 : i + 1]) if i >= n - 1 else None for i in range(len(closes))]


def ema(closes: list[Fraction], n: int) -> list[Fraction | None]:
    """Seeded with the mean of the first n closes, then alpha × close + (1 - alpha) × previous."""
    out, alpha = [None] * len(closes), Fraction(2, n + 1)
    for i in range(n - 1, len(closes)):
        out[i] = mean(closes[:n]) if i == n - 1 else alpha * closes[i] + (1 - alpha) * out[i - 1]
    return out


def crosses(a: list, b: list, above: bool) -> list[bool | None]:
    out = [None]
    for i in range(1, len(a)):
        if None in (a[i - 1], b[i - 1], a[i], b[i]):
            out.append(None)
        elif above:
            out.append(a[i - 1] <= b[i - 1] and a[i] > b[i])
        else:
            out.append(a[i - 1] >= b[i - 1] and a[i] < b[i])
    return out


def crossover(average):
    """Entry when the fast average crosses above the slow one; exit when it crosses below."""

    def signals(closes, fast=20, slow=50):
        fast_values, slow_values = average(closes, fast), average(closes, slow)
        return {"entry": crosses(fast_values, slow_values, above=True), "exit": crosses(fast_values, slow_values, above=False)}

    return signals


def wilder_rsi(closes: list[Fraction], n: int) -> list[Fraction | None]:
    """RSI with Wilder's averages: 100 when the average loss is 0, 50 when both averages are 0."""
    gains = [max(b - a, 0) for a, b in zip(closes, closes[1:], strict=False)]
    losses = [max(a - b, 0) for a, b in zip(closes, closes[1:], strict=False)]
    out, gain, loss = [None] * len(closes), None, None
    for i in range(n, len(closes)):
        if i == n:
            gain, loss = mean(gains[:n]), mean(losses[:n])
        else:
            gain, loss = (gain * (n - 1) + gains[i - 1]) / n, (loss * (n - 1) + losses[i - 1]) / n
        out[i] = 100 - 100 / (1 + gain / loss) if loss else Fraction(50 if gain == 0 else 100)
    return out


def both(a, b):
    return None if a is None or b is None else a and b


def either(a, b):
    return None if a is None or b is None else a or b


def bollinger_rsi(closes, period=20, k=Fraction(2), rsi_period=14, oversold=30, exit_rsi=50):
    """Entry: close below the lower band (middle - k × population deviation) and RSI below
    `oversold`. Exit: close at or above the middle band, or RSI above `exit_rsi`. A close is below
    middle - kσ when middle - close > 0 and (middle - close)² > k²σ², which needs no square root."""
    rsi = wilder_rsi(closes, rsi_period)
    signals = {name: [] for name in ("below_lower", "rsi_below_30", "at_or_above_middle", "rsi_above_50", "entry", "exit")}
    for i, close in enumerate(closes):
        window = closes[i - period + 1 : i + 1] if i >= period - 1 else None
        middle = mean(window) if window else None
        below = None if window is None else middle - close > 0 and (middle - close) ** 2 > k**2 * mean([(x - middle) ** 2 for x in window])
        low_rsi = None if rsi[i] is None else rsi[i] < oversold
        at_middle = None if window is None else close >= middle
        high_rsi = None if rsi[i] is None else rsi[i] > exit_rsi
        for name, value in zip(signals, (below, low_rsi, at_middle, high_rsi, both(below, low_rsi), either(at_middle, high_rsi)), strict=True):
            signals[name].append(value)
    return signals


def macd_histogram(closes: list[Fraction], fast=12, slow=26, signal=9) -> list[Fraction | None]:
    """Line = EMA(fast) - EMA(slow), valid from session slow - 1. Signal = EMA(signal) of the line, seeded
    with the mean of its first `signal` valid values. Histogram = line - signal."""
    line = [None if s is None else f - s for f, s in zip(ema(closes, fast), ema(closes, slow), strict=True)]
    smoothed = [None] * (slow - 1) + ema(line[slow - 1 :], signal)
    return [None if s is None else value - s for value, s in zip(line, smoothed, strict=True)]


def ridge_models(closes: list[Fraction], period=5, horizon=5, min_rows=60, every=20, alpha=1.0) -> tuple[list, list[float | None]]:
    """The ridge template's walk-forward models, each (cutoff, rows, means, scales, coefficients,
    intercept), and every session's prediction from the latest model fitted by its close. Features:
    the `period`-session return, RSI 14 and the MACD histogram; target: the next `horizon` sessions'
    return."""
    change = [closes[i] / closes[i - period] - 1 if i >= period else None for i in range(len(closes))]
    return walk_forward(closes, list(zip(change, wilder_rsi(closes, 14), macd_histogram(closes), strict=True)), horizon, min_rows, every, alpha)


def walk_forward(closes: list[Fraction], features: list[tuple], horizon: int, min_rows: int, every: int, alpha: float) -> tuple[list, list[float | None]]:
    """Walk-forward ridge models on `features`, with the next `horizon` sessions' return as target,
    and every session's prediction from the latest model fitted by its close."""
    n = len(closes)
    target = [closes[t + horizon] / closes[t] - 1 if t + horizon < n else None for t in range(n)]
    usable = [None not in row and target[t] is not None for t, row in enumerate(features)]
    x, y = np.array([[np.nan if v is None else float(v) for v in row] for row in features]), np.array([np.nan if v is None else float(v) for v in target])
    models = [(cutoff, rows, *ridge_lstsq(x[rows], y[rows], alpha)) for cutoff, rows in walk_forward_rows(usable, horizon, min_rows, every)]
    predictions: list[float | None] = [None] * n
    for cutoff, _, means, scales, coefficients, intercept in models:
        for session in range(cutoff, n):
            known = np.where(np.isnan(x[session]), means, x[session])  # a missing feature takes the training mean
            predictions[session] = float(intercept + ((known - means) / scales) @ coefficients)
    return models, predictions


def ridge_forecast(closes: list[Fraction], threshold=0.01, **model) -> dict[str, list[bool | None]]:
    """Entry while the prediction is above the threshold; exit otherwise. Missing until the first model."""
    entry = [None if p is None else p > threshold for p in ridge_models(closes, **model)[1]]
    return {"entry": entry, "exit": [None if e is None else not e for e in entry]}


def session_closes(template_id: str) -> list[datetime]:
    """Each fixture session's close, in UTC, from the exchange's calendar."""
    sessions = fixture(template_id)["Date"]
    calendar = templates.load(template_id).fixture.declaration.calendar
    schedule = pandas_market_calendars.get_calendar(calendar).schedule(sessions.iloc[0], sessions.iloc[-1])
    return [close.to_pydatetime() for close in schedule["market_close"]]


def document_versions(template_id: str) -> list[dict]:
    """The fixture's document versions, in the file's (publication) order, with the values the
    fixture provider answers for each (none if it gives no valid answer) and when each could be
    used: its publication plus the declared latency. With deduplication, a copy of text another
    document published first is left out, unless its own document already has a version kept."""
    fixture_ = templates.load(template_id).fixture
    spec, columns = fixture_.extraction, fixture_.documents.columns
    answers = json.loads((templates.TEMPLATE_DIR / spec.endpoint).read_text())
    versions, first_document, kept = [], {}, set()
    with open(templates.TEMPLATE_DIR / f"{template_id}.documents.csv", newline="", encoding="utf-8") as f:
        for record in csv.DictReader(f):
            document, title, text = record[columns.id], record[columns.title], record[columns.text]
            if first_document.setdefault(" ".join(text.split()), document) == document:
                kept.add(document)
            if spec.dedupe and document not in kept:
                continue
            answer = answers.get(sha256_hex(spec.prompt.replace("{title}", title).replace("{text}", text).encode()))
            values = json.loads(answer) if isinstance(answer, str) and answer.startswith("{") else None
            if values and not all(f.minimum <= values[f.name] <= f.maximum for f in spec.fields):
                values = None  # an answer out of bounds is no answer
            usable = datetime.fromisoformat(record[columns.published]) + timedelta(seconds=spec.latency_seconds)
            versions.append({"document": document, "event": record.get(columns.event or ""), "usable": usable, "values": values})
    return versions


def latest_versions(versions: list[dict], close: datetime, key: str) -> list[dict]:
    """Each document's (or event's) latest version usable by the close."""
    latest = {}
    for version in versions:
        if version["usable"] <= close:
            latest[version[key]] = version  # versions are in publication order
    return list(latest.values())


def sentiment_means(template_id: str, window_days: int = 3, min_count: int = 1) -> list[Fraction | None]:
    """At each close: the mean sentiment of each story's latest version usable by then and usable
    within the last `window_days`; missing with fewer than `min_count` valid values."""
    versions, means = document_versions(template_id), []
    for close in session_closes(template_id):
        recent = [v for v in latest_versions(versions, close, "document") if close - v["usable"] <= timedelta(days=window_days) and v["values"]]
        means.append(mean([Fraction(str(v["values"]["sentiment"])) for v in recent]) if len(recent) >= min_count else None)
    return means


def news_trend(closes, fast=12, slow=26, signal=9, trend=200, window_days=3, min_count=1, positive_above=0):
    """Entry: MACD crossing above its signal line, the close above its `trend`-session average and
    a positive mean sentiment. Exit: MACD crossing below its signal line."""
    line = [None if s is None else f - s for f, s in zip(ema(closes, fast), ema(closes, slow), strict=True)]
    smoothed = [None] * (slow - 1) + ema(line[slow - 1 :], signal)
    average = sma(closes, trend)
    uptrend = [None if a is None else c > a for c, a in zip(closes, average, strict=True)]
    positive = [None if s is None else s > positive_above for s in sentiment_means("news_trend", window_days, min_count)]
    entry = [both(both(c, u), p) for c, u, p in zip(crosses(line, smoothed, above=True), uptrend, positive, strict=True)]
    return {"entry": entry, "exit": crosses(line, smoothed, above=False), "uptrend": uptrend, "positive": positive}


def report_features(template_id: str, max_age_days: int = 45) -> list[tuple]:
    """At each close, each extracted figure of the latest month's report usable by then, in its
    latest version usable by then; missing if that version is older than `max_age_days`."""
    versions, rows = document_versions(template_id), []
    for close in session_closes(template_id):
        usable = [v for v in versions if v["usable"] <= close]
        latest = [v for v in usable if v["event"] == max(u["event"] for u in usable)][-1] if usable else None
        fresh = latest is not None and close - latest["usable"] <= timedelta(days=max_age_days)
        rows.append(tuple(Fraction(str(latest["values"][name])) if fresh and latest["values"] else None for name in ("growth", "tone")))
    return rows


def macro_models(closes, period=5, horizon=5, min_rows=60, every=20, alpha=1.0, max_age_days=45):
    change = [closes[i] / closes[i - period] - 1 if i >= period else None for i in range(len(closes))]
    features = [(c, r, *report) for c, r, report in zip(change, wilder_rsi(closes, 14), report_features("macro_ridge", max_age_days), strict=True)]
    return walk_forward(closes, features, horizon, min_rows, every, alpha)


def macro_ridge(closes, threshold=0.01, **model) -> dict[str, list[bool | None]]:
    """Entry while the prediction is above the threshold; exit otherwise. Missing until the first model."""
    entry = [None if p is None else p > threshold for p in macro_models(closes, **model)[1]]
    return {"entry": entry, "exit": [None if e is None else not e for e in entry]}


REFERENCES = {
    "ema_crossover": crossover(ema),
    "sma_crossover": crossover(sma),
    "bollinger_rsi": bollinger_rsi,
    "ridge_forecast": ridge_forecast,
    "news_trend": news_trend,
    "macro_ridge": macro_ridge,
}

# Each fixture must give different signals under every one of these parameter changes, so a template
# built with any of them would fail test_template_signals_match_the_reference_on_every_session.
NEIGHBOURS = {
    "ema_crossover": [{"fast": 19}, {"fast": 21}, {"slow": 49}, {"slow": 51}],
    "sma_crossover": [{"fast": 19}, {"fast": 21}, {"slow": 49}, {"slow": 51}],
    "bollinger_rsi": [
        {"period": 19},
        {"period": 21},
        {"k": Fraction(19, 10)},
        {"k": Fraction(21, 10)},
        {"rsi_period": 13},
        {"rsi_period": 15},
        {"oversold": 29},
        {"oversold": 31},
        {"exit_rsi": 49},
        {"exit_rsi": 51},
    ],
    # The refit interval leaves this fixture's decisions unchanged, so its cutoffs are checked
    # model by model in test_ridge_models_match_an_independent_fit_of_the_same_rows.
    "ridge_forecast": [
        {"threshold": 0.008},
        {"threshold": 0.012},
        {"period": 4},
        {"period": 6},
        {"horizon": 4},
        {"horizon": 6},
        {"min_rows": 59},
        {"min_rows": 61},
        {"alpha": 2.0},
    ],
    # Each change alters which sessions the strategy may enter, or the sentiment signal it reads.
    "news_trend": [
        {"fast": 11},
        {"fast": 13},
        {"slow": 25},
        {"slow": 27},
        {"signal": 8},
        {"signal": 10},
        {"trend": 199},
        {"trend": 201},
        {"window_days": 2},
        {"window_days": 4},
        {"min_count": 2},
    ],
    # As for the ridge template, but its alpha leaves the decisions unchanged, so it is not listed.
    "macro_ridge": [
        {"threshold": 0.008},
        {"threshold": 0.012},
        {"period": 4},
        {"period": 6},
        {"horizon": 4},
        {"horizon": 6},
        {"min_rows": 59},
        {"min_rows": 61},
        {"max_age_days": 20},
        {"max_age_days": 30},
    ],
}


def fixture(template_id: str) -> pd.DataFrame:
    return pd.read_csv(templates.TEMPLATE_DIR / f"{template_id}.csv", dtype=str)


def closes(template_id: str) -> list[Fraction]:
    return [Fraction(price) for price in fixture(template_id)["Close"]]


def reference_fills(template_id: str, **change) -> list[Fill]:
    """Long one contract on entry, flat on exit (exit wins), each change filled at the next session's
    open ± the backtest's slippage. The final session's target has no next open, so it never fills.
    `change` alters the reference's parameters, as in NEIGHBOURS."""
    template, rows = templates.load(template_id), fixture(template_id)
    signals = REFERENCES[template_id](closes(template_id), **change)
    costs = next(node.params for node in template.strategy.nodes if node.operator == "qrs.backtest")
    instrument = template.fixture.declaration.instrument
    slip = Fraction(str(instrument.tick_size)) * costs["slippage_ticks"]
    held, fills = 0, []
    for i in range(len(rows) - 1):
        target = 0 if signals["exit"][i] else 1 if signals["entry"][i] else held
        if target != held:
            side = "BUY" if target > held else "SELL"
            price = Fraction(rows["Open"][i + 1]) + (slip if side == "BUY" else -slip)
            fill = {"session": rows["Date"][i + 1], "side": side, "quantity": 1, "price": float(price)}
            fills.append(Fill(**fill, commission=costs["commission_per_contract"], slippage=float(slip * instrument.multiplier)))
            held = target
    return fills


def fixture_strategy(template_id: str, data_dir) -> StrategyDocument:
    """The template instantiated on its fixture, imported into data_dir with any features it reads."""
    return templates.instantiate(template_id, chosen=templates.prepare(template_id, data_dir))


def run(doc: StrategyDocument, data_dir) -> tuple[pd.DataFrame, Recording]:
    """The strategy's fills, and every node's outputs."""
    strategy = data_dir / "strategy.py"
    strategy.write_text(compiler.compile_for_data(doc, data_dir))
    execute(strategy, data_dir, data_dir / "out")
    return pd.read_csv(data_dir / "out" / "results" / "backtest" / "fills.csv"), run_recorded(strategy, data_dir)


def as_signal(series: pd.Series) -> list[bool | None]:
    return [None if pd.isna(value) else bool(value) for value in series]


def test_template_files_are_complete_and_valid():
    assert TEMPLATE_IDS == ["bollinger_rsi", "ema_crossover", "macro_ridge", "news_trend", "ridge_forecast", "sma_crossover"]
    some = {datasets.BARS: "sha256:" + "0" * 64, datasets.OBSERVATIONS: "sha256:" + "1" * 64}
    for listed in templates.list_templates():
        assert set(listed) == {"id", "version", "title", "description", "required_bar", "conditions", "sha256", "datasets"}
        assert listed["datasets"] == ([datasets.BARS, datasets.OBSERVATIONS] if listed["id"] in ("macro_ridge", "news_trend") else [datasets.BARS])
        assert graph.validate(templates.instantiate(listed["id"], chosen=some)) == []
        assert fixture(listed["id"]).columns.tolist() == ["Date", "Open", "High", "Low", "Close"]
        if templates.load(listed["id"]).fixture.extraction:
            for suffix in ("documents.csv", "answers.json", "responses.jsonl"):
                assert (templates.TEMPLATE_DIR / f"{listed['id']}.{suffix}").is_file()


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_the_recorded_fills_follow_from_the_template_rules(template_id):
    assert templates.load(template_id).fixture.fills == reference_fills(template_id)


@pytest.mark.parametrize("template_id, change", [(t, change) for t, changes in NEIGHBOURS.items() for change in changes])
def test_each_fixture_tells_its_template_from_neighbouring_parameters(template_id, change):
    assert REFERENCES[template_id](closes(template_id), **change) != REFERENCES[template_id](closes(template_id))


# Each template's metrics on its fixture, as recorded on Linux x86_64. CI checks them on macOS too, so
# they hold across platforms within decision 6's tolerance. On one platform, app, CLI and exported runs
# agree exactly (tests/test_service.py).
EXPECTED_METRICS = {
    "bollinger_rsi": {
        "sessions": 74,
        "initial_cash": 100000.0,
        "final_equity": 100440.0,
        "net_pnl": 440.0,
        "gross_pnl": 500.0,
        "commission": 10.0,
        "slippage": 50.0,
        "return_pct": 0.44,
        "max_drawdown": 130.0,
        "max_drawdown_pct": 0.12964671270787106,
        "max_drawdown_sessions": 26,
        "exposure_pct": 14.864864864864865,
        "fills": 4,
        "round_trips": 2,
        "contracts_traded": 4,
        "final_position": 0,
    },
    "ema_crossover": {
        "sessions": 115,
        "initial_cash": 100000.0,
        "final_equity": 99832.5,
        "net_pnl": -167.5,
        "gross_pnl": -137.5,
        "commission": 5.0,
        "slippage": 25.0,
        "return_pct": -0.1675,
        "max_drawdown": 940.0,
        "max_drawdown_pct": 0.9327941650747972,
        "max_drawdown_sessions": 29,
        "exposure_pct": 35.65217391304348,
        "fills": 2,
        "round_trips": 1,
        "contracts_traded": 2,
        "final_position": 0,
    },
    "ridge_forecast": {
        "sessions": 220,
        "initial_cash": 100000.0,
        "final_equity": 101767.5,
        "net_pnl": 1767.5,
        "gross_pnl": 1887.5,
        "commission": 20.0,
        "slippage": 100.0,
        "return_pct": 1.7675,
        "max_drawdown": 107.5,
        "max_drawdown_pct": 0.10628568603702697,
        "max_drawdown_sessions": 36,
        "exposure_pct": 23.636363636363637,
        "fills": 8,
        "round_trips": 4,
        "contracts_traded": 8,
        "final_position": 0,
    },
    "sma_crossover": {
        "sessions": 152,
        "initial_cash": 100000.0,
        "final_equity": 99792.5,
        "net_pnl": -207.5,
        "gross_pnl": -162.5,
        "commission": 7.5,
        "slippage": 37.5,
        "return_pct": -0.2075,
        "max_drawdown": 1152.5,
        "max_drawdown_pct": 1.1459394963832061,
        "max_drawdown_sessions": 61,
        "exposure_pct": 28.289473684210524,
        "fills": 3,
        "round_trips": 1,
        "contracts_traded": 3,
        "final_position": 1,
    },
    "news_trend": {
        "sessions": 461,
        "initial_cash": 100000.0,
        "final_equity": 100455.0,
        "net_pnl": 455.0,
        "gross_pnl": 575.0,
        "commission": 20.0,
        "slippage": 100.0,
        "return_pct": 0.455,
        "max_drawdown": 177.5,
        "max_drawdown_pct": 0.1770838529455779,
        "max_drawdown_sessions": 165,
        "exposure_pct": 14.75054229934924,
        "fills": 8,
        "round_trips": 4,
        "contracts_traded": 8,
        "final_position": 0,
    },
    "macro_ridge": {
        "sessions": 374,
        "initial_cash": 100000.0,
        "final_equity": 99905.0,
        "net_pnl": -95.0,
        "gross_pnl": 175.0,
        "commission": 45.0,
        "slippage": 225.0,
        "return_pct": -0.095,
        "max_drawdown": 862.5,
        "max_drawdown_pct": 0.8625,
        "max_drawdown_sessions": 295,
        "exposure_pct": 31.283422459893046,
        "fills": 18,
        "round_trips": 9,
        "contracts_traded": 18,
        "final_position": 0,
    },
}


def test_metrics_match_within_the_adopted_tolerance_only():
    assert metrics_match(0.0, 1e-6) and not metrics_match(0.0, 2e-6)  # the absolute floor
    assert metrics_match(1e9, 1e9 + 0.5) and not metrics_match(1e9, 1e9 + 2)  # relative: within 1 of 1e9


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_every_template_reproduces_its_expected_fills_and_metrics_on_its_fixture(template_id, tmp_path):
    fills, _ = run(fixture_strategy(template_id, tmp_path), tmp_path)
    assert [Fill.model_validate(row) for row in fills.to_dict("records")] == templates.load(template_id).fixture.fills
    assert len(fills) >= 2
    metrics = json.loads((tmp_path / "out" / "results" / "backtest" / "summary.json").read_text())["metrics"]
    expected = EXPECTED_METRICS[template_id]
    assert metrics.keys() == expected.keys()
    assert {name: (metrics[name], value) for name, value in expected.items() if not metrics_match(metrics[name], value)} == {}


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_template_signals_match_the_reference_on_every_session(template_id, tmp_path):
    _, recording = run(fixture_strategy(template_id, tmp_path), tmp_path)
    position = {edge.target.port: edge.source for edge in templates.instantiate(template_id).edges if edge.target.node == "position"}
    for name, expected in REFERENCES[template_id](closes(template_id)).items():
        source = position.get(name)
        engine = recording.outputs[source.node][source.port] if source else recording.outputs[name]["signal"]
        assert as_signal(engine) == expected, name


def test_the_bollinger_rsi_fixture_covers_each_rule():
    signals = bollinger_rsi(closes("bollinger_rsi"))
    sessions = fixture("bollinger_rsi")["Date"].tolist()
    filled = {str(fill.session): fill.side for fill in templates.load("bollinger_rsi").fixture.fills}
    # A fill on session i + 1 was decided at the close of session i.
    entries, exits = ([i for i in range(len(sessions) - 1) if filled.get(sessions[i + 1]) == side] for side in ("BUY", "SELL"))
    assert any(signals["entry"][i + 1] for i in entries)  # an entry while already long adds nothing
    assert any(signals["below_lower"][i] and not signals["rsi_below_30"][i] for i in range(len(sessions)))  # not oversold enough
    assert any(signals["rsi_above_50"][i] and not signals["at_or_above_middle"][i] for i in exits)  # an exit by RSI alone
    assert any(signals["at_or_above_middle"][i] and not signals["rsi_above_50"][i] for i in exits)  # an exit by the middle band alone
    # An entry needs the close below the lower band, so below the middle band, and RSI below 30, so
    # not above 50: both exit conditions are false, and "exit wins" never has to decide.
    assert not any(entry and exit_ for entry, exit_ in zip(signals["entry"], signals["exit"], strict=True))


def test_bollinger_rsi_rules_at_their_boundaries():
    """The template's own rule nodes, wired as in the template and fed values exactly at each
    boundary: below the lower band and RSI below 30 are strict, at or above the middle band is
    inclusive, RSI above 50 is strict, and a missing RSI (warm-up) gives no signal."""
    template = templates.load("bollinger_rsi")
    nodes = {node.id: node for node in template.strategy.nodes}
    index = pd.bdate_range("2026-01-05", periods=7)
    inputs = {
        ("close", "series"): [99, 100, 99, 102, 101.75, 101.75, 99],
        ("bands", "lower"): [100] * 7,
        ("bands", "middle"): [102] * 7,
        ("rsi", "series"): [29, 29, 30, 45, 50, 50.25, float("nan")],
    }
    values = {key: pd.Series(series, index=index, dtype=float) for key, series in inputs.items()}
    for node_id in ("below_lower", "rsi_below_30", "at_or_above_middle", "rsi_above_50", "entry_signal", "exit_signal"):
        node, op = nodes[node_id], registry.get(nodes[node_id].operator, nodes[node_id].version)
        wired = {edge.target.port: values[(edge.source.node, edge.source.port)] for edge in template.strategy.edges if edge.target.node == node_id}
        values[(node_id, "signal")] = op.run(None, wired, op.params.model_validate(node.params))["signal"]
    expected = {
        "below_lower": [True, False, True, False, False, False, True],  # 2nd session: close equals the lower band
        "rsi_below_30": [True, True, False, False, False, False, None],  # 3rd session: RSI equals 30
        "at_or_above_middle": [False, False, False, True, False, False, False],  # 4th session: close equals the middle band
        "rsi_above_50": [False, False, False, False, False, True, None],  # 5th session: RSI equals 50
        "entry_signal": [True, False, False, False, False, False, None],
        "exit_signal": [False, False, False, True, False, True, None],
    }
    assert {node_id: as_signal(values[(node_id, "signal")]) for node_id in expected} == expected


def test_exit_wins_when_entry_and_exit_fire_together(tmp_path):
    """The approved Bollinger/RSI rules cannot fire both signals at once, so precedence is tested on
    the same graph with its exit threshold lowered to RSI > 25: below the lower band with RSI between
    25 and 30, both fire, and the position must stay flat. The fixture's only such session comes
    while flat, so the template's own position node is also given both signals while long."""
    doc = fixture_strategy("bollinger_rsi", tmp_path)
    next(node for node in doc.nodes if node.id == "rsi_above_50").params["value"] = 25.0
    fills, recording = run(doc, tmp_path)
    entry, exit_ = (recording.outputs[node]["signal"].fillna(False) for node in ("entry_signal", "exit_signal"))
    overlap = entry & exit_
    assert overlap.any() and (recording.outputs["position"]["positions"][overlap] == 0).all()
    assert [Fill.model_validate(row) for row in fills.to_dict("records")] == reference_fills("bollinger_rsi", exit_rsi=25)
    assert len(fills) >= 2  # entries with RSI at or below 25 still trade

    position = next(node for node in doc.nodes if node.id == "position")
    op = registry.get(position.operator, position.version)
    index = pd.bdate_range("2026-01-05", periods=3)
    entry, exit_ = (pd.Series(values, index=index, dtype="boolean") for values in ([True, True, False], [False, True, False]))
    assert op.run(None, {"entry": entry, "exit": exit_}, op.params.model_validate(position.params))["positions"].tolist() == [1, 0, 0]


def ridge_node(template_id: str, node_id: str) -> dict:
    return next(node.params for node in templates.load(template_id).strategy.nodes if node.id == node_id)


def test_ridge_models_match_an_independent_fit_of_the_same_rows(tmp_path):
    """Every model's cutoff and training rows follow decision 5, and its statistics and coefficients
    match a least-squares fit on the exactly computed features."""
    _, recording = run(fixture_strategy("ridge_forecast", tmp_path), tmp_path)
    sessions = recording.outputs["bars"]["bars"].frame.index
    fitted = recording.outputs["fit"]["model"].fitted
    reference, predictions = ridge_models(closes("ridge_forecast"))
    assert [(m.cutoff, m.rows) for m in fitted] == [(sessions[cutoff], tuple(sessions[rows])) for cutoff, rows, *_ in reference]
    for model, (_, _, means, scales, coefficients, intercept) in zip(fitted, reference, strict=True):
        for ours, theirs in [(model.means, means), (model.scales, scales), (model.coefficients, coefficients), (model.intercept, intercept)]:
            np.testing.assert_allclose(ours, theirs, rtol=1e-9, atol=1e-12)
    ours = recording.outputs["predict"]["series"]
    np.testing.assert_allclose(ours, [np.nan if p is None else p for p in predictions], rtol=1e-9, atol=1e-12)


def test_every_ridge_prediction_is_at_least_1e_6_from_the_threshold(tmp_path):
    """So a last-bit difference between platforms cannot change a trade."""
    _, recording = run(fixture_strategy("ridge_forecast", tmp_path), tmp_path)
    predictions = recording.outputs["predict"]["series"].dropna()
    threshold = ridge_node("ridge_forecast", "entry")["value"]
    assert len(predictions) == 220 - 97 and (predictions - threshold).abs().min() >= 1e-6


def test_the_ridge_template_signals_nothing_before_its_first_model(tmp_path):
    # The MACD histogram (26, 9) is complete from session 26 + 9 - 2 = 33, the 5-session target is
    # known 5 sessions later, and 60 rows are needed: the first model is fitted at the close of
    # session 33 + 5 + 60 - 1 = 97, on sessions 33 to 92.
    doc = fixture_strategy("ridge_forecast", tmp_path)
    warm_up = graph.effective_warm_up(doc)
    assert (warm_up["add_macd"], warm_up["fit"], warm_up["predict"], warm_up["backtest"]) == (33, 97, 97, 97)
    _, recording = run(doc, tmp_path)
    sessions = recording.outputs["bars"]["bars"].frame.index
    first = recording.outputs["fit"]["model"].fitted[0]
    assert (first.cutoff, first.rows) == (sessions[97], tuple(sessions[33:93]))
    predictions, entry = recording.outputs["predict"]["series"], recording.outputs["entry"]["signal"]
    assert predictions.iloc[:97].isna().all() and predictions.iloc[97:].notna().all()
    assert entry.iloc[:97].isna().all() and entry.iloc[97:].notna().all()


def test_ridge_threshold_rules_at_their_boundaries():
    """The template's entry and exit nodes fed predictions at the threshold and 1e-6 either side:
    entry is strictly above it, exit is its complement, and a missing prediction gives no signal."""
    template = templates.load("ridge_forecast")
    nodes = {node.id: node for node in template.strategy.nodes}
    threshold = nodes["entry"].params["value"]
    index = pd.bdate_range("2026-01-05", periods=4)
    prediction = pd.Series([threshold - 1e-6, threshold, threshold + 1e-6, float("nan")], index=index)
    entry_op, exit_op = (registry.get(nodes[n].operator, nodes[n].version) for n in ("entry", "exit"))
    entry = entry_op.run(None, {"a": prediction}, entry_op.params.model_validate(nodes["entry"].params))["signal"]
    exit_ = exit_op.run(None, {"a": entry}, exit_op.params.model_validate(nodes["exit"].params))["signal"]
    assert (as_signal(entry), as_signal(exit_)) == ([False, False, True, None], [True, True, False, None])


def test_the_macro_fixture_covers_late_copied_and_invalid_reports_with_predictions_clear_of_its_threshold(tmp_path):
    doc = fixture_strategy("macro_ridge", tmp_path)
    _, recording = run(doc, tmp_path)
    predictions = recording.outputs["predict"]["series"].dropna()
    assert len(predictions) > 200 and (predictions - 0.01).abs().min() >= 1e-6
    # The revised reports of April 2023 and January 2024 give tones of -1.13 and -1.17, outside -1 to
    # 1: those responses are invalid, so from their publication (25 May, 25 February) both figures are
    # missing, not the first release's, until the next month's report.
    growth = recording.outputs["growth"]["series"]
    assert growth.loc["2023-05-24"] == -3.0 and growth.loc["2023-05-25":"2023-06-09"].isna().all() and growth.loc["2023-06-12"] == -0.2
    assert growth.loc["2024-02-23"] == -3.1 and growth.loc["2024-02-26":"2024-03-08"].isna().all() and growth.loc["2024-03-11"] == -2.3
    # June's report came out at 16:30 on 10 July, after the close, and September's at 15:58 on 10
    # October, within the 5-minute latency before it: each counts from the next session.
    assert growth.loc["2023-07-10":"2023-07-11"].tolist() == [-0.2, 2.1]
    assert growth.loc["2023-10-10":"2023-10-11"].tolist() == [-1.4, -1.1]
    # A wire service's copy of November's report is dropped before extraction.
    features = next(node.params["dataset_id"] for node in doc.nodes if node.id == "growth")
    derivation = datasets.read_manifest(tmp_path, features).derivation
    assert (derivation.duplicates, derivation.requests) == (1, 24)


def test_the_news_fixture_exercises_each_rule():
    signals, sessions = news_trend(closes("news_trend")), fixture("news_trend")["Date"].tolist()
    entered = [sessions[i] for i, e in enumerate(signals["entry"]) if e]
    blocked = {sessions[i]: signals["positive"][i] for i, e in enumerate(signals["entry"]) if e is False or e is None}
    assert entered == ["2023-10-20", "2024-06-28", "2024-08-16", "2024-10-03"]
    # MACD crossed above in an uptrend on these sessions, but the news was negative (8 December, after
    # B's correction and with C's syndicated copy dropped) or missing (2 February: D came after the
    # close and E is more than 3 days old).
    cross_up = crosses_up_in_uptrend(signals, sessions)
    assert set(cross_up) - set(entered) == {"2023-12-08", "2024-02-02"}
    assert (blocked["2023-12-08"], blocked["2024-02-02"]) == (False, None)
    # Exits do not wait for news: 14 November's exit came with no story in the last 3 days.
    exit_day = sessions.index("2023-11-14")
    assert signals["exit"][exit_day] and signals["positive"][exit_day] is None


def crosses_up_in_uptrend(signals, sessions) -> list[str]:
    closes_ = closes("news_trend")
    line = [None if s is None else f - s for f, s in zip(ema(closes_, 12), ema(closes_, 26), strict=True)]
    smoothed = [None] * 25 + ema(line[25:], 9)
    return [sessions[i] for i, (c, u) in enumerate(zip(crosses(line, smoothed, above=True), signals["uptrend"], strict=True)) if c and u]
