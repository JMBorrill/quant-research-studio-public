# Free CBOT corn contract acquisition — 4 November 2026

**Update:** [Yahoo live testing](../yahoo_feasibility/LIVE.md) subsequently acquired
4,040 generic ZC=F rows privately. Individual contracts remain missing. The
zero-acquisition conclusion below describes the earlier constrained investigation.

**Blocked: zero price files, zero contracts and zero price rows acquired or imported.**
The requested observation window remains 2006-01-01 through 2021-12-31. This
directory records an investigation, an actual archive access attempt and an
offline-tested access probe. It does not complete the missing price dataset.
PR #43 supplies other research inputs, not executable corn contracts.

Follow-up: the [Yahoo/yfinance feasibility audit](../yahoo_feasibility/README.md)
tests personal-use suitability and reproduces an unbounded timezone lookup
offline. It establishes neither a permitted live download nor usable coverage.

## Source register and actual access results

The links below were reviewed on 2026-11-04. Advertised coverage is not verified
file coverage. No market-data payload was downloaded, so no raw price-file hash,
row count, observed date range or redistribution clearance can be supplied.

| Source | Advertised product and fields | Actual result and suitability | Rights |
| --- | --- | --- | --- |
| [TurtleTrader historical data](https://www.turtletrader.com/hpd/) | Static corn ZIP; date, OHLC, volume, OI. Its [CD manual](https://www.turtletrader.com/pdfs/instructions_cd.pdf) describes individual files such as `C_69h.txt` and attributes data to Price-Data.com / Grain Market Research. | Actual `HEAD https://www.turtletrader.com/cddata/c.zip` returned HTTP 403; see `turtle_head.json`. Web download handling also could not retrieve ZIP bytes. No access-control workaround attempted. The manual describes the CD, not verified ZIP contents. Start/end dates, individual-contract structure, units, settlement meaning and expiry metadata remain unknown. | Page requires written permission to copy or redistribute. Free download is not permission to commit data. No permission obtained. |
| [Trading Commodity downloads](https://trading-commodity.com/download/) and [corn listing](https://trading-commodity.com/download/c/) | Corn listing advertises 7,802 daily bars from 1995-11-17 to 2026-11-02, date/OHLC/volume; FAQ describes continuous front-month data, adjusted by default. | No CSV fetched: no verified server-side pre-2022 boundary. A raw/unadjusted button does not establish individual contracts. OI, separate settlement, expiry and contract identifiers are not listed. Continuous data cannot fill the contract requirement. | Research/education wording does not establish redistribution rights or underlying exchange provenance. Unresolved. |
| [Tick Data demo](https://www.tickdata.com/demo) | TickWrite demo; [futures product](https://www.tickdata.com/product/historical-futures-data/) advertises individual contracts and selectable dates. | No anonymous, dated, pre-2022 CBOT corn sample payload verified. No form, account or installer used. [FAQ](https://www.tickdata.com/futures-faq/) distinguishes tick-aggregated volume from official EOD volume. | No sample redistribution permission established. |
| [FinzData samples](https://finzdata.com/samples) | Six equity samples; [futures documentation](https://finzdata.com/futures-data) offers the most recent 30 days on its free API. | No historical corn sample identified. Recent API data is outside the permitted window; not requested. | [Terms](https://finzdata.com/terms) prohibit raw redistribution, including samples. |

The access receipt contains retrieval time, URL, method, HTTP status and selected
headers. It contains no response body and grants no acquisition permission even
if a later probe returns HTTP 200. Missing Last-Modified is recorded as null;
retrieval time is not a market publication time. The ZIP's historical end date
must be established before any body download can be considered.

## Paid alternatives: documented offers, not validated datasets

| Candidate | Public USD price | Coverage, fields and exact limitations |
| --- | --- | --- |
| FinzData | [Shop](https://finzdata.com/shop): $49 once for one market; optional updates $19.95/month. No checkout performed. | Lowest one-time price among the candidates in this table. OHLCV and OI are advertised. The ZC row in [coverage documentation](https://finzdata.com/futures-data) starts in 2008; generic text says 2007. Neither establishes 2006 coverage. A [marketing page](https://finzdata.com/historical-futures-data-download) also conflicts on price ($39). No corn file, official settlement definition, complete expiry master, publication clocks or revision history verified. A roll calendar is not an expiry master. |
| Norgate | [Futures package](https://norgatedata.com/futurespackage.php): $148.50 for six months or $270/year. | [Coverage tables](https://norgatedata.com/data-content-tables.php) list corn from 1978-01-04; individual contracts are advertised. This is a conditional full-window candidate, not verified completeness of every 2006–2021 contract. Close is settlement, not a separate traded close. [Data FAQ](https://norgatedata.com/data-package-faq.php) documents high/low expansion to include settlement, OI lag and corrections. Expiry metadata and bounded delivery still require verification. |
| Barchart Premier | Prior source register records $29.95/month and expired-contract downloads. Direct [membership page](https://www.barchart.com/membership-comparison) recheck met a robot challenge. | Lower advertised entry cost than FinzData, but not cleared for this workflow. Independent review of indexed official [terms](https://www.barchart.com/terms), CME Group Policy (updated 2026-02-03), found restrictions on derivative products and AI/ML use of information and metadata. No automated extraction, subscription or data download attempted. Current direct-page price and suitable separate licensing remain unverified. |

FinzData's shop permits retaining purchased history and private cloud/backtester
use. Its terms restrict single products to an individual's personal research or
trading; institutional/commercial use needs different licensing. Raw redistribution
is prohibited. Source authority and marketing claims still need independent
validation against a corn sample before purchase.

Norgate's [licensing FAQ](https://norgatedata.com/faq.php) limits use to personal
purposes and permits private cloud processing while licensed. On expiration,
data, exports, normalized copies, metadata, snapshots and backups must be deleted.
It therefore does not provide a permanently retained research archive at $148.50.
Neither offer authorizes adding raw prices to this repository.

**No source was verified to meet all fields, the complete window and retention
requirements.** The cheapest next step is free: obtain an explicitly pre-2022
corn sample, a coverage inventory and written usage terms from a provider.
FinzData's $49 route remains incomplete for 2006–07 even on its advertised dates;
Norgate is the lowest documented full-window candidate examined here, subject to
the above restrictions and sample validation; Barchart's lower retail price does
not establish suitable rights. No vendor was contacted and no
purchase, account or subscription was created.

## Validation and QRS integration status

All market-data checks are **NOT RUN — no eligible payload**, not passed:

- Actual date coverage, duplicates, missing sessions and missing fields.
- Price units, tick grid, OHLC ordering, volume/OI integrality and anomalies.
- Contract identifiers, exchange identity, expiry dates and pre-expiry bars.
- Independent overlapping values, settlement definitions and revision differences.
- Release/publication times and point-in-time availability; current vendor history
  cannot automatically be called point-in-time evidence.

Existing `src/qrs/contracts.py` and `src/qrs/importer.py` were inspected. Import
requires genuine OHLC, an instrument, a calendar and source/rights declarations.
An individual instrument needs supported expiry metadata; expiry cannot be
inferred from its final row. No continuous series will be labelled an individual
contract and no settlement will be synthesized from close. Volume, OI and distinct
settlement need preserved source sidecars because QRS bars do not carry them.
No instrument, synthetic price fixture, engine change, QRS run or confirmatory
test was introduced. Existing manifests and historical hashes are unchanged.

## Protected-period incident

An initial web search returned unsolicited current corn-price rows and CFTC
positioning snippets dated in 2026. The assistant saw those snippets. No values
were copied into repository files, used for validation or used in research.
Subsequent lookups were restricted to documentation and pricing. No protected
CSV/ZIP was requested. This is an additional exposure, not a claim that 2022 onward
remained unseen; prior exposures and lineage issue #41 remain unresolved.
This record supersedes inherited documentation that describes the period as
untouched; it does not reset or cure that exposure.

## Reproduction and checks

From the repository root, an optional network probe writes a **new** metadata
receipt and refuses redirects; it never requests price bodies:

```sh
python3 research/corn/data_sources/free_cbot/probe.py --out /tmp/corn-head-new.json
```

Offline verification:

```sh
python3 -m unittest discover -s research/corn/data_sources/free_cbot -p 'test_*.py'
python3 -m unittest discover -s research/corn/data_sources -p 'test_*.py'
python3 research/corn/data_sources/acquire.py verify
```

The parent acquisition suite includes PR #43 tests only when that unmerged work
is present. This PR is independently based on main and reuses main's redirect
guard. See `VERIFICATION.md` for the exact local results and independent review.
