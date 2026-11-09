# Corn programme on real data: audit and research design

> Historical design audit (3 November 2026), contextualised on 7 November 2026. Statements
> about unavailable data describe the original audit date; see
> [data_sources/README.md](data_sources/README.md) for subsequent bounded acquisition and its
> limitations. This documentation update does not register
> hypotheses, run evaluations, establish trading eligibility or release protected periods.

Written 3 November 2026, at the Phase 5 release (55c0003). This is a design, not a result: no real
CBOT, USDA, CFTC or weather data has been read by QRS, and nothing here is an empirical finding. The
synthetic fixtures verified the workflow only. Confirmatory testing must not start until the data
(step 1–3 of the [execution sequence](#execution-sequence)) and the periods (decisions D1–D6) are
validated.

Facts are marked **verified** when this audit checked them, with the source, and **to verify** when
they come from documentation or general knowledge that could not be checked here.

## Summary

- **No real data is available.** Neither this repository, this environment, nor the related project
  (`JMBorrill/earth-observation-commodity-ml`, "the EO project") holds CBOT prices. The agency hosts
  are refused by this environment's network policy, so no public series could be downloaded either.
- **Every Family A and B hypothesis needs licensed CBOT prices.** USDA, CFTC and weather data can be
  obtained free once the hosts are allowed, but none of the registered hypotheses can be evaluated
  without prices.
- **The 2022 test period cannot be confirmatory.** The confirmation start, 3 January 2022, was fixed
  in the Stage 5 plan (approved 2 November 2026), when the corn market's path since 2022 was public
  knowledge to everyone who designed the programme. QRS's read history shows that QRS has not read it;
  it cannot show that the designers had not seen it. Families evaluated on it should be registered
  post hoc (`confirmatory: false`). A confirmatory test needs data published after its registration
  ([Periods](#periods)).
- **Two specification problems for real data:** the December 2024 contract may have too little
  pre-2022 history for Family B's search, and the disaggregated COT history before September 2009 was
  not published until October 2009, so it cannot be ranked "from 2006" point-in-time.
- **The EO project proposes reserving the 2024 and 2025 seasons** as its own holdout. Reading December
  2024 and 2025 corn prices here would compromise that holdout too; the two projects' periods need one
  decision.

## Data previously examined

Everything known to have been read or seen, which constrains what can still serve as a holdout:

| Where | What was examined | Bearing on periods |
|---|---|---|
| This repository | Synthetic fixtures only, written by `fixtures/generate.py` from a seed, with no data file read (**verified**: it reads only its own outputs). No `.qrs` store in this environment | None: not evidence |
| QRS stores elsewhere | Unknown. A store on the owner's machine that imported real corn data would hold the read history | Check every store's read horizons (Research dialog, or `GET /api/projects/{id}`) before registering |
| EO project, stages 0–1 (22–23 October 2026) | Its records state that no futures price, yield label or outcome was loaded (`research/SOURCE_REGISTER.md`, DEC-0007, DEC-0012). It did retrieve documentation samples: the October 2026 WASDE tables, the 19 October 2026 Crop Progress report, one Iowa GHCN station for May–September 2024, two GEFS runs of 1 June 2024 and Sentinel-2 scenes of June 2024 | The October 2026 WASDE and Crop Progress were seen: exclude them from any prospective window that starts before them (none proposed does). The 2024 weather sample matters only to a weather hypothesis |
| The designers (owner and AI assistants) | General public knowledge of corn prices, USDA balance sheets and fund positioning through 2026, and the published literature on seasonality, report-day volatility and momentum | Contaminates every historical period chosen in 2026, including 2022–2026. Hence post hoc labels |

## Data inventory

### Prices: CBOT corn (ZC)

| | |
|---|---|
| Needed for | Every Family A hypothesis (continuous series) and Family B (the December 2024 contract) |
| Status | **Missing, restricted.** Licensed vendor data (CME DataMine or a commercial vendor); not redistributable. No provider chosen, no sample, no quote |
| Contract (verified, CBOT Rulebook ch. 10 as reviewed by the EO project) | 5,000 bushels; tick 0.25 ¢/bu = $12.50; months H, K, N, U, Z; last trading day the business day before the 15th of the contract month; daily settlement from the 13:14–13:15 CT window, 12:00–12:01 CT on an expiring contract's last day |
| Listing (to verify) | July and December contracts are listed several years ahead (a June 2026 snapshot listed December 2029). If ZCZ24 was listed on ZCZ20's expiry, it has about 13 months of thin, deferred trading before 2022; the fixture assumed it from July 2020 |
| Wanted | Daily OHLC and settlement, volume and open interest, per contract: ZCZ24 from listing to 12 December 2024; every front contract used by the continuous series; earlier December contracts if Family B is redesigned. A back-adjusted (difference) continuous series from June 2006, with the vendor's roll date and adjustment for every contract, which the declaration must list (A3) |
| Timestamps | One bar per CBOT session. Whether the vendor's "close" is the settlement or the last trade must be established; the as-of join treats the bar as known at the session's close |
| Revisions | Vendor corrections are silent: keep the download date and the raw file's SHA-256 |
| Calendar (verified here) | `CBOT_Agriculture` in pandas_market_calendars 5.5 has no session on 20 June 2022, 19 June 2023, 19 June 2024 and 19 June 2025 (Juneteenth; CME's notices, reviewed by the EO project, confirm no CBOT settlement on these), or on 5 December 2018 and 9 January 2025 (national days of mourning). Its early closes, such as 29 November 2024, were not checked against CME |
| QRS limits | Bars hold OHLC only, so volume and open interest are audited outside QRS (step 3). Fractional quotes (eighths, as in `475'2`) must be decoded before import |

### USDA WASDE

| | |
|---|---|
| Needed for | Family A `reports` (release sessions); the tone extraction (report texts) |
| Status | **Public domain, not acquired** (hosts refused here) |
| Sources | Every report and its release date: the Cornell Mann Library archive and USDA ESMIS. First-release values: USDA's historical WASDE data, as published, from 2010 (to verify); earlier values transcribed from the archived reports |
| Release time (verified, Farm Progress and Agri-Pulse, December 2012) | 08:30 ET until 1 January 2013, noon ET from January 2013. Both fall before the CBOT close, so the release session is the same day either way |
| Exceptions | October 2013 cancelled (shutdown); October 2025 not issued, November 2025 released on 14 November (verified, Farm Policy News and Reuters factbox); January 2019 delayed (to verify) |
| Revisions | Each report revises earlier months' estimates for the same marketing year; the consolidated CSV appears the day after the report and must not be stamped with the report's time (EO project notes) |
| Confound | NASS Crop Production is released with WASDE in August–November and January; other major NASS reports (Grain Stocks, Acreage, Prospective Plantings) fall on non-WASDE days and so sit in the comparison group |

### USDA NASS Crop Progress (corn condition)

| | |
|---|---|
| Needed for | Family A `condition` |
| Status | **Public domain, not acquired** |
| Sources | Values: Quick Stats (free API key; 50,000 records per response; bulk files), which is a current extract, not a vintage. Release dates and as-published values: the dated releases in the ESMIS archive |
| Release time (to verify) | Mondays at 16:00 ET, after the CBOT close, so the next session's close is the first that can use it; Tuesday after a Monday holiday. The EO project could not verify the time |
| Exceptions | Suspended during the October 2025 shutdown (verified, Reuters factbox); the 2013 shutdown delayed a release |
| Revisions | Corrections can appear in the Weekly Weather and Crop Bulletin (EO project notes) |
| Coverage (to verify) | National corn condition, about late May to October, from 1986 |

### CFTC Commitments of Traders, disaggregated futures only

| | |
|---|---|
| Needed for | Family A `positioning` (managed money long less short) |
| Status | **Public domain, not acquired** |
| Sources | CFTC historical compressed annual files; the weekly releases' actual dates from the CFTC's release schedule and special announcements |
| Publication (verified, CFTC release 5737-09 and contemporary notices) | The disaggregated report was first published on 4 September 2009. History back to 13 June 2006 was released on 20 October 2009, classified by traders' classifications at that time ("backcasting"). Positions are as of Tuesday, normally released Friday at 15:30 ET (to verify per week) |
| Exceptions (verified, CFTC announcements) | Interrupted 1 October to 12 November 2025, then published in chronological order to catch up (normal by late December 2025 or January 2026; sources differ). The 2013 and 2018–19 shutdowns also delayed releases |
| Point-in-time | Rows for June 2006 to August 2009 were first public on 20 October 2009 and use later classifications. Stamped with their true publication, they never appear at a close as the latest event, so ranking starts in late 2009, not 2006 as the README says. Stamping them on their nominal Fridays would be look-ahead |
| Market code (to verify) | Corn, Chicago Board of Trade, 002602 |

### Weather

Not in the programme: the Stage 5 plan (P10) left weather out, so any weather hypothesis needs
approval and would start as exploratory.

| Source | Terms | Point-in-time status |
|---|---|---|
| GHCN-Daily (NOAA, on AWS) | Public domain | The EO project verified that the files are rewritten daily with no vintages: observed values are retrospective |
| NOAA climate divisions (nClimDiv) | Public domain | Preliminary values are revised later; vintages unverified |
| US Drought Monitor | Public domain | Weekly maps; whether past values are ever revised is to verify |
| GEFS forecasts (NOAA, on AWS) | Public domain | Operational runs from 2017; the EO project measured near-real-time mirror copies from 2019 and the v12 system from 23 September 2020. The only weather a trader could actually have had |
| ERA5 reanalysis | Copernicus licence | Produced retrospectively: never point-in-time |
| PRISM; commercial weather (e.g. DTN) | Restricted or licensed (terms to verify) | Unknown |

### Missing and restricted

- **Analyst expectations** for WASDE and NASS reports (news-agency surveys): licensed and absent. No
  hypothesis about report *surprises* can be tested; Family A tests volatility only.
- **Release dates of other NASS reports** (Grain Stocks, Acreage, Prospective Plantings, Crop
  Production): public, needed only to control the `reports` comparison group.
- **CME daily price limits and margins over time:** public notices, not acquired. QRS models margin as
  a fixed fraction of notional; limit days truncate returns.

## Questions, hypotheses and methods

The registered families stay as written in `families/`; their parameters were fixed by the approved
plan before any data, and changing them now, after this audit, would be a fork that must be recorded.
All Family A analyses use price changes per contract on the difference-adjusted continuous series,
the only measure QRS accepts for it.

| ID | Hypothesis (direction) | Method | Economic rationale | Competing explanations to rule out |
|---|---|---|---|---|
| A `months` | Mean monthly returns differ across calendar months (two-sided, joint) | `qrs.seasonal_test`, F on (11, years − 1) | A weather-risk premium that builds before pollination and decays into harvest | A few extreme years (2012 drought) dominate; dollar changes scale with the price level, so high-price years weigh most; carry and convergence within contracts; widely published seasonal charts |
| A `reports` | Absolute returns are larger on WASDE sessions (greater) | `qrs.event_test`, absolute | Scheduled public information is priced on release | Co-released Crop Production; other big report days in the comparison group bias the difference down; volatility seasonality (WASDE falls in the growing season); daily limits truncate the largest moves; the 2013 release-time change |
| A `condition` | The weekly change in condition predicts the next five sessions' return (two-sided) | `qrs.regression`, sampled at releases, h = 5 | None expected in an efficient market: a finding would be slow incorporation | Weather forecasts move both; condition ratings are subjective and biased early in the season; overlap of five-session horizons (the method warns) |
| A `positioning` | Returns after managed-money net positions above their 90th percentile differ (two-sided) | `qrs.regimes`, expanding rank, lagged one session | Crowded positions unwind (reversal), or trend-followers persist (momentum) | Managed money follows trends, so the regime proxies past returns; few episodes (warned below 5); backcast classifications before 2009 |
| B `trend` | The top EMA-crossover trial keeps a positive mean return on ZCZ24 from 2022 (greater) | `qrs.mean_test` on the trial's holdout equity; deflated Sharpe and Reality Check reported | Time-series momentum in commodities, as published | One contract's path; the 2022–2024 path was known when the rule was chosen; costs and thin deferred trading; post-publication decay |

Assumptions common to all: returns are stationary within each period; HAC errors with the plan's lag;
Holm within Family A; and each method's stated, measured size (for example 10–12% at 5% for the
seasonal test with two to four years per month), which the results report.

## Periods

| Period | Use | Status |
|---|---|---|
| June 2006 to 31 December 2021 (COT from its first point-in-time release, late 2009) | Exploration, specification checks, Family B's search (trials cut at 3 January 2022) | Exploratory |
| 3 January 2022 to the last session before registration | Single, pre-specified evaluation of Families A and B, as the project already declares | **Post hoc**: pre-specified but not blind, because the period was public knowledge when the start was chosen |
| From a future registration date | A new project, on data imported after that date, with frozen families whose documents and hashes are committed to Git before the date | Confirmatory, if no one reads the new data before registering |

Why keep 3 January 2022: it was fixed in an approved plan before any data was read in QRS; moving it
now, after seeing what each choice would give, adds a fork without removing the contamination.

What a prospective test can show: each year adds about 12 WASDE releases, about 20 condition
releases, 52 COT weeks (about a tenth of them in the top decile) and one year per calendar month. Within
two years only `reports` has more than a handful of observations; `months` cannot be tested at all
(one year per month leaves no degrees of freedom). This is arithmetic on release counts, not a power
calculation.

## Risks

**Data quality**
- Thin deferred trading: settlement-only rows (open = high = low = close) are accepted unless every row
  is one; vendors may fill no-trade days with settlements, which look like zero-volatility sessions.
- Missing sessions are reported, never filled; a gap moves a backtest's next fill.
- Daily price limits truncate the moves report and positioning tests look for.
- Pre-2010 WASDE values are transcribed by hand.
- Continuous series: QRS checks the provenance's consistency, not its correctness. Spot-check rolls
  against the individual contracts.

**Contract roll**
- A roll on the last trading day, as the fixture's rule says, holds contracts through first notice and
  delivery, which speculators avoid. Prefer a vendor roll before first notice day, and record it.
- A roll decided by a volume or open-interest crossover uses that session's own data; with price
  changes only (enforced by QRS) this affects which contract a change comes from, not its level.
- Back-adjusted levels embed later rolls: QRS refuses percentage returns, levels-based records and
  spliced series on it (A3). Keep it that way.

**Leakage**
- Release times taken from schedules instead of archives (shutdowns, holidays) would place data before
  it existed.
- COT history before 20 October 2009 (above); the WASDE consolidated CSV's next-day clock; Quick Stats
  values as current extracts.
- Preparing the CSV files shows the researcher the test period. Convert with a script, and look only at
  rows before 3 January 2022.
- Any full read of a dataset in scope (`qrs run` without `--until`, an analysis outside the project, an
  export) compromises confirmatory registration in that store. Through a different file it is not even
  recorded (issue #41): a refreshed download, ZCZ24 against the continuous series, or the same data in
  another store or tool are all unrelated to QRS's lineage.

**Multiple testing and selection**
- Holm covers the four Family A hypotheses only. Registering several families and evaluating some is a
  selection no correction covers; every family is listed with the project.
- Exploration before the confirmation start is unlimited by design and only counted: list the planned
  exploratory analyses (the nine in `analyses/` plus those in step 5) and record any parameter changed
  after seeing them as a project note before registration.
- The hypotheses come from a literature that reported them because they worked historically.
- Family B's N counts the trials on its own holdout window only; any search run elsewhere, in another
  store or tool, is not counted.
- Cross-project: the EO project's proposed holdout (2024–2025 seasons) overlaps this test period.

## Execution sequence

Each step uses existing QRS commands and API; nothing needs a new product feature. Steps 1–3 read no
data through QRS beyond import.

1. **Decide** D1–D6 below, and record them as a project note once the project exists.
2. **Acquire** (needs D1 and network access). Put raw downloads outside Git (for example
   `~/corn-data/raw/`). Record for each file: source URL or vendor product, licence, retrieval date,
   SHA-256 and the release-date source. Only these metadata are committed.
3. **Audit before import.** Outside QRS, on the raw files and only before 3 January 2022: volume and
   open interest of ZCZ24 before 2022 (decides Family B, D5); roll dates against first notice; a sample
   of rolls against individual contracts; release timestamps against the archives, marking uncertain
   ones estimated.
4. **Import.** Write the CSVs with the fixtures' names, edit the declarations (`evidence_class:
   retrospective`, `redistributable: false` for vendor data, the vendor's contracts and roll rule,
   sources), then run `research/corn/run.py --data ~/corn-data` **only up to the project and the
   experiment**: the driver as written registers both families confirmatory and evaluates them, which
   D3 changes. Read each import report: missing sessions, calendar, file hash.
5. **Explore**, inside the project so every analysis is capped at the confirmation start: the nine
   analyses in `analyses/`, plus `qrs.coverage` of every dataset, `qrs.distribution` and `qrs.rolling`
   of the continuous returns, `qrs.subperiods` by year, and `qrs.event_study` and `qrs.seasonal` split
   before and after 2013 (the release-time change) using `since`. Each is kept and counted by QRS.
6. **Search** Family B's experiment on ZCZ24 to 3 January 2022, if step 3 shows enough traded history; then
   `search_surface`, `search_deflated` and `search_reality`.
7. **Validate the plan.** Check every store's read horizons; commit the family documents, unchanged or
   with recorded changes, to Git; confirm D3.
8. **Register** Families A and B, post hoc under D3, then **evaluate** each hypothesis once.
9. **Report**: build `report.json` with the data register, the previously-examined-data table and the
   post hoc label; `qrs report verify`; not `--shareable` (vendor data).
10. **Reproduce** in a second, fresh store from the same files: identical dataset IDs and
    `result_sha256` for every analysis (those naming experiments compared with their IDs masked, as the
    Stage 5 record did).
11. **Prospective tier** (D3): commit the frozen confirmatory family documents and confirmation start
    now; when the data have accrued, import the new files, create a new project and register
    immediately, before any analysis, then evaluate.

## Expected outputs

| Kind | Items |
|---|---|
| Datasets | `continuous`, `zcz24`, `crop_progress`, `wasde`, `cot` (retrospective), `wasde_documents` and its tone features (exploratory); for the prospective tier, the same series re-imported later |
| Experiments | The EMA crossover grid on ZCZ24 (six trials), or D5's replacement |
| Analyses and figures | Coverage tables; the seasonal profile; event-study paths; feature–return scatters; distributions; rolling volatility; sub-period tables; the search surface; Family A and B results with adjusted p-values |
| Report | `report.html`, `evidence.json`, `record.json`, verified; labelled post hoc and not redistributable |
| Reproducibility evidence | The data register (files, hashes, licences, release-date sources); the programme documents' Git commit; dataset IDs; every analysis's `record.json`; the second store's matching hashes; the runtime identity (QRS, Python, lock, pandas_market_calendars). Hosted CI and macOS remain outstanding for QRS itself |

## What can start now, and what needs data

| Work | Needs |
|---|---|
| This audit and design; the calendar spot check above; the fixture programme (passes) | Nothing: done |
| Synthetic size and power checks of the registered methods at the real release counts, labelled synthetic | Nothing |
| Decisions D1–D6 | The owner |
| Release-date registers; COT, WASDE and Crop Progress imports and their coverage before 2022 | Network access to the agency hosts (and a free Quick Stats key) |
| Any Family A or B exploration, search or evaluation | Licensed CBOT prices |
| A weather hypothesis | Approval (outside P10), then GEFS and observed weather |

## Blockers and decisions

**Blockers**
1. **No CBOT price data.** Blocks every hypothesis. Needs a vendor and licence (D1).
2. **Network policy.** This environment refused `www.cftc.gov`, `publicreporting.cftc.gov`,
   `www.usda.gov`, `www.nass.usda.gov`, `quickstats.nass.usda.gov`, `esmis.nal.usda.gov`,
   `usda.library.cornell.edu`, `www.ncei.noaa.gov` and `www.cmegroup.com`.
3. **ZCZ24's history before 2022** is short (about 13 months, if listed on ZCZ20's expiry; the listing
   date is unverified) and thinly traded, so Family B's search would be chosen on deferred, illiquid
   prices (D5).
4. **Cross-file lineage (issue #41)** leaves copies of the data under other files unrecorded; controls
   are procedural (steps 3, 7 and 11).

**Decisions for the owner**
- **D1** The price vendor and licence: fields, contracts, years, roll method, and whether results may
  be published.
- **D2** Network access for the hosts above, or acquisition on another machine.
- **D3** Families on the 2022 test period registered post hoc (recommended), and whether to add the
  prospective confirmatory tier with its start date.
- **D4** The EO project's holdout and this test period: which project reads the 2024–2025 December
  contracts first, or a joint prospective holdout.
- **D5** Family B: keep the ZCZ24 search if its history allows; otherwise a single pre-chosen
  parameter set (a one-point grid, N = 1) on ZCZ24, or prospectively on a later December contract.
- **D6** Whether a weather family is in scope.

## Sources

- CBOT Rulebook chapter 10, CME corn settlement procedure and Juneteenth settlement notices 2022–2025,
  as reviewed in the EO project: `research/references/2026-10-22/CME_CORN_CONVENTIONS.md` and
  `CME_JUNETEENTH_SETTLEMENT_REVIEW.md`
- [Farm Progress: USDA crop report release times will change](https://www.farmprogress.com/farm-business/usda-crop-report-release-times-will-change);
  [Agri-Pulse: USDA to begin noon-time crop reports on January 11](https://www.agri-pulse.com/articles/2498-usda-to-begin-noon-time-crop-reports-on-january-11)
- [CFTC release 5737-09](https://www.cftc.gov/PressRoom/PressReleases/5737-09);
  [CFTC Disaggregated Explanatory Notes](https://www.cftc.gov/MarketReports/CommitmentsofTraders/DisaggregatedExplanatoryNotes/index.htm);
  [Mondo Visione: CFTC to release history of disaggregated COT data](https://mondovisione.com/media-and-resources/news/cftc-to-release-history-of-disaggregated-cot-data-2011124/)
- [CFTC COT historical special announcements](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalSpecialAnnouncements/index.htm);
  [Farm Policy News: USDA will release November WASDE](https://farmpolicynews.illinois.edu/2025/11/usda-will-release-nov-wasde-crop-production-reports/);
  [Reuters factbox: shutdown and agriculture data](https://www.streetinsider.com/Reuters/Factbox-US+government+shutdown%3A+agriculture+data+hit/22220100.html)
- [USDA ESMIS: WASDE](https://esmis.nal.usda.gov/publication/world-agricultural-supply-and-demand-estimates);
  [Cornell Mann Library WASDE archive](https://usda.library.cornell.edu/concern/publications/3t945q76s)
- EO project source register and decisions DEC-0003, DEC-0007 and DEC-0012

