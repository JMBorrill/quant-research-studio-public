# Corn programme source register

**Follow-on update:** see [HISTORY.md](HISTORY.md) for the 2006–2021 expansion,
current coverage, exposure record and procurement comparison. Current Barchart CME
terms prevent treating Premier as suitable for this AI-assisted workflow. The
original pilot register below is retained as a dated record, not the current recommendation.

Verified/retrieved 3 November 2026. Prices below are advertised USD prices, not a purchase,
binding quote or confirmation that a particular licence covers this research. No money spent,
account created, credential requested, vendor contacted or market-price record downloaded.

## Decision

Use agency archives for fundamentals, CFTC for positioning, NOAA/NASA for weather, and USDA for
geography. These cover the non-price requirements at zero data-access cost. They do **not** provide
a complete trading-time dataset automatically. The lowest advertised subscription found with
documented expired-contract downloads and history reaching 2006 is Barchart Premier; it is a
candidate for a permitted manual export, subject to field and use-rights verification. Obtain a
specific corn-only quote from CSI or CME if those requirements cannot be met. Databento is a useful
API alternative from 2010 onward, but cannot alone fill the 2006–2009 requirement.

No source is declared suitable merely because its web page or API is free to access. In particular,
exchange prices remain restricted, and the audit's blanket public-domain label for USDM statistics
has not been established by the permission page reviewed here.

## Programme coverage

| Requirement | Preferred route | Delivered / remaining |
|---|---|---|
| Family A monthly returns, report-day volatility, condition and positioning responses | Individual CBOT ZC contracts plus transparent difference-adjusted continuous construction | Prices absent. Need daily OHLC, settlement, volume, OI, expiries, notice dates and roll ledger from June 2006. |
| Family B December contract | Licensed individual-contract history | No ZCZ24 or ZCZ25 data touched. Listing date, thin-trading assessment and D5 remain unresolved. |
| WASDE sessions, ending stocks and text | Dated OCE report archive; historical data as a cross-check | September 2021 corn table pilot; full pre-2022 releases, release clocks and narrative corpus still needed. |
| Corn condition | Dated NASS Crop Progress reports | One 2021 week, 18 states plus aggregate; complete weekly vintages still needed. |
| Managed money | CFTC disaggregated futures-only, market 002602 | 812 corn records from June 2006 through 2021; actual publication ledger and first-print/correction audit missing. |
| Grain Stocks and co-release controls | NASS dated archives and historical release calendars | September 2021 stocks pilot. Full Grain Stocks, Acreage, Prospective Plantings and Crop Production calendars/data remain. |
| Observed weather | NOAA GHCN-Daily; NASA POWER | Two complete 2021 point/station samples, retrospective. Corn Belt spatial weighting and historical availability clocks remain. |
| Forecast weather | Archived operational NOAA GEFS | One 2021 forecast index only; gridded forecasts, production latency and version breaks remain. |
| Drought | USDM weekly map/statistics archive; nClimDiv for monthly context | Iowa API sample validated locally; distribution rights unresolved. nClimDiv catalogue checked, values not fetched. |
| Crop geography | NASS annual/state/county acreage + CDL | 2020 state acreage from January 2021 report; 2020 Iowa CDL metadata checked. County history, boundaries and rasters remain. |
| Execution realism | CME historical calendars, limits, margins; vendor metadata | Current contract conventions checked. Historical notices, early closes and costs are not a validated dataset. |
| Report surprises | Licensed analyst consensus survey | Unavailable; a released level or revision is not a market-expectations surprise. No surprise hypothesis enabled. |

## Market prices: licensing and cost

### Barchart Premier / licensed OnDemand

