"""Private Yahoo chart acquisition and offline profiling; no QRS execution.

Requires yfinance==1.8.0 for fetch; validation uses the standard library.
Current chart metadata may be returned. Never commit downloaded files.
"""

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

URL = "https://query2.finance.yahoo.com/v8/finance/chart/ZC=F"
PARAMS = {"period1": 1136073600, "period2": 1640995200, "interval": "1d", "events": "div,splits,capitalGains"}


def profile(body):
    chart = json.loads(body)["chart"]
    if chart.get("error") or len(chart.get("result") or []) != 1:
        raise ValueError("No unique chart result")
    data = chart["result"][0]
    timestamps = data["timestamp"]
    quote = data["indicators"]["quote"][0]
    tz = ZoneInfo(data["meta"]["exchangeTimezoneName"])
    dates = [datetime.fromtimestamp(t, tz).date().isoformat() for t in timestamps]
    if not dates or any(not "2006-01-01" <= d <= "2021-12-31" for d in dates):
        raise ValueError("Unexpected observation dates")
    fields = ("open", "high", "low", "close", "volume")
    if any(len(quote[f]) != len(dates) for f in fields):
        raise ValueError("Mismatched field lengths")
    missing, bad, off_grid, nonpositive, flat = [], [], [], [], []
    for i, day in enumerate(dates):
        prices = [quote[f][i] for f in fields[:4]]
        if any(p is None for p in prices):
            missing.append(day)
            continue
        if any(not math.isfinite(p) for p in prices):
            raise ValueError("Non-finite price")
        o, h, low, close = prices
        if low > min(o, h, close) or h < max(o, low, close):
            bad.append(day)
        if any(abs(p * 4 - round(p * 4)) > 1e-6 for p in prices):
            off_grid.append(day)
        if min(prices) <= 0:
            nonpositive.append(day)
        if h == low:
            flat.append(day)
    volumes = quote["volume"]
    return {
        "raw_sha256": hashlib.sha256(body).hexdigest(),
        "rows": len(dates),
        "first": min(dates),
        "last": max(dates),
        "timezone": str(tz),
        "duplicate_dates": len(dates) - len(set(dates)),
        "dates_sorted": dates == sorted(dates),
        "missing_price_dates": missing,
        "complete_price_rows": len(dates) - len(missing),
        "invalid_ohlc_dates": bad,
        "invalid_ohlc_by_year": dict(Counter(d[:4] for d in bad)),
        "off_quarter_cent_grid_dates": off_grid,
        "nonpositive_price_dates": nonpositive,
        "flat_bar_dates": flat,
        "missing_volume_rows": sum(v is None for v in volumes),
        "zero_volume_rows": sum(v == 0 for v in volumes),
        "invalid_volume_rows": sum(v is not None and (not math.isfinite(v) or v < 0 or v != int(v)) for v in volumes),
        "historical_oi_present": "openInterest" in quote,
        "separate_settlement_present": "settlement" in quote,
        "per_row_expiry_present": "expiry" in quote,
        "qrs_import_approved": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "validate"))
    parser.add_argument("path", type=Path, help="New private output directory for fetch; raw chart JSON for validate")
    args = parser.parse_args()
    if args.action == "validate":
        print(json.dumps(profile(args.path.read_bytes()), indent=2))
        return
    import yfinance as yf

    if yf.__version__ != "1.8.0":
        raise ValueError("Use reviewed yfinance==1.8.0")
    repo = Path(__file__).resolve().parents[4]
    if args.path.resolve().is_relative_to(repo):
        raise ValueError("Choose private storage outside the repository")
    args.path.mkdir(parents=True, exist_ok=False)
    response = yf.data.YfData().cache_get(url=URL, params=PARAMS, timeout=15)
    response.raise_for_status()
    body = response.content
    (args.path / "chart.json").write_bytes(body)
    receipt = {
        "url": URL,
        "params": PARAMS,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "yfinance": yf.__version__,
        "current_metadata_possible": True,
        "redistributable": False,
    }
    (args.path / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    (args.path / "quality.json").write_text(json.dumps(profile(body), indent=2) + "\n")


if __name__ == "__main__":
    main()
