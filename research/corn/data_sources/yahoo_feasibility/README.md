# Yahoo Finance / yfinance corn feasibility

**Superseded by the [live acquisition result](LIVE.md)** after the user waived
the two access constraints: ZC=F downloaded successfully, with material quality
and individual-contract limitations. The offline findings below remain a record
of the earlier constrained test, not the current acquisition status.

Checked 2026-11-04 for personal, non-commercial research, with no redistribution.
**Yahoo has not unblocked the programme. No Yahoo price request was dispatched,
no prices were downloaded and no usable coverage was established.** Live testing
stopped at access-permission and protected-period blockers. This is not evidence
that Yahoo has no historical corn data.

## Actual experiment

Installed `yfinance==1.8.0` in a disposable environment outside the repository
(Python 3.12.14). No QRS dependency or lockfile changed. Ran the real client's
`Ticker.history` with `start=2006-01-01`, exclusive `end=2022-01-01`, daily bars,
`auto_adjust=False`, `back_adjust=False`, `repair=False`, `keepna=True` and
`actions=False`. Intercepted data calls before dispatch and separately blocked
the HTTP transport. No Yahoo responses, prices, cookies or credentials were read.

| Input | First intercepted request parameters | Actual Yahoo result |
| --- | --- | --- |
| `ZC=F` | `range=1d`, `interval=1d` | Not requested; zero HTTP dispatches |
| `ZCH06.CBT` | `range=1d`, `interval=1d` | Not requested; symbol availability unverified |
| `ZCZ21.CBT` | `range=1d`, `interval=1d` | Not requested; symbol availability unverified |

The last two symbols are candidate syntax for March 2006 and December 2021, not
verified Yahoo listings. No live symbol search, contract-chain page or current
quote page was opened to discover symbols. The offline experiment cannot prove
expired-contract retention, an HTTP error, first/last observations or row counts.

`base.py:_fetch_ticker_tz` requests the latest day on a cold timezone cache before
the bounded historical request. Its fallback can access current `info`. Thus
explicit start/end alone do not preserve the protected period. `history.py` also
retains chart metadata alongside historical rows; date arguments do not establish
that all returned metadata are historical. Seeding a timezone or filtering a
download afterwards is not a verified solution to the no-access constraint.

