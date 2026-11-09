"""Offline ingestion checks only; no research tests, services or market data."""

import copy
import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("corn_acquire", HERE / "acquire.py")
acquire = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acquire)


class AcquisitionTests(unittest.TestCase):
    def test_all_catalog_entries_pass_preflight(self):
        for asset in acquire.catalog():
            acquire.check_asset(asset)

    def test_holdout_and_post_exploration_dates_rejected(self):
        for day in ("2022-01-01", "2024-01-01", "2025-12-31"):
            with self.subTest(day=day), self.assertRaises(ValueError):
                acquire.safe_date(day)

    def test_protected_request_refused_before_network(self):
        assets = copy.deepcopy(acquire.catalog())
        assets[-1]["observation_end"] = "2024-12-31"
        with tempfile.TemporaryDirectory() as folder, patch.object(acquire.urllib.request, "build_opener") as request:
            with self.assertRaises(ValueError):
                acquire.fetch(Path(folder), assets)
            request.assert_not_called()

    def test_redirect_refused_before_following_target(self):
        handler = acquire.NoRedirect()
        with self.assertRaises(ValueError):
            handler.redirect_request(None, None, 302, "Found", {}, "https://www.cftc.gov/files/2024.zip")

    def test_protected_url_cannot_claim_old_dates(self):
        asset = copy.deepcopy(acquire.catalog()[0])
        asset["url"] = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_2024.zip"
        with self.assertRaises(ValueError):
            acquire.check_asset(asset)

    def test_duplicate_observations_rejected(self):
        row = {"date": "2021-01-01", "station": "A"}
        with self.assertRaises(ValueError):
            acquire.validate_rows("test", [row, row], ["station", "date"], "date")

    def test_changed_source_hash_rejected(self):
        asset = acquire.catalog()[0]
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / asset["file"]).write_text("changed upstream content")
            with self.assertRaises(ValueError):
                acquire.checked_bytes(Path(folder), asset)

    def test_power_processing_time_is_not_a_revision_but_values_are(self):
        asset = {"file": "power2021.json"}
        original = {"times": {"process": 0.1}, "properties": {"value": 5}}
        second = copy.deepcopy(original)
        second["times"]["process"] = 0.9
        third = copy.deepcopy(original)
        third["properties"]["value"] = 6

        def fingerprint(obj):
            return acquire.content_digest(asset, json.dumps(obj).encode())

        self.assertEqual(fingerprint(original), fingerprint(second))
        self.assertNotEqual(fingerprint(original), fingerprint(third))

    def test_usda_zero_and_suppressed_tokens_not_conflated(self):
        parsed = acquire.usda_rows("Ohio .....: - 1 20 50 29\nMaine .....: 1 2 (NA) 4 5\n18 States .: 5 10 26 45 14", 5)
        self.assertEqual(parsed[0][1][0], "-")
        self.assertEqual(parsed[1][1][2], "(NA)")
        self.assertEqual(parsed[2][0], "18 States")

    def test_condition_rows_need_exactly_one_value_per_category(self):
        for values in (["10", "20", "30", "40"], ["10", "20", "30", "20", "10", "10"]):
            with self.subTest(columns=len(values)), self.assertRaisesRegex(ValueError, "Expected 5 condition columns"):
                acquire.condition_categories(values)

    def test_a_condition_row_with_the_wrong_column_count_fails_the_table(self):
        lines = self.excerpt().splitlines()
        iowa = next(i for i, line in enumerate(lines) if line.startswith("Iowa"))
        lines[iowa] += "  0"
        block = "\n".join(lines)
        with self.assertRaisesRegex(ValueError, "Expected 5 condition columns, got 6"):
            acquire.crop_condition_rows(block)

    def test_condition_categories_must_sum_to_100(self):
        with self.assertRaisesRegex(ValueError, "do not sum to 100"):
            acquire.condition_categories(["10", "20", "30", "20", "19"])

    def test_committed_condition_excerpt_parses_to_the_committed_rows(self):
        # As ordered pairs: dict equality would ignore the column order, which the CSV's pinned hash depends on.
        parsed = [[(k, str(v)) for k, v in row.items()] for row in acquire.crop_condition_rows(self.excerpt())]
        self.assertEqual(parsed, [list(row.items()) for row in self.rows("crop_condition.csv")])

    def test_committed_samples_and_evidence_match_hashes(self):
        acquire.verify(HERE / "samples")

    def test_cot_market_counts_and_unresolved_clocks(self):
        rows = self.rows("cot_corn.csv")
        self.assertEqual(len(rows), 812)
        self.assertEqual({r["market_code"] for r in rows}, {"002602"})
        self.assertTrue(all(not r["published_at"] for r in rows))
        self.assertTrue(all(int(r["net"]) == int(r["long"]) - int(r["short"]) for r in rows))

    def test_independent_usda_anchor_values(self):
        # Anchors manually checked against the dated source tables, not computed by the parser.
        wasde = self.rows("wasde_corn.csv")
        current = next(r for r in wasde if r["marketing_year"] == "2021/22" and r["column_vintage"] == "Sep")
        self.assertEqual(current["ending_stocks_million_bushels"], "1408")
        crop = next(r for r in self.rows("crop_condition.csv") if r["state"] == "18 States")
        self.assertEqual(crop["good_excellent"], "59")
        stocks = next(r for r in self.rows("grain_stocks_corn.csv") if r["as_of"] == "2021-09-01")
        self.assertEqual(stocks["total_thousand_bushels"], "1236481")
        national = next(r for r in self.rows("corn_geography_2020.csv") if r["state"] == "United States")
        self.assertEqual(national["planted_thousand_acres"], "90819")

    def test_weather_coverage_and_flags_preserved(self):
        for filename in ("nasa_power_iowa_2021.csv", "noaa_ghcn_iowa_2021.csv"):
            rows = self.rows(filename)
            self.assertEqual(len({r["date"] for r in rows}), 365)
            self.assertEqual(min(r["date"] for r in rows), "2021-01-01")
            self.assertEqual(max(r["date"] for r in rows), "2021-12-31")
        ghcn = self.rows("noaa_ghcn_iowa_2021.csv")
        self.assertEqual(sum(r["prcp_attributes"].startswith("T,") for r in ghcn), 43)

    def excerpt(self):
        return (HERE / "samples" / "evidence" / "crop_progress_excerpt.txt").read_text()

    def rows(self, name):
        with (HERE / "samples" / name).open(newline="") as handle:
            return list(csv.DictReader(handle))


if __name__ == "__main__":
    unittest.main()
