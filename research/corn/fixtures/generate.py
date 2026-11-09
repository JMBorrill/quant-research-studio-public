"""Writes the corn programme's synthetic fixtures: seeded series with the shapes, calendars and release
patterns of the real data, so the whole workflow runs offline. No licensed data is committed.

- continuous.csv: a back-adjusted (difference) continuous series of front contracts on the CBOT
  agricultural calendar, 2006 to 2024, in cents, with every contract's provenance in its declaration;
- zcz24.csv: the December 2024 contract's own, unadjusted prices from mid-2020 to the session before
  its expiry: executable, so a strategy can trade it;
- crop_progress.csv: weekly crop condition (percent good or excellent), late May to September,
  released on Mondays at 16:00 New York time, or Tuesday after a holiday; the release due on 7
  October 2013 came late, after the government shutdown;
- wasde.csv: monthly ending stocks, released at noon New York time around the 10th; the October 2013
  report was cancelled by the shutdown and the January 2019 one delayed;
- cot.csv: weekly managed-money net positions as of Tuesday, released on Friday at 15:30 New York
  time; the 2013 and 2018-19 shutdowns' backlogs came out a few days apart afterwards;
- wasde_documents.csv and answers.json: monthly report texts and a fixture provider's tone answers.

The declarations beside these (../declarations) are written too, the continuous one with its contracts.

    python research/corn/fixtures/generate.py"""

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_market_calendars
from pandas.tseries.holiday import USFederalHolidayCalendar

from qrs.extraction import render
from qrs.hashing import sha256_hex

HERE = Path(__file__).parent
DECLARATIONS = HERE.parent / "declarations"
MONTHS = {3: "H", 5: "K", 7: "N", 9: "U", 12: "Z"}
BASIS = {"H": 0.01, "K": 0.02, "N": 0.03, "U": -0.03, "Z": -0.02}  # old and new crop: the rolls net to nothing over a year
NEW_YORK = "America/New_York"
SHUTDOWNS = [(date(2013, 10, 1), date(2013, 10, 16)), (date(2018, 12, 22), date(2019, 1, 25))]
PROMPT = "Report: {title}\n\n{text}"
INSTRUMENT = {"symbol": "ZC", "currency": "USD", "multiplier": 5000, "tick_size": 0.0025, "margin_init": 0.06, "margin_maint": 0.05}


def sessions(start: str, end: str) -> pd.DatetimeIndex:
    return pandas_market_calendars.get_calendar("CBOT_Agriculture").valid_days(start, end).tz_localize(None)


def contracts(days: pd.DatetimeIndex) -> list[dict]:
    """Each front contract: its symbol and expiry (the last session before the 15th of its month), used
    up to the session before its expiry."""
    found = []
    for year in range(days[0].year, days[-1].year + 2):
        for month, code in MONTHS.items():
            before = days[days < pd.Timestamp(year, month, 15)]
            if len(before) >= 2 and before[-1] > days[0]:
                found.append({"symbol": f"ZC{code}{year % 100:02d}", "code": code, "expiry": before[-1], "last": before[-2]})
    return found


def ohlc(close: np.ndarray, rng: np.random.Generator) -> pd.DataFrame:
    """Opens near the previous close and wicks around both, all on the 0.25-cent grid."""
    opens = np.r_[close[0], close[:-1]] + np.round(rng.normal(0, 1.5, len(close)) * 4) / 4
    wick = np.round(np.abs(rng.normal(0, 2.0, len(close))) * 4) / 4
    return pd.DataFrame({"Open": opens, "High": np.maximum(opens, close) + wick, "Low": np.minimum(opens, close) - wick, "Close": close})


def write_bars(path: Path, days: pd.DatetimeIndex, frame: pd.DataFrame) -> None:
    out = frame.copy()
    out.insert(0, "Date", days.strftime("%Y-%m-%d"))
    out.to_csv(path, index=False, float_format="%.2f", lineterminator="\n")


