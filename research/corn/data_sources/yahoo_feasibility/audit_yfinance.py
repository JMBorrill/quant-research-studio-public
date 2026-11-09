"""Offline yfinance 1.8.0 boundary regression check; never download Yahoo data.

Run in an isolated environment with yfinance==1.8.0. All data calls are intercepted
before dispatch, with the HTTP transport also blocked. Candidate expired symbols
are test inputs, not assertions that Yahoo lists or retains those contracts.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import yfinance as yf
from yfinance import _http
from yfinance.data import YfData

VERSION = "1.8.0"
SYMBOLS = ("ZC=F", "ZCH06.CBT", "ZCZ21.CBT")


class Intercepted(BaseException):
    """Stop before dispatch, including the client's ordinary exception fallbacks."""


def audit():
    if yf.__version__ != VERSION:
        raise ValueError(f"Reviewed yfinance version required: {VERSION}")
    cases = []
    for symbol in SYMBOLS:
        requests = []

        def intercept(*args, calls=requests, **kwargs):
            calls.append({"url": kwargs.get("url"), "params": kwargs.get("params")})
            raise Intercepted

        with (
            patch("yfinance.cache.get_tz_cache") as cache,
            patch.object(YfData, "cache_get", side_effect=intercept),
            patch.object(YfData, "get", side_effect=intercept),
            patch.object(_http.requests.Session, "request", side_effect=Intercepted) as transport,
        ):
            cache.return_value.lookup.return_value = None
            try:
                yf.Ticker(symbol).history(
                    start="2006-01-01",
                    end="2022-01-01",  # exclusive; requested observations end in 2021
                    interval="1d",
                    auto_adjust=False,
                    back_adjust=False,
                    repair=False,
                    keepna=True,
                    actions=False,
                )
            except Intercepted:
                pass
            transport.assert_not_called()
        if len(requests) != 1 or requests[0]["params"] != {"range": "1d", "interval": "1d"}:
            raise AssertionError(f"Unexpected request path for {symbol}: {requests}")
        cases.append({"symbol": symbol, "first_intercepted_request": requests[0], "http_dispatches": 0})
    root = Path(yf.__file__).parent
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "yfinance_version": yf.__version__,
        "mode": "offline_interception_not_download",
        "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ("base.py", "scrapers/history.py", "utils.py")},
        "cases": cases,
        "acquired_rows": 0,
        "conclusion": "Explicit history dates do not bound cold-cache timezone discovery.",
    }


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
