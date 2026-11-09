# Follow-on validation

The expanded 2006–2021 corpus and current local verification are recorded in
[HISTORY.md](HISTORY.md). The original pilot record below is preserved.

# Acquisition validation record

3 November 2026. Scope: source access, file integrity, parsing and data quality only. No empirical
hypothesis test or QRS research execution. Source bytes are identified by `assets.json`; transformed
bytes, keys and date ranges by `samples/manifest.json`.

## Acquired and checked

| Item | Evidence / result | Limit |
|---|---|---|
| CFTC corn futures-only | 812 unique market/date rows, 13 June 2006–28 December 2021; all code 002602 and CBOT corn name; net = long − short; long and short each non-negative and no greater than OI | Publication/correction history unknown. 18 adjacent intervals differ from seven days (6/8 days), retained as source dates. Not all observations are Tuesday. |
| Crop Progress | 19 rows: 18 states + aggregate, week ending 26 September 2021; each five-category total = 100; aggregate good+excellent = 59 | One week only; not a time series. Aggregate is acreage-weighted by NASS. |
| WASDE | Four corn ending-stock entries in September 2021 table; 2021/22 September projection = 1,408 million bushels | Four columns from one report, not four release events; August comparator explicitly labelled. Text is tabular, not full narrative. |
| Grain Stocks | Six corn date/vintage rows; on-farm + off-farm = total; 1 September 2021 = 1,236,481 thousand bushels | All from September edition; earlier periods can be revised. December 2020's incompletely paired row is not included in the six-row pilot. |
| State geography | 48 states + US total for crop year 2020; sum of state planted acreage = 90,819 thousand acres; harvested ≤ planted where available | Seven harvested values are `NA`; no county data or raster; figures are the January 2021 edition, not latest revised history. |
| NASA POWER | 365 unique daily dates in 2021; no returned fill values; min ≤ max temperature; precipitation ≥ 0; UTC explicit | One model grid point near Ames; Celsius/mm per day. Historical publication time unknown. |
| NOAA GHCN | 365 unique daily dates in 2021; station identity checked; min ≤ max; precipitation ≥ 0; no nonblank quality flags among the three elements | 43 trace-precipitation flags retained. Des Moines airport, not representative Corn Belt weather. Observation-time/source flags retained verbatim. |
| USDM (local only) | 53 unique map dates, 29 December 2020–28 December 2021; IA only; D0≥D1≥D2≥D3≥D4; none+D0 = 100 within 0.02 percentage points | Date-filtering returns overlapping validity periods. No values committed pending redistribution verification. No actual-release clock asserted. |
| NOAA GEFS | Dated 2021 control-member f006 index retrieved; all entries identify initialization 2021060100; temperature/precipitation fields present | Index only; no forecast grid, ensemble coverage, scientific validation or publication-latency proof. |
| USDA CDL | 2020 Iowa metadata acquired; release date 20210201 and public-domain redistribution statement checked | No raster; pixel classification/accuracy not tested. |

USDA numeric anchors were checked directly against the acquired source tables, then used as
independent parser regression anchors. No cross-vendor market-price comparison was possible.
All data-access charges for these public requests were zero. Computation/storage costs were not
priced. Network access worked with ordinary identified HTTP requests; early unidentified CFTC
requests returned 403, then the same identified client used for 2021 succeeded for the older files.
This is an observed access result, not a guarantee that endpoints stay available.

## Verification commands and boundaries

The focused offline unit suite checks catalog preflight, rejection of dates from 2022 onward,
rejection of protected URLs before any network call, refusal to follow redirects, duplicate rejection, changed input hashes,
NASA response-duration versus observation changes, USDA zero/missing tokens, manually verified
numeric anchors, weather coverage, and sample/excerpt checksums. It uses no QRS import, service,
family registration, backtest or inferential test.

`prepare` ran on all acquired raw files, and `verify` checks the emitted sample bundle. A repeat
preparation must reproduce identical CSV/excerpt hashes from identical inputs. Raw API timing
metadata is not a data revision; content changes require a separate reviewed vintage.

Executed locally with Python 3.14.8: all 13 focused unit checks passed. Two preparations from the
same acquired files produced byte-identical outputs for all seven CSVs and five evidence files.
The committed sample manifest also passed integrity, uniqueness and date-bound verification.
Python 3.12 was not available locally; no claim of a separate 3.12 execution is made.

No claim is made that the full application gate, hosted CI, macOS application runtime, full
historical report ingestion, point-in-time joins or research programme passes on real data.
Source coverage and quality approval here is limited to the rows and editions in the table.

## Remaining critical limitations

- No licensed contract prices, continuous roll provenance or volume/OI history: all return-based
  hypotheses remain blocked.
- Historical publication clocks and corrections are not verified sufficiently for temporal joins.
  Blank `published_at` values are intentional; schedule times are explicitly labelled.
- Most USDA and weather coverage is a pilot, not the complete 2006–2021 programme dataset.
- The unmerged audit's design/holdout decisions and QRS cross-file lineage issue are unchanged.

The source register contains the procurement/access paths and acceptance criteria for these gaps.
