# Corn public data acquisition

The follow-on 2006–2021 acquisition is documented in [HISTORY.md](HISTORY.md).
It adds a separately pinned vintage and supersedes the pilot's current acquisition
status and vendor recommendation. Its protected-period exposure record also supersedes
the pilot boundary claims below. The pilot files and historical hashes remain unchanged.

Checked 3 November 2026. This is an acquisition and quality-control package, not a research result.
Read [SOURCE_REGISTER.md](SOURCE_REGISTER.md) for coverage, rights, costs and outstanding decisions,
and [VALIDATION.md](VALIDATION.md) for the measured scope of this delivery.

## Boundaries

- Requested data observations/editions are before 2022; no contract-price dataset was acquired.
  On 3 November 2026, unsolicited search snippets exposed the assistant to 2024 corn quotes.
  This session cannot claim an untouched 2024–2025 holdout; see the
  [exposure and lineage record](HISTORY.md#protected-period-exposure-and-lineage).
- No QRS store was opened; no family was registered; no confirmatory test, backtest, search or
  `research/corn/run.py` execution occurred. The existing synthetic declarations stay unchanged.
- Weather and geography acquisition is authorized by this task; it does not add a weather hypothesis
  to the programme or resolve D3–D5 in [the audit](../real-data-plan.md).
- The original audit is retained as a dated record. This source register supersedes its acquisition
  status, not its design decisions. This PR builds on audit commit `b222135` and targets `main`.

## Reproduce

Python standard library only (Python 3.12+); no new dependencies. From the repository root:

```sh
# Offline: check the committed public samples, keys, bounds and hashes.
python research/corn/data_sources/acquire.py verify
python -m unittest discover -s research/corn/data_sources -p 'test_*.py' -v

# Online: fetch only the explicit reviewed assets; raw/ is ignored by Git.
python research/corn/data_sources/acquire.py fetch

# Offline staging from those raw files; use a NEW directory to preserve vintages.
python research/corn/data_sources/acquire.py prepare --out /tmp/corn-pre2022-review
```

`assets.json` records every successful raw request, resolved URL, retrieval timestamp, byte count,
SHA-256, date bounds, rights evidence and redistribution decision. `fetch` caches verified files,
caps downloads, records failures and refuses redirects or changed content. Review upstream revisions before
updating the pinned hashes. NASA POWER includes changing request durations: only its `times` field
is excluded from its additional canonical-content hash; observations and metadata remain checked.
The original raw hash and the bytes actually prepared are both retained. There is no arbitrary URL,
year-range, vendor or credential option. Adding another source or year is a reviewable code/catalog
change, never an automatic expansion into a holdout.

`prepare` is an import/staging script, **not an executable full-programme importer**. It extracts the
corn market from CFTC annual files, parses dated USDA tables, retains weather flags and emits the
small CSVs under `samples/`. The manifest includes checksums and per-dataset keys, counts and bounds.
The four USDA excerpts preserve table context. The GEFS index proves only field/forecast-file
availability, not downloaded gridded forecasts. CDL metadata was acquired and checked; no raster
or field boundary is claimed. Full upstream files remain outside the commit.

USDM statistics were legally retrieved through its public API and validated locally. Its permission
page specifies map attribution but did not establish a blanket statistics redistribution licence;
only request metadata and validation counts are committed. The downloader will retain that response
in ignored `raw/`; the staging script never emits its values into shareable samples.

## Before a QRS import

These CSVs deliberately preserve more dimensions than the existing fixture declarations:

| Staging file | Mapping still required |
|---|---|
| `cot_corn.csv` | `as_of` → `AsOf`; `net` → `ManagedMoneyNet`; verified actual release ledger → `Published`. Never set publication to observation Tuesday or mechanically to Friday. |
| `crop_condition.csv` | Select `18 States`, not an unweighted state mean; `week_ending` → `Week`; `good_excellent` → `Condition`; resolve actual `Published` from release evidence. |
| `wasde_corn.csv` | Choose and document the marketing-year rule; exclude `Aug_comparator` as a new release; `release_date` identifies the report vintage. Do not merge different crop years into one observation. |
| `grain_stocks_corn.csv` | Units are **thousand bushels**, unlike WASDE million bushels. Older dates are estimates printed in the September 2021 vintage, not their first releases. |
| `corn_geography_2020.csv` | State acreage weights are available only after the January 2021 release; seven harvested-acreage `NA`s remain missing. National total is a check, not another state. |
| Weather CSVs | POWER is a model grid point near Ames; GHCN is Des Moines airport. Neither is an acreage-weighted Corn Belt series or an archived trading-time weather feed. |

Blank `published_at` is intentional. `scheduled_at` is a historical calendar time, explicitly not
a delivery log. No script replaces unknown publication times with plausible values. Fill a reviewed
release ledger before generating QRS observations; retain `evidence_class: retrospective` for these
downloads. A future point-in-time pipeline needs actual acquisition logs and immutable vintages.

Market data must stay in separate private storage. Record hashes, provider product IDs, entitlements,
session definitions, settlement/close distinction, contract expiries, first-notice dates, roll dates
and adjustment amounts in a manifest. Do not commit the vendor CSVs or reuse synthetic declarations
for real prices. Do not run the end-to-end driver as a way of testing an importer.

## Attribution

The samples are transformations by this project, not official agency products. Credit USDA NASS,
USDA Office of the Chief Economist, CFTC, NOAA NCEI, NOAA NCEP and NASA POWER as identified in each
source record. NASA POWER data are supplied under the attribution terms linked in `assets.json`
([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)); this project reformatted the returned
daily values and did not alter them. No agency endorsement is implied. Source-specific terms are
recorded separately from any repository code licence.
