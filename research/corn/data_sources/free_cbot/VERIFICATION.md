# Verification evidence — 2026-11-04

Scope: documentation and a fixed-URL, header-only archive access probe. No market
data or QRS importer/engine changes. This directory is added to upstream main
`eb78cbb43f93ddec8b4a938508b5488d3ea2bf14`; PR #43 is not included.

| Local check | Result |
| --- | --- |
| `python3 -m unittest discover -s research/corn/data_sources/free_cbot -p 'test_*.py'` | 3 passed: HEAD-only/no body on success; HTTP failure recording; redirect refusal. |
| `python3 -m unittest discover -s research/corn/data_sources -p 'test_acquire.py'` | 17 existing main acquisition tests passed. |
| `python3 research/corn/data_sources/acquire.py verify` | 7 original datasets and 5 evidence excerpts match their hashes and profiles. No QRS execution. |
| `uvx ruff check research/corn/data_sources/free_cbot` | Passed. |
| `uvx ruff format --check research/corn/data_sources/free_cbot` | Passed. |
| `git diff --check` | Passed for tracked workspace changes; new files also checked before publication. |
| Historical files | `assets.json`, `samples/` and `acquire.py` have no local changes. Remote main's `acquire.py` blob is `46836a1f0414c2d5c07b4ad744796aa1ddf4bbec`, matching the tested local file. |

Additional compatibility check in the workspace containing unmerged PR #43:
`python3 -m unittest discover -s research/corn/data_sources -p 'test_*.py'`
passed all 40 tests. This is supplementary evidence, not a claim that this
main-based PR contains those 40 tests. The new three-test suite runs separately.

The actual HEAD attempt returned HTTP 403 at
`2026-11-04T16:10:16.117774+00:00`. The committed `turtle_head.json` receipt has
SHA-256 `06c33f84c4f2f9a65b55ae256f2b917e4358dae5ede1c521f31f1b7eda556007`.
This hashes our metadata receipt, **not a downloaded archive**. No source body
was acquired, and no invented raw-data manifest or empty QRS import was created.

Independent review inspected the code, tests, receipt, source comparisons and
main-based dependency. It found no material code or boundary defect. Its wording
finding was fixed: the comparison now distinguishes FinzData's one-time price
from Barchart's cheaper but uncleared subscription. This verification record
resolves its missing-document finding and separates main tests from PR #43 tests.

Market-data quality, independent price overlap, full coverage, expiry and
point-in-time validation remain blocked, as detailed in `README.md`. Application,
studio and browser suites were not rerun for this isolated probe/documentation
change. Hosted CI is unavailable. No backtest, research evaluation, family
registration or confirmatory test was run.
