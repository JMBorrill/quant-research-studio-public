"""Synthetic unit fixtures only; none are observations or acquired market data."""

import json
import unittest

from live import profile


def fixture():
    return {
        "chart": {
            "result": [
                {
                    "meta": {"exchangeTimezoneName": "UTC"},
                    "timestamp": [1136246400],
                    "indicators": {"quote": [{"open": [1], "high": [2], "low": [1], "close": [3], "volume": [None]}]},
                }
            ]
        }
    }


class ProfileTests(unittest.TestCase):
    def test_flags_inconsistent_prices_and_preserves_missing_volume(self):
        data = fixture()
        result = profile(json.dumps(data).encode())
        self.assertEqual(len(result["invalid_ohlc_dates"]), 1)
        self.assertEqual(result["missing_volume_rows"], 1)
        self.assertEqual(result["zero_volume_rows"], 0)
        self.assertEqual(data, fixture())
        self.assertFalse(result["qrs_import_approved"])

    def test_rejects_out_of_range_observations(self):
        data = fixture()
        data["chart"]["result"][0]["timestamp"] = [1640995200]
        with self.assertRaisesRegex(ValueError, "dates"):
            profile(json.dumps(data).encode())

    def test_rejects_mismatched_fields(self):
        data = fixture()
        data["chart"]["result"][0]["indicators"]["quote"][0]["volume"] = []
        with self.assertRaisesRegex(ValueError, "lengths"):
            profile(json.dumps(data).encode())