def main() -> None:
    rng = np.random.default_rng(20261102)
    days = sessions("2006-01-03", "2024-12-31")
    # A mean-reverting log price around 450 cents, with 1.5% daily moves.
    log_price, prices = np.log(450.0), []
    for _ in days:
        log_price += 0.01 * (np.log(450.0) - log_price) + rng.normal(0, 0.015)
        prices.append(np.exp(log_price))
    spot = np.array(prices)
    raw = {c: np.round(spot * (1 + BASIS[c]) * 4) / 4 for c in BASIS}  # each contract month's own price, on the grid

    segments, start = [], 0
    for contract in contracts(sessions("2006-01-03", "2025-12-31")):  # expiries past the data's end too
        end = min(int(days.searchsorted(contract["last"], side="right")) - 1, len(days) - 1)
        if end < start:
            continue
        segments.append({**contract, "start": start, "end": end})
        start = end + 1
        if start >= len(days):
            break
    # Back-adjusted by differences: the latest contract as traded, each earlier one shifted by the gap
    # between it and the next at the roll session.
    adjustment, adjusted = 0.0, np.empty(len(days))
    for i, segment in reversed(list(enumerate(segments))):
        if i + 1 < len(segments):
            roll = segments[i + 1]["start"]
            adjustment += raw[segments[i + 1]["code"]][roll] - raw[segment["code"]][roll]
        segment["adjustment"] = adjustment
        adjusted[segment["start"] : segment["end"] + 1] = raw[segment["code"]][segment["start"] : segment["end"] + 1] + adjustment
    assert adjusted.min() > 50, "the back-adjusted series must stay positive"
    write_bars(HERE / "continuous.csv", days, ohlc(adjusted, rng))
    provenance = {
        "roll_rule": "Front contract, rolled to the next on its last trading day (the last session before the 15th of its month).",
        "adjustment": "difference",
        "contracts": [
            {
                "symbol": s["symbol"],
                "expiry": s["expiry"].date().isoformat(),
                "first_session": days[s["start"]].date().isoformat(),
                "last_session": days[s["end"]].date().isoformat(),
                "adjustment": s["adjustment"] / 100,  # in dollars, as the prices are after import
            }
            for s in segments
        ],
    }
    bars = {"columns": {"date": "Date", "open": "Open", "high": "High", "low": "Low", "close": "Close"}, "date_format": "%Y-%m-%d", "price_unit": "cents"}
    bars |= {"calendar": "CBOT_Agriculture", "evidence_class": "synthetic", "redistributable": True}
    declare(
        "continuous",
        bars
        | {
            "name": "Corn, continuous front contract (synthetic)",
            "instrument": INSTRUMENT | {"continuous": provenance},
            "source": "research/corn/fixtures/generate.py",
        },
    )

    # The December 2024 contract, as traded, from mid-2020 to the session before its expiry.
    z24 = next(s for s in segments if s["symbol"] == "ZCZ24")
    traded = sessions("2020-07-01", (z24["expiry"] - timedelta(days=1)).date().isoformat())
    traded_prices = np.round(spot[days.get_indexer(traded)] * (1 + BASIS["Z"]) * 4) / 4
    write_bars(HERE / "zcz24.csv", traded, ohlc(traded_prices, rng))
    instrument = INSTRUMENT | {"symbol": "ZCZ24", "expiry": z24["expiry"].date().isoformat()}
    declare("zcz24", bars | {"name": "Corn, December 2024 contract (synthetic)", "instrument": instrument, "source": "research/corn/fixtures/generate.py"})

    release = lambda day, hour, minute: pd.Timestamp(day).tz_localize(None).replace(hour=hour, minute=minute).tz_localize(NEW_YORK)  # noqa: E731

    def delayed(published: pd.Timestamp, weeks: int) -> pd.Timestamp:
        """A release due during a shutdown comes the given number of weeks after it ends."""
        for start, end in SHUTDOWNS:
            if start <= published.date() <= end:
                return release(end + timedelta(weeks=weeks), published.hour, published.minute)
        return published

    holidays = set(USFederalHolidayCalendar().holidays("2006-01-01", "2024-12-31").date)
    rows, condition = [], 62.0
    for year in range(2006, 2025):
        for week_end in pd.date_range(f"{year}-05-28", f"{year}-10-13", freq="W-SUN"):
            condition = float(np.clip(condition + rng.normal(0, 1.5), 35, 80))
            monday = (week_end + timedelta(days=1)).date()
            published = delayed(release(monday + timedelta(days=monday in holidays), 16, 0), 1)  # a Monday holiday moves it to Tuesday
            rows.append((week_end.date().isoformat(), published.isoformat(), round(condition, 1)))
        condition = 62.0 + rng.normal(0, 5)
    write_csv("crop_progress.csv", ["Week", "Published", "Condition"], rows)
    declare("crop_progress", observations("Crop condition, good or excellent (synthetic)", "Week", {"Condition": "percent"}))

    rows, stocks = [], 1500.0
    for month in pd.date_range("2006-01-01", "2024-12-01", freq="MS"):
        stocks = float(np.clip(stocks + rng.normal(0, 120), 600, 2600))
        if month == pd.Timestamp("2013-10-01"):
            continue  # cancelled by the 2013 shutdown; November's report came instead
        day = month + timedelta(days=9)
        published = release(day + timedelta(days=(2 if day.weekday() == 5 else 1 if day.weekday() == 6 else 0)), 12, 0)
        published = delayed(published, 2)
        rows.append((month.strftime("%Y-%m"), published.isoformat(), round(stocks)))
    write_csv("wasde.csv", ["Month", "Published", "EndingStocks"], rows)
    declare("wasde", observations("Ending stocks, monthly supply and use (synthetic)", "Month", {"EndingStocks": "million bushels"}, event_format="%Y-%m"))

    rows, net, backlog = [], 0.0, 0
    for tuesday in pd.date_range("2006-06-13", "2024-12-31", freq="W-TUE"):
        net = 0.97 * net + rng.normal(0, 25_000)
        due = release(tuesday + timedelta(days=3), 15, 30)
        published = delayed(due, 1)
        # A shutdown's backlog came out over the following weeks, a few days apart.
        backlog = backlog + 1 if published != due else 0
        published += timedelta(days=3 * backlog)
        rows.append((tuesday.date().isoformat(), published.isoformat(), round(net)))
    write_csv("cot.csv", ["AsOf", "Published", "ManagedMoneyNet"], rows)
    declare("cot", observations("Managed-money net positions (synthetic)", "AsOf", {"ManagedMoneyNet": "contracts"}))

    # Monthly report texts from 2015, and the tone a model reads in each: the fixture provider's answers.
    rows, answers = [], {}
    phrases = ["sharply lower", "lower", "unchanged", "higher", "sharply higher"]
    for month in pd.date_range("2015-01-01", "2024-12-01", freq="MS"):
        tone = round(float(np.clip(rng.normal(0, 0.5), -1, 1)), 2)
        title = f"Supply and use, {month.strftime('%B %Y')}"
        text = f"Projected ending stocks are {phrases[min(4, int((tone + 1) * 2.5))]} this month. Demand is steady."
        published = next(r[1] for r in csv_rows("wasde.csv") if r[0] == month.strftime("%Y-%m"))
        rows.append((month.strftime("%Y-%m"), published, title, text))
        answers[sha256_hex(render(PROMPT, title, text).encode())] = json.dumps({"tone": tone})
    write_csv("wasde_documents.csv", ["Id", "Published", "Title", "Body"], rows)
    (HERE / "answers.json").write_text(json.dumps(answers, indent=1, sort_keys=True) + "\n")
    documents = {
        "kind": "qrs/documents",
        "name": "Monthly supply and use reports (synthetic)",
        "evidence_class": "synthetic",
        "source": "research/corn/fixtures/generate.py",
    }
    documents |= {"columns": {"id": "Id", "published": "Published", "title": "Title", "text": "Body"}, "redistributable": True, "hosted_processing": False}
    declare("wasde_documents", documents)


def observations(name: str, event: str, values: dict, event_format: str = "%Y-%m-%d") -> dict:
    return {
        "kind": "qrs/observations",
        "name": name,
        "columns": {"event": event, "published": "Published"},
        "event_format": event_format,
        "values": values,
        "evidence_class": "synthetic",
        "source": "research/corn/fixtures/generate.py",
        "redistributable": True,
    }


def write_csv(name: str, header: list[str], rows: list[tuple]) -> None:
    pd.DataFrame(rows, columns=header).to_csv(HERE / name, index=False, lineterminator="\n")


def csv_rows(name: str) -> list[list[str]]:
    return pd.read_csv(HERE / name, dtype=str).values.tolist()


def declare(name: str, declaration: dict) -> None:
    DECLARATIONS.mkdir(exist_ok=True)
    (DECLARATIONS / f"{name}.json").write_text(json.dumps(declaration, indent=2) + "\n")


if __name__ == "__main__":
    main()
