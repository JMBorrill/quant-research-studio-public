"""Offline probe checks; fixture headers are synthetic, not market observations."""

import io
import unittest
import urllib.error
from unittest.mock import patch

import probe


class ProbeTests(unittest.TestCase):
    def test_success_is_head_only_and_does_not_approve_acquisition(self):
        with patch.object(probe.urllib.request, "build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.headers = {"Content-Type": "application/zip"}
            result = probe.probe()
            self.assertEqual(opener.return_value.open.call_args.args[0].method, "HEAD")
            self.assertEqual(opener.return_value.open.call_args.args[0].full_url, probe.URL)
            response.read.assert_not_called()
            self.assertFalse(result["acquisition_approved"])
            self.assertFalse(result["price_body_requested"])

    def test_http_failure_is_recorded_without_reading_body(self):
        body = io.BytesIO(b"not inspected")
        error = urllib.error.HTTPError(probe.URL, 403, "Forbidden", {}, body)
        with patch.object(probe.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            result = probe.probe()
        self.assertEqual(result["status"], 403)
        self.assertFalse(result["acquisition_approved"])
        self.assertTrue(body.closed)

    def test_redirect_is_refused(self):
        with self.assertRaises(ValueError):
            probe.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.com/latest.csv")


if __name__ == "__main__":
    unittest.main()
