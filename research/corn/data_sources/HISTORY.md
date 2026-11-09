# Corn history acquisition — follow-on to eb78cbb

3 November 2026. This is a partial acquisition and validation delivery, not a
completed research dataset or an empirical result. The original seven samples,
their manifests and raw hashes are unchanged. No engine or hypothesis changes.

## Reproduction and storage

```sh
uv run python research/corn/data_sources/historical.py acquire
uv run python research/corn/data_sources/historical.py prepare --out /tmp/corn-history-check
uv run python research/corn/data_sources/historical.py verify
uv run python research/corn/data_sources/acquire.py verify
uv run python -m unittest discover -s research/corn/data_sources -p 'test_*.py' -v
```

Raw files, archive metadata pages and retrieval receipts are under ignored
`raw/history/`. The final raw bundle is supplied separately; it is not committed.
Copy its `raw/history/` directory beneath this directory before reproducing.
PDF header inspection needs Poppler's `pdftotext`; it does not infer numerical
tables from PDF layout. The source and output hashes in `history/manifest.json`
identify this vintage. A fresh download that disagrees with the pin is refused;
cached files must also match. POWER's separate content hash excludes only its
request-duration field. Raw hashes and original retrieval times remain recorded.
Changing the pin requires explicit review of a new vintage; no refresh overwrites
the historical samples. Archive indexes can change even when reports do not.

Only agency release **metadata** is read on unfiltered index pages. A dated row
must be in 2006–2021 before a report URL can be followed. Dated filenames and
compact filename years are checked against that row. Sixty-eight version-specific
archive URLs named `latest.txt` or `latest.pdf` are separately allowlisted in `archive_aliases.json`,
after HEAD-only inspection established pre-2022 Last-Modified dates. Their exact
headers must still match before downloading a body; unreviewed aliases are refused.
Those modification dates do not establish original publication clocks. Redirects are refused.
Weather queries have fixed server-side bounds, locations, elements and units.
New locations/years/providers require a reviewed code change.

## Interpretation and import readiness

| Dataset | Use permitted by this evidence | QRS temporal import |
|---|---|---|
| NASA POWER | Daily UTC model-grid estimates at three fixed points; Celsius and mm/day | Blocked: historical dissemination and revision clocks unknown |
| NOAA GHCN-Daily | Three airport station series; Celsius/mm with original measurement, quality, source and observation-time flags | Blocked: current retrospective extract; original publication clocks unknown |
| USDM | Cumulative drought-area percentages for IA, IL and NE; local/private use | Blocked: publication clocks and statistics redistribution rights unresolved |
| USDA reports | Archived edition corpus and release-date evidence; current bytes pinned | Blocked: archive dates can disagree with document headers; exact clocks/corrections unresolved |
| CFTC positions | Original 812-row futures-only corn series retained; added notice-linked availability evidence | Blocked: announced dates are not delivery logs; first-print corrections unresolved |
| CBOT contracts | No licensed market-data files acquired | Blocked: procurement, all required fields and retention rights |

No compliant temporal import was identified. No QRS store was opened, no family
registered, and no research run, backtest, search, confirmatory evaluation or
`research/corn/run.py` execution occurred. Application tests use synthetic fixtures.
Blank `published_at` is deliberate. Using retrieval in 2026 as an invented
historical publication time would not resolve the gap. Archive HTML noon-UTC
timestamps are date metadata, not release clocks.

The regional panel is a starting coverage set, not an acreage-weighted Corn Belt
index. POWER coordinates: IA 42.03/-93.65, IL 40.05/-89.40, NE 41.50/-98.00.
NOAA station identities are Des Moines International Airport (USW00014933),
Springfield Abraham Lincoln Capital Airport (USW00093822), and Lincoln Airport
(USW00014939). No claim of farm-level representativeness, operational weather
forecasts or contemporaneous trader availability is made. Do not forward-fill
missing dates, discard flags, or combine UTC grid days with station days without
an explicit convention.

The USDM service returns a preceding map overlapping the requested start. Its
map date is checked before excluding pre-2006 overlap. The final 2021 map's
validity can extend into January 2022; this is a pre-2022 map, not a 2022 outcome.
Area percentages are cumulative and must not be summed as disjoint categories.

USDA raw reports may contain earlier crop years and forecasts targeting 2021/22.
Their information edition, rather than forecast target year, controls scope.
Revisions/supplements remain separate files. Archive/header conflicts are retained
and quarantined from temporal use, never silently corrected. WASDE PDF reports are retained alongside text companions because many later TXT
files contain tables only. Multiple formats, revisions and ancillary agency notices
are not independent release events. Downloaded reports are not a validated
full-history numerical corn table. Existing pilot parsers
are deliberately not applied to incompatible historical layouts.

