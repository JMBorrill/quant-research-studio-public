# Live Yahoo result — 2026-11-04

This supersedes the earlier offline-only feasibility conclusion. The user
explicitly waived the protected-period access and automated-permission constraints
for this evaluation. No purchases or redistribution were authorized. Technical
success does not itself establish a data licence. No market prices are committed.

**Python successfully acquired ZC=F for 2006–2021. It can support preliminary
proxy-series exploration, but does not supply the validated individual contracts
needed for realistic QRS backtests or the complete corn research programme.**

## Actual results

`yfinance==1.8.0` history calls requested 2006-01-01 through exclusive 2022-01-01,
with adjustment and repair disabled, missing rows retained. ZC=F returned 4,040
daily rows, 2006-01-03–2021-12-31: OHLC, adjusted close and volume. Adjusted close
equals close on every complete row. Returned timezone is America/New_York.

Candidate expired symbols ZCH06.CBT, ZCZ21.CBT and ZCZ20.CBT failed with
YFTzMissingError; Yahoo also explicitly returned quote-not-found for the first
two. This limited probe is not an exhaustive search or proof that all expired
contracts are unavailable.

A second, bounded chart request retained the unprocessed JSON privately:

- URL: `https://query2.finance.yahoo.com/v8/finance/chart/ZC=F`
- Parameters: `period1=1136073600`, `period2=1640995200`, `interval=1d`,
  `events=div,splits,capitalGains`.
- Retrieved: `2026-11-04T16:49:56.595246+00:00`; 201,140 bytes.
- SHA-256: `910ba31bf473391fa35f29fbba4e5834604bb6541feacaa122ba87f0c4f8d171`.
- Prices/metadata remain outside Git in private local working storage. Raw bytes
  are not embedded in the report. This is a retrospective current vendor extract.

## Measured quality

| Check | Raw chart result |
| --- | --- |
| Complete OHLC rows | 4,025; 15 rows have missing prices |
| Inconsistent OHLC range | 123: 113 in 2006; 3 in 2007; 1 in 2008; 3 in 2009; 3 in 2011 |
| Off 0.25-cent grid / nonpositive prices | 0 / 0 among complete rows |
| Missing / zero volume | 15 missing, 2 actual zeros; yfinance converts missing volume to zero |
| Negative/nonintegral volume | 0 |
| Duplicate dates / order | 0; ascending |
| High equals low | 8 rows; not automatically invalid, requires context |
| Historical OI / distinct settlement / per-row expiry | Absent from returned quote fields |

OHLC inconsistency means high is below open/close/low, or low is above
open/close/high. Mixed settlement and trading-session definitions could explain
some cases, but no verified definition resolves them. Do not silently widen
ranges, substitute prices or call every inconsistent row a transcription error.

`pandas_market_calendars==5.5.0`, `CMEGlobex_Grains`, gives 4,032 expected sessions.
Eight lack valid prices: 2007-01-02, 2012-10-29, 2012-10-30, 2016-10-10,
2016-11-11, 2018-12-05, 2018-12-17, 2018-12-18. One valid-price date is outside
the calendar: 2006-11-23. These are **calendar discrepancies**, not nine proven
vendor errors: historical closures and the library's calendar need reconciliation.
Most of the 15 null-price rows align with calendar holidays, so they must not all
be counted as missing trading sessions.

Two contemporary independent spot checks from Successful Farming:
[30 December 2021](https://www.agriculture.com/markets/analysis/crops/soybeans-fall-double-digits-thursday-december-30-2021)
matches Yahoo close exactly after dollars-to-cents conversion;
[31 December 2021](https://www.agriculture.com/markets/analysis/crops/corn-soybeans-move-up-before-2022-friday-december-31-2021)
differs by one quarter-cent tick from the article's two-decimal dollar quote.
The latter is consistent with limited reporting precision but remains a mismatch.
These corroborate scale/plausibility at two dates, not whole-series accuracy,
OHLC/volume, contract mapping or official settlement identity.

## What can use it?

- **Preliminary exploration:** yes, privately, as a flagged generic corn-price
  proxy. Descriptive plots and pipeline prototyping can begin. Keep original
  missingness and quality flags; investigate 2006 before using its OHLC.
- **Statistical conclusions:** provisional only. Unknown roll dates/adjustments,
  revisions and session definitions can contaminate returns and report effects.
  Do not invent a roll ledger, infer it solely from jumps or present results as
  validated executable-contract research. No hypotheses were evaluated here.
- **Realistic QRS backtests:** no. Individual contracts, expiry/first-notice records,
  historical OI and separate settlement remain missing. The 123 OHLC inconsistencies
  also violate QRS bar checks. No import or engine relaxation was attempted.

No raw price analysis beyond acquisition quality checks was run. Current metadata
was returned/read under the new authorization; search references also displayed
current snippets. The former untouched-period claim is no longer applicable.
No 2022-onward history was requested, and no confirmatory testing occurred.

## Reproduction

`live.py` acquires only the bounded ZC=F chart, refuses output inside the repository
and requires a new directory. It preserves raw bytes and a retrieval/hash receipt.
It does not fabricate missing fields or apply yfinance's DataFrame normalization.

```sh
uv run --no-project --with yfinance==1.8.0 python research/corn/data_sources/yahoo_feasibility/live.py fetch /private/path/new-yahoo-corn
python3 research/corn/data_sources/yahoo_feasibility/live.py validate /private/path/new-yahoo-corn/chart.json
python3 -m unittest discover -s research/corn/data_sources/yahoo_feasibility -p 'test_live.py'
```

The fetch code's bounded endpoint and parameters were exercised by the retained
live request. Offline validation reproduced the above raw profile. Unit tests
exercise rejection of protected-range observations, shape mismatches and quality
flagging without repairing input. Existing 17 acquisition tests, original seven
datasets/five evidence manifest checks, Ruff and whitespace checks pass. No
application/engine changes, dependency updates or hosted CI claims.

The alternatives in the earlier comparison remain necessary for completing the
contract dataset: FinzData's $49 candidate lacks established 2006–07 coverage;
Norgate's $148.50/six-month candidate has settlement-as-close and retention limits.
Neither has been fully validated. Yahoo's successful free download does not
resolve those missing contract requirements or the source-licensing questions.
