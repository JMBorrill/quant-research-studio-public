# Corn futures research programme

A worked research programme for QRS's research layer (Stage 5): content, not code. The core
(`src/qrs/`) knows nothing of corn; a test checks that no programme term appears in it.

## The question

Do corn futures returns vary by calendar month, move more on the monthly supply and use (WASDE)
report, follow the weekly change in crop condition, or differ after extreme speculative
positioning? And does a trend-following rule chosen before 2022 keep a positive return after it?

Two families of hypotheses are registered, confirmatory, before anything reads the test period from
the confirmation start, 3 January 2022:

- **A, market behaviour** (Holm): calendar months (a joint F test on monthly returns per year);
  report sessions (absolute returns on WASDE sessions against others); crop condition (the weekly
  change regressed on the next five sessions' return, sampled at its releases); positioning
  (returns after managed-money net positions above the 90th percentile of their history to date,
  ranked from 2006).
- **B, strategy**: the top-ranked trial of an EMA crossover search on the December 2024 contract
  keeps a positive mean return after the confirmation start. Its deflated Sharpe ratio and Reality
  Check are reported beside it.

Expect inconclusive results: with three years of test period, most effects this size cannot be told
from noise, and the calendar-month test has 2 denominator degrees of freedom (three years per month,
less one), so it can detect only very large differences. That is the honest outcome.

Family B on real data: the strategy trades one contract, so its sample is that contract's life; first
notice and delivery are not modelled before the expiry, and a contract's early, deferred months are
thinly traded.

## Files

| File | What it is |
|---|---|
| `declarations/*.json` | How each CSV file is imported: columns, calendar, instrument, evidence class, rights |
| `fixtures/*.csv`, `fixtures/answers.json` | Synthetic, seeded data with the real data's shapes, calendars and release patterns; written by `fixtures/generate.py` |
| `project.json`, `families/*.json`, `analyses/*.json`, `experiment.json`, `extraction.json`, `report.json` | The programme's documents, with `${name}` in place of the IDs the run finds |
| `run.py` | Runs the whole programme through the service's API |

## Running it

```sh
uv run qrs serve --home .qrs &
uv run python research/corn/run.py --home .qrs                    # the synthetic fixtures
uv run python research/corn/run.py --home .qrs --data ~/corn-data  # real data, as below
```

It imports the datasets, creates the project, runs the strategy search (its trials read only to
the confirmation start), registers both families, extracts the reports' tone (a read of every
report, test period included, so after registration; the reports are outside the project's scope),
explores before the confirmation start, evaluates every hypothesis, then builds and verifies the
report. It runs where the service does, as `qrs extract` writes to the service's data directory. `tests/test_corn.py` runs it on the fixtures, offline.

## Real data

Start with the [real-data audit](real-data-plan.md) and the
[source register and bounded acquisition tools](data_sources/README.md). The latter contains
real, retrospective observations through 2021 only. Do not pass these pilot files to `run.py`:
that driver registers and evaluates families. The 2024–2025 holdout remains protected.

Put files of the fixtures' names in a directory, and edit the declarations to match their columns.
Release times must come from the agencies' archives of actual release dates, not their regular
schedules: government shutdowns (2013, 2018–19 and 2025) delayed releases, and a schedule would place
them too early. Data reconstructed this way is `retrospective`; mark rows whose time is uncertain as
estimated.

| File | Source | Terms |
|---|---|---|
| `continuous.csv` | CBOT corn (ZC) front-contract daily bars, back-adjusted by price differences, from your data vendor | Your vendor's licence. Not redistributable: set `redistributable: false`. QRS never builds rolls: the vendor's roll dates and adjustments go in the declaration's `continuous.contracts`, every contract with its expiry, first and last session and adjustment, covering every session once. The series is never traded, and is `retrospective` at best |
| `zcz24.csv` | The December 2024 contract's own daily bars, from the same vendor | As above. It has an expiry, so a strategy can trade it |
| `crop_progress.csv` | USDA NASS Crop Progress (nass.usda.gov): weekly corn condition, percent good or excellent | US government work, public domain; cite NASS |
| `wasde.csv` | USDA World Agricultural Supply and Demand Estimates (usda.gov/oce/commodity/wasde): US corn ending stocks, first releases and revisions | Public domain; cite USDA's Office of the Chief Economist |
| `cot.csv` | CFTC Commitments of Traders, disaggregated futures-only report (cftc.gov): managed-money long less short, from June 2006; positions as of Tuesday, released on Friday | Public domain; cite the CFTC |
| `wasde_documents.csv` | The WASDE reports' text, for the tone extraction | Public domain. The extraction is a model's reading, so its features are `exploratory` |

For the tone extraction on real data, record it with a real provider (`qrs extract --record`, after
editing `extraction.json`'s provider, model and budget); the fixture provider answers only the
synthetic reports.