[Membership comparison](https://www.barchart.com/membership-comparison) advertises Premier at
**$29.95/month**, **$239.95/year**, with an initial trial. Its
[download documentation](https://help.barchart.com/support/solutions/articles/242748-how-can-i-download-historical-data-)
specifies daily history from 2000 and explicit expired-contract symbols such as `ZCZ05`.
These are product-level coverage statements, not proof that every requested ZC row exists.

[Terms](https://www.barchart.com/terms) limit website use to personal, non-commercial purposes and
restrict automated extraction. Use a permitted manual download or separately licensed
[OnDemand getHistory](https://www.barchart.com/ondemand/api/getHistory), whose price is not publicly
verified here. No scraping script is supplied. Do not assume private/team research, cloud processing,
derived publication or redistribution is covered by a retail subscription.

Before purchase, establish whether Close means settlement or last trade, whether volume/OI are
contract-specific and which day OI describes, the earliest trading row for each contract, export
limits, correction policy and retention rights. Website help and membership pages show differing
download limits; confirm the applicable current plan. No sample values or entitlement were inspected.

### CSI Unfair Advantage / custom historical export

[Current order form](https://apps.csidata.com/OrderUA) advertises North American futures history-only
packages at **$200/5 years, $300/10 years, $450/20 years**. These require the Windows UA application
and exporting during the included month of access; Mac support is not guaranteed. A $20 trial has
only 18 months of history. Twenty rolling years in November 2026 would not necessarily reach June
2006, so ask for the exact 2006–2021 corn window. Full-history/custom pricing is quote-only.

[Format specification](https://www.csidata.com/?page_id=3610) documents one delivery contract per
file and says Close may mean settlement or last depending on the CSI series. Confirm the right
series and per-contract volume/OI. Marketing statements about historical availability are not a
completeness audit.

[Licence, updated December 2025](https://www.csidata.com/?page_id=824), restricts personal/private
use and requires a custom agreement for multi-user/network use, derivatives, publication or
redistribution. It also documents corrections without notice. Neither the retail fee nor an export
button grants permission to upload data to this repository or a hosted assistant. Suitable rights
and post-subscription retention remain unverified.

### Databento CME Globex

[Pricing](https://databento.com/pricing) offers usage-based historical access, a public estimate tool
and **$125 introductory credits**. Standard is advertised at **$199/month**; it is not necessary to
assume a subscription for a one-off historical request. The actual bounded corn query cost was not
computed without an account. Exchange agreements and permitted use must be checked; no external
redistribution right is assumed.

[Release notes](https://databento.com/docs/release-notes) document the extension of CME history to
**2010**. This leaves the earlier programme period missing. Use instrument definitions and
[statistics](https://databento.com/docs/schemas/statistics) for settlement/OI with their publication
and reference dates. [OHLCV documentation](https://databento.com/docs/schemas/ohlcv) says daily bars
use UTC days and trade aggregation; they are not CBOT-session bars or official settlements. Missing
no-trade intervals, corrections and breaks must be handled explicitly. Never treat a continuous
symbol mapping as a complete QRS roll/adjustment declaration.

### CME DataMine and exchange reference material

[CME DataMine](https://www.cmegroup.com/datamine.html) offers historical settlement and other
exchange datasets. Its earliest dataset is advertised as 1972, but **corn-specific history, fields,
retention, research/publication rights and total cost require a product quote**. Do not generalize
that date to every corn field. Public quote pages are not a licence for a redistributable archive.

[CME specifications](https://www.cmegroup.com/markets/agriculture/grains/corn/specs) and the
[grain fact card](https://www.cmegroup.com/trading/agricultural/files/grain-and-oilseed-futures-options-fact-card.pdf)
confirm 5,000 bushels, cents/bushel, 0.25-cent ticks ($12.50), H/K/N/U/Z months and last trading day
before the 15th. Current specifications do not prove a historical listing date or every historical
session time. Collect dated notice/expiry/calendar records without loading protected prices.
The [price-limit FAQ](https://www.cmegroup.com/articles/faqs/grain-oilseed-and-lumber-price-limit-faq.html)
explains the mechanism, not a validated historical daily limit series. Margins and execution costs
remain separate gaps.

**Free-price conclusion:** no free source was verified here to satisfy all of contract-level history,
settlement versus traded OHLC, volume/OI, 2006 coverage and explicit reuse rights. Generic continuous,
cash-corn or CFD prices cannot fill that contract-level requirement. No such proxy was acquired.

## USDA fundamentals and geography — $0 public access

USDA-authored report tables are government works; attribute NASS or OCE. The
[National Agricultural Library policy](https://www.nal.usda.gov/web-policies-and-important-links)
permits reuse of public-domain material but warns that third-party holdings may be restricted.
This delivery includes agency table transformations, not third-party photographs or research papers.

### WASDE (OCE)

The [WASDE landing page](https://www.usda.gov/oce/commodity/wasde) links an archive from 1973 and
consolidated historical data from 2010. USDA's
[2021 data-users explanation](https://data.nass.usda.gov/Education_and_Outreach/Meeting/2021/2021-spring-questionandanswer.pdf)
specifies as-published data from April 2010, without later revisions to older publications. The
consolidated download is updated **the day after** the monthly report; that file's availability is
not the report's release clock. It was deliberately not downloaded because it also contains holdout
years. Use dated editions or verified server-side temporal subsets.

The [10 September 2021 release](https://esmis.nal.usda.gov/publication/world-agricultural-supply-and-demand-estimates/2021-09-10)
was acquired as text. Its US corn table retains multiple marketing years and the prior-month
comparator. A comparator printed in September is not independent evidence of the August first print.
The text starts at the tabular pages and is **not a complete narrative corpus**. Historical release
times, cancellations, delay notices and archive replacement/correction history still need a ledger;
no exact publication timestamp is invented in the pilot.

### Crop Progress (NASS)

[27 September 2021 report](https://www.nass.usda.gov/Publications/Todays_Reports/reports/prog4021.txt)
contains condition for week ending 26 September. Its methodology says estimates can be revised the
following week; national estimates use acreage weighting. The
[September 2021 calendar](https://www.nass.usda.gov/Publications/Calendar/reports_by_date.php?month=09&view=l&year=2021)
marks this release published at a scheduled 16:00 ET. A historical schedule is not a delivery log.
Monday releases occur after the normal CBOT day close; holiday exceptions must be joined to actual
release records and the session calendar. The 18-state aggregate is retained rather than recomputed
as an unweighted mean. Dash means zero in this specific table, not missing.

[Quick Stats](https://quickstats.nass.usda.gov/api) is a free keyed API with a 50,000-row response
limit. No key was obtained. Current extracts/load times do not reconstruct first releases.
[NASS bulk extracts](https://www.nass.usda.gov/Quick_Stats/) are updated after weekdays; avoid
whole-history downloads that include holdouts. The plan's 1986 condition start is plausible but
complete national/state coverage and first-vintage availability were **not independently audited**.

### Grain Stocks and other release controls

[30 September 2021 edition](https://esmis.nal.usda.gov/publication/grain-stocks/2021-09-30) and its
[agency text](https://www.nass.usda.gov/Publications/Todays_Reports/reports/grst0921.txt) were acquired.
The September calendar marks publication at scheduled noon ET. Farm plus off-farm quantities
reconcile; older observation dates remain tied to this edition. Sampling error, suppression,
subsequent revisions and differing domestic/metric units must be retained. The archive has material
well before 2006 (including [1964](https://esmis.nal.usda.gov/sites/default/release-files/z316q1572/fb494c28f/mg74qq21k/StocOfGr-01-24-1964.pdf));
this is evidence of historical depth, not proof of complete coverage.

Acreage, Prospective Plantings and Crop Production can be obtained from the same dated archive and
calendar. Their complete pre-2022 editions and co-release ledger were not acquired in this pilot.
No current Quick Stats values are relabelled as vintage releases.

### Crop geography: acreage, CDL and county data

[Crop Production 2020 Summary](https://www.nass.usda.gov/Publications/Todays_Reports/reports/cropan21.txt)
provides the 48-state acreage table plus US total. The
[January calendar](https://www.nass.usda.gov/Publications/Calendar/reports_by_date.php?month=01&view=l&year=2021)
records 12 January 2021, noon ET. Use these weights only after availability; national totals are not
extra geography rows. Missing harvested values remain `NA`, not zero. County Quick Stats exports
would additionally need FIPS, changing boundaries, suppression and release-vintage handling.

[CDL catalogue](https://catalog.data.gov/dataset/cropland-data-layer) documents partial coverage
from 1997, CONUS coverage from 2008 and CC0 licensing. The
[2020 Iowa metadata](https://www.nass.usda.gov/Research_and_Science/Cropland/metadata/metadata_ia20.htm)
was acquired: publication date 1 February 2021; observations span 1 October 2019–31 December 2020;
it explicitly permits redistribution. This annual map was not available during the 2020 crop season.
No raster was downloaded. Use year/state accuracy assessments, projection, resolution, corn and
double-crop class definitions before aggregation. The
[CDL FAQ](https://www.nass.usda.gov/Research_and_Science/Cropland/sarsfaqs2.php) records historical
recoding/re-release of 1997–2013 maps; annual labels alone do not establish immutable vintages.

## CFTC positioning — $0, government data

[Historical compressed archives](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm)
supplied the 2006–2016 bundle plus annual 2017–2021 disaggregated **futures-only** files. Corn code
`002602` and exchange name were checked in the returned rows. Managed-money net is long minus short,
in contracts, excluding the separate spreading category. The
[CFTC web policy](https://www.cftc.gov/sites/default/files/cftc/cftcprivacy.htm) identifies government
information as public domain; private contributed content is excluded from that general rule.

[CFTC release 5737-09](https://www.cftc.gov/PressRoom/PressReleases/5737-09) verifies: disaggregated
reports began 4 September 2009; June 2006–August 2009 history was released 20 October 2009 using
then-current trader classifications. It cannot be treated as contemporaneously public in 2006.
[Historical viewable reports](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalViewable/index.htm)
label observation dates, not publication dates. Some observation dates themselves shift around
holidays; the sample contains 6- and 8-day intervals. No missing-week repair was applied.

Use the [release schedule](https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm)
and [special announcements](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalSpecialAnnouncements/index.htm)
to reconstruct actual publication, including shutdown backlogs. Present archives may incorporate
corrections. This delivery leaves every publication timestamp blank and makes no first-print claim.

## NOAA/NASA weather and drought

### GHCN-Daily — $0

[NOAA dataset documentation](https://www.drought.gov/data-maps-tools/global-historical-climatology-network-ghcn)
describes daily updates and weekly reconstruction, so today's extract is retrospective. Historical
coverage varies by station and element; century-scale catalogue depth is not local completeness.
The [NCEI subset API](https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation)
returned Des Moines airport (`USW00014933`) for 2021, metric units and flags, without a key.
Do not fetch a whole-station file spanning protected years. The
[NOAA-managed GHCN open-data catalogue](https://registry.opendata.aws/noaa-ghcn/) identifies the
dataset's open/public-domain policy (CC0 for NODD dissemination), attribution and no-endorsement
conditions. Station quality, observation-time/source changes, trace precipitation and missing
values need explicit handling. This sample has 43 trace-precipitation flags; those were preserved.

### NASA POWER — $0

[NASA's open-data entry](https://registry.opendata.aws/nasa-power/) permits access/download/use,
links CC BY 4.0 and requests citation. Daily meteorology is documented from 1981. Typical current
meteorological latency is 2–3 days; it is not historical row-level publication evidence.
[Daily API documentation](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) allows date
bounds and explicit UTC/LST; the pilot requests UTC. NASA's
[methodology FAQ](https://power.larc.nasa.gov/docs/faqs/data/) identifies MERRA-2/GEOS model-derived
meteorology. Reanalysis is not an operational forecast. The acquired response near Ames identifies
MERRA2/POWER, API v2.10.1, Celsius and mm/day. Preserve source/version/resolution and fill values;
do not confuse a model grid-point estimate with a weather station or national crop weather.

### NOAA GEFS operational forecasts — $0 download, storage/processing extra

[NOAA open-data catalogue](https://registry.opendata.aws/noaa-gefs/) permits use with attribution
and exposes a public S3 archive with four initialization cycles daily. A dated index for 1 June
2021 00Z, control member, 0.5-degree grid, forecast hour 006 was acquired. It lists 2-m temperature
and accumulated precipitation. **No gridded forecast values were acquired or validated.**
The existing plan's 2017 archive start and 2019 near-real-time mirror claim were not reverified
across the archive here. Initialization, lead/valid time and object availability are different
clocks; an S3 Last-Modified value after migration need not be original dissemination time. Retain
member, model version and precipitation accumulation interval. Operational runs and later-generated
reforecasts must never be silently combined. Full historic latency remains a gap.

### US Drought Monitor — $0 download; redistribution unresolved here

[Public REST documentation](https://droughtmonitor.unl.edu/DmData/DataDownload/WebServiceInfo.aspx)
supports date/area filters. Iowa cumulative area percentages were acquired locally and checked.
The [production timeline](https://drought.unl.edu/Education/Tutorials/usdm/section-six.aspx) places
publication Thursday 08:30 Eastern; map dates are Tuesdays. Historical exceptions/corrections
were not audited. The query returned the overlapping late-2020 map plus 52 maps dated in 2021.
Cumulative D0–D4 percentages must not be summed as disjoint categories.

The [permission page](https://droughtmonitor.unl.edu/About/Permission.aspx) specifies attribution
for maps; a blanket licence for republishing the statistics was not established. Therefore values
remain local, with manifest and validation metadata only in Git. General historical archive depth
is not yet a complete pre-2022 download or a first-publication ledger.

### NOAA nClimDiv — $0, retrospective context; not acquired

[NOAA metadata](https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc%3AC00005)
documents monthly CONUS coverage from 1895; the
[dataset briefing](https://www.ncei.noaa.gov/pub/data/cirs/climdiv/nCLIMDIV-briefing-cirs.pdf) describes
monthly updates. Latest files can contain revised historical estimates and protected periods.
Retrieve a verified bounded vintage/subset before use. Exact first-publication lag, revision
archive and file-specific reuse constraints remain to be captured; no unbounded file was downloaded.

## Remaining acquisition work and acceptance criteria

1. **Prices (blocking all hypotheses):** select a licence/product and obtain a pre-2022-only export
   excluding protected December contracts. Verify every required contract, daily field, session,
   missing/no-trade row, units and adjustment. Quote scope must cover June 2006, not just a rolling
   twenty years. No purchase, licence acceptance or distribution is part of this PR.
2. **USDA full history:** enumerate and hash dated reports from 2006–2021; audit missing/cancelled,
   revised and co-released reports; obtain narrative pages for tone work. The bounded parsers here
   are pilots for explicitly identified editions, not universal parsers for all historical layouts.
3. **Publication ledger (blocking temporal joins):** retain observation/target period, release date,
   actual published time/time zone, evidence URL/hash, vintage, correction/supersession relationship
   and uncertainty. Do not infer timestamps from ordinary weekly/monthly schedules. Reconcile CFTC
   backcasts and the 2013 and 2018–2019 shutdowns before any point-in-time use.
4. **Weather/geography:** extend to an approved spatial coverage and lagged acreage weighting; keep
   changing county boundaries, station quality and model versions. Acquire operational forecast
   grids with evidenced availability. Do not backfill crop-season weights from post-harvest maps.
5. **Rights:** confirm USDM statistics redistribution and vendor research, derived-results, hosted
   processing, retention and publication permissions. Record each right separately; free download
   is not equivalent to redistribution permission.
6. **Governance:** D3–D5 and [issue #41](https://github.com/JMBorrill/quant-research-studio/issues/41)
   remain open. Maintain the cross-project read record below; file identity alone does not detect
   exposure through refreshed copies. Do not merge or start evaluation from this acquisition PR.

## Cross-project exposure record

This task read source documentation and only pre-2022 real observations/report editions listed in
`assets.json`. The CFTC ZIPs contain other commodities in those same historical years; they were
parsed to select corn. The September 2021 WASDE contains forecasts for marketing year 2021/22;
those are historical forecasts, not later outcomes. Crop annual source tables include 2018–2020.
No 2024–2025 prices, weather, geography, agency outcomes or protected source files were requested.
Historical documentation/catalogue pages may mention modern release dates; their underlying
holdout data files were not opened. Previously recorded exposures in the original audit are not
erased or reclassified by this delivery. These acquired observations must remain marked examined
in any later project, including if they are downloaded again under another name.