## CFTC availability and corrections

The new `cot_release_evidence.csv` links original position dates to the pinned
source and relevant notices, without inventing Friday timestamps:

- [5737-09](https://www.cftc.gov/PressRoom/PressReleases/5737-09) announces
  20 October 2009 availability for June 2006–August 2009 backcast history and
  records the 4 September 2009 start of disaggregated releases. It does not prove
  an exact delivery timestamp or the first-print status of today's annual ZIP.
- [6745-13](https://www.cftc.gov/PressRoom/PressReleases/6745-13) describes an
  anticipated shutdown catch-up schedule starting 25 October 2013. It does not
  establish an actual release date for every delayed observation.
- [7864-19](https://www.cftc.gov/PressRoom/PressReleases/7864-19) anticipates
  1 February 2019 publication of the 24 December 2018 observation, followed by
  twice-weekly catch-up releases. The ledger labels this as announced availability.

CFTC's [FAQ](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm)
says a comprehensive historical release-date list is not available there.
Its [historical notices](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalSpecialAnnouncements/index.htm)
also identify corrections affecting corn for 15 October 2019. A current annual
download must not be labelled a first print. This follow-on fetched the three
dated press notices, not the unbounded historical positions page.

## CBOT procurement comparison

Advertised USD prices checked 3 November 2026; no account, purchase or paid query.
No free source was verified to provide the complete required individual-contract
OHLC, distinct settlement, volume, OI, expiry and rights package for 2006–2021.

| Source | Lowest relevant advertised cost | Suitability for this task |
|---|---|---|
| Barchart Premier | $29.95/month; $239.95/year | Cheapest advertised download candidate, **not verified suitable**. Daily history from 2000 and expired symbols are documented, but current CME terms prohibit AI/ML use and derivative products. No scraping/export supplied. |
| Norgate Futures | $148.50/6 months; $270/year | Cheapest reviewed candidate with explicit personal hosted-research permission. Corn history is listed from 4 January 1978. Close normally means settlement; high/low can be adjusted to encompass it. Separate traded close, expiry metadata and complete contract coverage require a sample/confirmation. Data must be deleted after subscription expiry. |
| CSI history export | $450 for 20 years of North American futures | Rolling coverage in late 2026 may omit early 2006. Exact window, inactive contracts, required fields, hosted processing and retention need confirmation/custom quote. Windows software; export during initial access period. |
| Databento | Usage priced; $125 introductory credit | API candidate for the later part of the window; documented CME history starts in 2010. No authenticated cost estimate or exchange entitlement obtained. Cannot alone cover 2006–2009. |
| CME DataMine / licensed Barchart OnDemand | Quote required | Request a corn-only 2006–2021 package with appropriate research, derived-output and retention rights. No verified total price. |

**No cheapest fully compliant vendor is established.** For personal research,
Norgate is the lowest reviewed candidate with clearer hosted-use terms; it is not
a substitute for perpetual retention or a separate traded-close series. For
commercial QRS development, obtain an appropriately licensed quote. Do not treat
a provider's catalogue start date as proof that every requested contract exists.

Sources: [Barchart pricing](https://www.barchart.com/membership-comparison),
[download coverage](https://help.barchart.com/support/solutions/articles/242748-how-can-i-download-historical-data-),
[current terms](https://www.barchart.com/terms),
[Norgate pricing](https://norgatedata.com/futurespackage.php),
[corn coverage](https://norgatedata.com/data-content-tables.php),
[field definitions](https://norgatedata.com/data-package-faq.php),
[hosted use and retention](https://norgatedata.com/faq.php),
[EULA](https://norgatedata.com/subscribe/eula.php),
[CSI order form](https://apps.csidata.com/OrderUA),
[Databento pricing](https://databento.com/pricing),
[CME history release notes](https://databento.com/docs/release-notes).

## Protected-period exposure and lineage

No protected-period source file was requested and no 2022-onward values were
integrated. However, an early vendor-discovery search returned unsolicited
snippets containing 2024 corn futures quotes from third-party news pages. The
assistant saw them. Those quote values were not saved to research files or used
in an analysis. **This session cannot be described as unexposed to the protected
period.** Subsequent discovery was restricted to provider/agency documentation.
Do not erase this record or claim prospective blindness based on QRS read logs.

[Issue #41](https://github.com/JMBorrill/quant-research-studio/issues/41) remains
open. The larger weather panels overlap the pilot's 2021 values and the new
CFTC ledger refers to the existing position series; different filenames are not
independent evidence. Retain this procedural relationship in subsequent projects.
The original research-plan decisions D3–D5 and cross-project holdouts are unchanged.

## Measured coverage and verification

| Output | Rows / assets | Coverage |
|---|---:|---|
| NOAA GHCN daily | 17,532 rows | IA, IL, NE; 5,844 days each, 2006-01-01–2021-12-31 |
| NASA POWER daily | 17,532 rows | Same three regions and daily interval |
| USDM (private) | 2,505 rows | 835 Tuesday maps per state, 2006-01-03–2021-12-28 |
| CFTC availability ledger | 812 rows | Existing positions, 2006-06-13–2021-12-28; 169 announced-date links, 643 unknown |
| USDA archive ledger | 1,194 assets | 1,181 distinct SHA-256 values; 203 PDFs plus text/ASC companions |

The USDA asset counts are **not independent release counts**. There are 1,180
report/revision assets, 11 ancillary notices and 3 non-corn supplements filed with
WASDE. Multiple formats and identical bytes are retained with their distinct URLs.

| USDA collection | Assets | Distinct archive dates for report/revision assets |
|---|---:|---:|
| WASDE | 333 | 194 |
| Crop Progress | 570 | 565 |
| Crop Production | 194 | 191 |
| Grain Stocks | 65 | 64 |
| Acreage | 16 | 16 |
| Prospective Plantings | 16 | 16 |

All six crawls reached the pre-2006 boundary. There are no HTTP failures, but
`acquire` deliberately exits nonzero for three unresolved archive entries:
WASDE 2008-07-02 has only a `.doc` file; the January 2019 rescheduling notice has a
filename/archive-date conflict in both Grain Stocks and Crop Production. The
unsupported/conflicting files were not fetched. WASDE has no report archive month
for October 2013 or January 2019; Crop Production has none for January 2019.
Absence is not filled with invented reports or assumed dates.

Nine assets have archive/header date conflicts. Header dates are unresolved for
66 report/table companions, four ancillary notices and three non-corn supplements.
Every actual `published_at` remains blank. The manifest records 1,206 source assets
and 194 index pages; source receipts preserve retrieval time, URL, byte count,
SHA-256, response type and available Last-Modified metadata.

All daily regional calendars and weekly drought calendars are complete with zero
missing temperature/precipitation values. NOAA retains 1,002/733/722 trace-rain
flags for IA/IL/NE respectively; no nonblank quality flags were returned. No
duplicate region/date keys, inverted temperatures, negative rain or invalid
cumulative drought percentages passed validation. Both expanded IA panels match
the original pilot's 2021 numerical values exactly. USDA table values were not
normalized across historical formats; market calendars, expiry and rolls remain
unvalidated because no contract data was acquired. CFTC managed-money coverage
starts in June 2006, not January; the pre-September-2009 backcast is not
contemporaneous positioning evidence.

Local verification on Python 3.12:

- 763 application tests passed; 14 browser tests deselected.
- 41 studio tests passed; studio type checking and production build passed.
- 39 acquisition checks passed, including protected-date preflight, source pins,
  alias changes, full-PDF selection, ancillary identity and region completeness.
- Ruff lint and formatting passed for application checks and the new tooling.
- Original seven datasets and five evidence excerpts passed their unchanged manifest.
- The new five-dataset manifest passed public-only and raw-inclusive verification.
- Independent review verified all 68 alias records, source hashes, counts and
  classifications; material findings were corrected with regressions.

Chromium installation failed with a truncated/non-ZIP download, so the 14 browser
tests were not run. Hosted CI was not used. These checks validate acquisition and
application compatibility, not trading performance, point-in-time joins or
full-history numerical USDA ingestion.

A second preparation reproduced all five public bundle files byte-for-byte,
including the manifest. Raw archive: `corn_history_raw_2006_2021.zip`,
97,681,050 bytes, SHA-256 `15fdfe9d0b273472bae830b99d54eaea46588b5502a169938577ac31820f06dc`. The archive includes private USDM
statistics and must not be publicly redistributed.

## Follow-up review — 4 November 2026

The four delivery ZIPs are independent archives. Their combined contents preserve
all 2,803 original entries: 2,802 files under `raw/history/` plus the root README.
All 1,401 directly hash-pinned raw files match: 1,206 source assets (including the
three CFTC notices), 194 archive indexes and the private drought CSV. The remaining
1,401 raw files are 1,400 retrieval receipts plus `acquisition_status.json`.
These support reproduction but do not have separate file-byte pins. All index
hashes are present in the manifest, including pages not referenced by a report.

`verify` now checks every pinned index, matching the coverage already enforced by
`prepare`. A regression rejects missing or changed unreferenced indexes. The
focused acquisition suite now has 40 passing checks; raw-inclusive verification
and Ruff checks passed. No data bytes, manifest pins or engine code changed.
The 812-row follow-on CFTC CSV is an availability-evidence ledger, not a newly
acquired position series; positions remain in the original pilot.