`offline_result.json` records the actual intercepted calls and hashes of the
inspected installed source files. It is test evidence, not a market-data manifest.
The audit is pinned to this version and refuses other versions. Upstream source:
[base.py](https://github.com/ranaroussi/yfinance/blob/main/yfinance/base.py),
[history.py](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py),
[utils.py](https://github.com/ranaroussi/yfinance/blob/main/yfinance/utils.py).
Those upstream links move; the local receipt pins the inspected source bytes.

## Access and personal-use terms

- [yfinance documentation](https://ranaroussi.github.io/yfinance/) describes a
  research/education tool and personal-use API, but explicitly says it is not
  affiliated with or endorsed by Yahoo and refers users to Yahoo's terms. Its
  code licence is not a data licence or Yahoo's permission for this collection.
- Yahoo's [published general terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html),
  section 4(a)(9), require express prior permission for automated collection,
  including attempted collection. The indexed [older EMEA terms](https://legal.yahoo.com/ie/en/yahoo/terms/otos/previousversion-2025-01-29/index.html)
  contain the same restriction. The current EMEA/UK endpoint returned HTTP 999
  through the research tool, so current regional wording was not fully verified.
  No exception or permission covering this automated research was established.
  This is an unresolved permission check, not a legal ruling about personal use.
- [Yahoo's provider notice](https://help.yahoo.com/kb/SLN2352.html) prohibits
  redistribution and describes informational, as-is data. It lists ICE Data
  Services for CBOT and the `.CBT` suffix, but does not establish vintage-level
  lineage or the completeness of a particular corn contract.
- [Yahoo's historical-download help](https://uk.help.yahoo.com/kb/historical-prices-sln2311.html?redirect=true)
  currently describes CSV downloads as a Gold subscription feature, unavailable
  for some instruments because of licensing. No subscription, checkout, login,
  alternate endpoint or access-control workaround was attempted.

Personal, non-commercial use removes the proposed redistribution requirement; it
does not by itself establish automated-access permission. Therefore no live
acquisition script is enabled. An authorized bounded delivery method is needed
before empirical coverage testing can proceed.

## Fields and quality: supported schema versus observed data

| Requirement | Source-code finding | Actual data validation |
| --- | --- | --- |
| OHLC / volume | `utils.parse_quotes` constructs these columns. | No rows acquired; units, completeness and values unknown. |
| Adjusted close | Parser accepts it, or falls back to close. Default history behaviour auto-adjusts OHLC unless disabled. | Not settlement and not evidence of a documented futures roll adjustment. |
| Historical open interest | Not a column in the standard history parser. | Not obtained. A current quote's OI would not supply historical OI. |
| Distinct official settlement | Not a separate parser field. | Close's settlement/last-trade meaning is unverified; no substitution allowed. |
| Contract ID / expiry / first notice | Not per-row fields in the history parser. | No contract master or historical notices obtained. |
| Revisions / publication times | Download time is not original availability. | No archived vintages or release clock established. |

The inspected client removes duplicate timestamps and fills missing volume with
zero, including when `keepna=True`. Default `keepna=False` also removes certain
empty rows. Therefore the processed DataFrame alone cannot demonstrate that the
source had no duplicates or distinguish missing volume from genuine zero volume.
If access becomes authorized, preserve the bounded raw response privately before
normalization, subject to permission and a verified metadata boundary. Do not
enable repair, fill gaps, infer expiries or relabel close as settlement.

Independent reference: [CME grain fact card](https://www.cmegroup.com/trading/agricultural/files/grain-and-oilseed-futures-options-fact-card.pdf)
specifies 5,000 bushels, cents per bushel and a 0.25-cent tick ($12.50). This is a
reference convention, not proof of Yahoo's units or a historical calendar audit.
No acquired values exist to cross-check against it or an independent vendor.
Missing sessions, OHLC order, tick alignment, duplicates, volume/OI anomalies,
individual expiries and overlapping prices are all **NOT RUN**, not passed.

## Research decision

| Intended use | Decision |
| --- | --- |
| Exploratory plots / prototype features | Potentially useful after lawful, bounded acquisition and validation; no dataset cleared now. Label any generic series as a proxy. |
| Programme statistical research | Not ready. `ZC=F` does not by itself identify each delivery contract or supply verified roll dates/adjustments. Undocumented splices could contaminate returns, seasonality and event studies. |
| Realistic executable backtests | Not suitable as currently established: no verified individual-contract inventory, expiry/notice ledger, historical OI or distinct settlement, and no validated sessions or fills. |

Do not treat a generic symbol as an executable contract or assert a specific
continuous construction without evidence. QRS requires an individual expiry or
documented continuous provenance. Neither was created. No QRS store, family,
backtest or confirmatory test was opened/run. No protected observations were
accessed during this Yahoo investigation; earlier exposure records remain intact.

## Alternatives and remaining cost

See the [free-source comparison](../free_cbot/README.md) for the broader audit.
Prices were rechecked on official documentation on 2026-11-04:

- [FinzData](https://finzdata.com/shop): $49 once for one market; its
  [ZC coverage table](https://finzdata.com/futures-data) starts in 2008, while
  generic text says 2007. It cannot establish the required 2006–2021 window.
  Settlement/expiry completeness still needs a sample. Personal use may fit its
  individual licence, but no purchase is recommended without validation.
- [Norgate](https://norgatedata.com/futurespackage.php): $148.50/six months;
  wider individual-contract history advertised, close means settlement. It is a
  conditional full-window candidate, with no separate traded close and the
  [retention restrictions](https://norgatedata.com/faq.php) recorded in the prior audit.
- Barchart's previously recorded $29.95/month is cheaper at entry, but its
  automated-use/AI and CME restrictions remain unresolved for this workflow.
  No claim that FinzData is the cheapest suitable complete source is made.

No fully suitable complete paid route has been verified. The next prerequisite
is a pre-2022 sample delivered under confirmed terms, including a contract
inventory and field definitions. No vendor was contacted or money spent.

## Reproduce and verify

From the repository root (package installation contacts the package index, not
Yahoo; the audit itself blocks HTTP):

```sh
uv run --no-project --with yfinance==1.8.0 python research/corn/data_sources/yahoo_feasibility/audit_yfinance.py
python3 -m unittest discover -s research/corn/data_sources -p 'test_acquire.py'
python3 research/corn/data_sources/acquire.py verify
uvx ruff check research/corn/data_sources/yahoo_feasibility
uvx ruff format --check research/corn/data_sources/yahoo_feasibility
```

Local evidence: all three offline boundary cases passed with zero HTTP
dispatches; 17 existing acquisition tests passed; seven original datasets and
five evidence excerpts passed manifest verification; Ruff check/format passed.
Only this isolated audit, metadata receipt and report are added. No price files,
engine changes or new project dependencies. Hosted CI is unavailable; application
and browser suites were not rerun for this scope.
