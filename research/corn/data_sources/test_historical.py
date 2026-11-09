"""Offline checks for cutoff, provenance, archive ambiguity and weather quality."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("corn_historical", HERE / "historical.py")
history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(history)


def index(day, filename, extension="txt"):
    return (
        f'<form id="publication-releases-indexed"><select name="date"></select></form><table><tr>'
        f'<td><time datetime="{day}T12:00:00Z">date</time></td>'
        f'<td><a href="/sites/default/release-files/123/{filename}.{extension}">file</a></td></tr></table>'
    )


class HistoryTests(unittest.TestCase):
    def test_reviewed_weather_queries_have_bounded_dates(self):
        for name, url, meta in history.weather_requests():
            history.check_request(name, url, meta)
            with self.assertRaises(ValueError):
                history.check_request(name, url.replace("2021", "2022"), meta)
            with self.assertRaises(ValueError):
                history.check_request(name, url.split("?")[0], meta)

    def test_compact_protected_report_year_rejected_before_request(self):
        for filename, publication in (("wasde1026", history.PUBLICATIONS[0]), ("prog4024", "crop-progress")):
            with tempfile.TemporaryDirectory() as folder, patch.object(history.urllib.request, "build_opener") as opener:
                with self.assertRaises(ValueError):
                    history.download(
                        Path(folder),
                        filename + ".txt",
                        f"https://esmis.nal.usda.gov/sites/default/release-files/123/{filename}.txt",
                        {"source": "usda", "publication": publication, "release_date": "2021-10-01"},
                    )
                opener.assert_not_called()

    def test_latest_release_is_ignored_and_archive_noon_is_not_a_clock(self):
        html = index("2006-01-12", "wasde-01-12-2006") + index("2026-10-09", "wasde1026")
        rows = history.release_links(html, history.PUBLICATIONS[0], "2006-01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "2006-01-12")

    def test_mislabeled_archive_link_is_refused(self):
        for filename in ("wasde1026", "wasde-10-01-2026", "download", "wasde0921"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                history.release_links(index("2006-01-12", filename), history.PUBLICATIONS[0], "2006-01")

    def test_undated_archive_alias_needs_reviewed_pre_cutoff_metadata(self):
        url = "https://esmis.nal.usda.gov/sites/default/release-files/123/latest.txt"
        with tempfile.TemporaryDirectory() as folder, patch.object(history, "HERE", Path(folder)):
            with self.assertRaises(ValueError):
                history.check_report_url(url, "2021-07-12", history.PUBLICATIONS[0])

            alias = {"url": url, "archive_date": "2021-07-12", "head_status": 200, "last_modified": "Mon, 12 Jul 2021 16:04:57 GMT"}
            path = Path(folder) / "archive_aliases.json"
            path.write_text(json.dumps({"aliases": [alias]}))
            history.check_report_url(url, "2021-07-12", history.PUBLICATIONS[0])
            alias["last_modified"] = "Wed, 12 Jul 2023 16:04:57 GMT"
            path.write_text(json.dumps({"aliases": [alias]}))
            with self.assertRaises(ValueError):
                history.check_report_url(url, "2021-07-12", history.PUBLICATIONS[0])

    def test_opaque_archive_ids_are_not_years_but_filenames_still_are(self):
        base = "https://esmis.nal.usda.gov/sites/default/release-files/8336h188j/0v8381910/3n204067p/"
        history.check_report_url(base + "CropProg-10-28-2013.txt", "2013-10-28", "crop-progress")
        with self.assertRaises(ValueError):
            history.check_report_url(base + "CropProg-10-28-2024.txt", "2013-10-28", "crop-progress")

    def test_changed_alias_headers_refuse_get_before_body_request(self):
        url = "https://esmis.nal.usda.gov/sites/default/release-files/123/latest.txt"
        alias = {"url": url, "archive_date": "2021-07-12", "head_status": 200, "last_modified": "Mon, 12 Jul 2021 16:04:57 GMT"}
        with tempfile.TemporaryDirectory() as folder, patch.object(history, "HERE", Path(folder)):
            (Path(folder) / "archive_aliases.json").write_text(json.dumps({"aliases": [alias]}))
            with patch.object(history.urllib.request, "build_opener") as opener:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.status = 200
                response.headers.get.return_value = "Wed, 12 Jul 2023 16:04:57 GMT"
                with self.assertRaisesRegex(ValueError, "refuse body"):
                    history.download(
                        Path(folder) / "raw", "report.txt", url, {"source": "usda", "publication": history.PUBLICATIONS[0], "release_date": "2021-07-12"}
                    )
                self.assertEqual(opener.return_value.open.call_count, 1)
                self.assertEqual(opener.return_value.open.call_args.args[0].method, "HEAD")
                response.read.assert_not_called()

    def test_denial_unsupported_format_and_pagination_are_not_empty_months(self):
        for html in (
            "Access denied",
            index("2006-01-12", "wasde-01-12-2006", "xls"),
            index("2006-01-12", "wasde-01-12-2006") + '<li class="pager__item">next</li>',
        ):
            with self.assertRaises(ValueError):
                history.release_links(html, history.PUBLICATIONS[0], "2006-01")

    def test_pdf_only_dated_release_is_acquired_without_inventing_text(self):
        links = history.release_links(index("2006-01-12", "wasde-01-12-2006", "pdf"), history.PUBLICATIONS[0], "2006-01")
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0][1].endswith(".pdf"))

    def test_wasde_keeps_full_pdf_alongside_tabular_text(self):
        html = index("2021-09-10", "wasde0921")
        html = html.replace("</td></tr>", '<a href="/sites/default/release-files/123/wasde0921.pdf">pdf</a></td></tr>')
        links = history.release_links(html, history.PUBLICATIONS[0], "2021-09")
        self.assertEqual({url.rsplit(".", 1)[1] for _, url in links}, {"txt", "pdf"})

    def test_report_header_date_is_independent_of_archive_date(self):
        day, status = history.report_header(b"WASDE-432 March 10, 2006\nWHEAT: content", history.PUBLICATIONS[0])
        self.assertEqual(day, "2006-03-10")
        self.assertIn("not_delivery_clock", status)
        for body in (b"WASDE-700 January 12, 2022", b"unrelated text", b"<html>WASDE-432 March 10, 2006"):
            with self.assertRaises(ValueError):
                history.report_header(body, history.PUBLICATIONS[0])

    def test_ancillary_notices_are_not_numerical_report_editions(self):
        notices = (
            ("crop-production", b"USDA Announces Cancellation and Postponement of Selected Reports\nWASHINGTON, October 17, 2013"),
            ("crop-production", b"USDA-NASS Offers Direct Links to Upcoming Reports\nIssued Aug. 08, 2018"),
            ("crop-progress", b"USDA September 10 Crop Progress Report Delayed\nIssued Sept. 10, 2018"),
            (history.PUBLICATIONS[0], b"REVISIONS TO MILK SUPPLY AND USE AND BROILER STOCKS DATA IN USDA'S MAY 9 WASDE REPORT\nWashington, May 5, 2008"),
            (history.PUBLICATIONS[0], b"USDA to Issue Corrected Crop Production, WASDE Reports\nIssued October 27, 2008"),
        )
        for publication, body in notices:
            with self.subTest(publication=publication, title=body.splitlines()[0]):
                _, status = history.report_header(body, publication)
                self.assertTrue(status.startswith("ancillary_notice"))
        # A corrected edition containing tables is distinct from a notice announcing one.
        day, status = history.report_header(b"Crop Progress\nReleased August 3, 2015\nUpdate Alert - August 4, 2015", "crop-progress")
        self.assertEqual((day, status), ("2015-08-03", "header_date_only_not_delivery_clock"))

    def test_non_corn_supplement_dates_are_not_publication_dates(self):
        for title in (
            b"U.S. Broiler Meat Supply and Use, Revised May 9, 2008",
            b"U.S. Milk Supply and Use, revised May 9, 2008",
            b"June 9, 2006 China and Global Rice Consumption and Stocks Estimates",
        ):
            with self.subTest(title=title):
                self.assertEqual(history.report_header(title, history.PUBLICATIONS[0]), ("", "non_corn_supplement_date_unresolved"))
        # Ordinary WASDE report dates still follow the report-header path.
        self.assertEqual(
            history.report_header(b"WASDE-456 May 9, 2008\nU.S. Milk Supply and Use", history.PUBLICATIONS[0]),
            ("2008-05-09", "header_date_only_not_delivery_clock"),
        )

    def test_known_ancillary_notice_is_not_a_numerical_report(self):
        body = b"NASS Continues to Suspend Certain Statistical Estimates and Reports\nIssued November 19, 2013"
        self.assertEqual(history.report_header(body, "grain-stocks"), ("2013-11-19", "ancillary_notice_header_date_only"))
        links = history.release_links(index("2014-01-23", "wasde-01-23-2014", "asc"), history.PUBLICATIONS[0], "2014-01")
        self.assertTrue(links[0][1].endswith(".asc"))

    def test_cached_bytes_and_request_scope_are_immutable(self):
        name, url, metadata = next(history.weather_requests())
        with tempfile.TemporaryDirectory() as folder:
            raw = Path(folder)
            (raw / name).write_bytes(b"original")
            receipt = {"url": url, "sha256": history.digest(b"original"), **metadata}
            (raw / (name + ".receipt.json")).write_text(json.dumps(receipt))
            with patch.object(history, "HERE", raw):
                self.assertEqual(history.download(raw, name, url, metadata), receipt)
                (raw / name).write_bytes(b"changed")
                with self.assertRaises(ValueError):
                    history.download(raw, name, url, metadata)

    def test_power_replay_ignores_only_request_duration(self):
        payload = {"times": {"process": 1}, "header": {"api": "1"}, "properties": {"value": 2}}
        original = history.replay_digest("power", json.dumps(payload).encode())
        payload["times"] = {"process": 3}
        self.assertEqual(original, history.replay_digest("power", json.dumps(payload).encode()))
        payload["properties"]["value"] = 5
        self.assertNotEqual(original, history.replay_digest("power", json.dumps(payload).encode()))

    def test_shared_notice_url_can_be_pinned_for_two_publications(self):
        url = "https://esmis.nal.usda.gov/sites/default/release-files/123/01-30-19_Report_Reschedule_ASB_Notice_Final.txt"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "history").mkdir()
            records = []
            for publication in ("grain-stocks", "crop-production"):
                name = publication + ".txt"
                record = {
                    "file": name,
                    "url": url,
                    "source": "usda",
                    "publication": publication,
                    "release_date": "2019-01-30",
                    "sha256": history.digest(b"notice"),
                }
                records.append(record)
                (root / name).write_bytes(b"notice")
                (root / (name + ".receipt.json")).write_text(json.dumps(record))
            (root / "history/manifest.json").write_text(json.dumps({"inputs": records}))
            with patch.object(history, "HERE", root):
                for record in records:
                    metadata = {k: record[k] for k in ("source", "publication", "release_date")}
                    self.assertEqual(history.download(root, record["file"], url, metadata), record)

    def test_cached_and_fresh_assets_must_match_committed_pin(self):
        name, url, metadata = next(s for s in history.weather_requests() if s[2]["source"] == "ghcn")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "history").mkdir()
            (root / "history/manifest.json").write_text(json.dumps({"inputs": [{"file": name, "url": url, "sha256": history.digest(b"pinned"), **metadata}]}))
            raw = root / "raw"
            raw.mkdir()
            (raw / name).write_bytes(b"changed")
            receipt = {"url": url, "sha256": history.digest(b"changed"), **metadata}
            (raw / (name + ".receipt.json")).write_text(json.dumps(receipt))
            with patch.object(history, "HERE", root), self.assertRaisesRegex(ValueError, "contradicts pinned"):
                history.download(raw, name, url, metadata)
            (raw / name).unlink()
            (raw / (name + ".receipt.json")).unlink()
            with patch.object(history, "HERE", root), patch.object(history.urllib.request, "build_opener") as opener:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.read.return_value = b"changed"
                response.url, response.status = url, 200
                response.headers.get.return_value = "application/json"
                with self.assertRaisesRegex(ValueError, "Upstream revision"):
                    history.download(raw, name, url, metadata)
                self.assertFalse((raw / name).exists())

    def test_preparation_cannot_bypass_committed_pin(self):
        name, url, metadata = next(s for s in history.weather_requests() if s[2]["source"] == "ghcn")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "history").mkdir()
            expected = {"file": name, "url": url, "sha256": history.digest(b"pinned"), **metadata}
            (root / "history/manifest.json").write_text(json.dumps({"inputs": [expected]}))
            raw = root / "raw"
            raw.mkdir()
            (raw / "acquisition_status.json").write_text("{}")
            (raw / name).write_bytes(b"changed")
            (raw / (name + ".receipt.json")).write_text(json.dumps({**expected, "sha256": history.digest(b"changed")}))
            with patch.object(history, "HERE", root), self.assertRaisesRegex(ValueError, "contradicts pinned"):
                history.prepare_history(raw, root / "new-output")
            self.assertFalse((root / "new-output").exists())

    def test_drought_overlap_and_cumulative_categories(self):
        row = {"mapDate": "2006-01-03", "stateAbbreviation": "IA", "statisticFormatID": 1, "none": 30, "d0": 70, "d1": 50, "d2": 25, "d3": 10, "d4": 0}
        old = {**row, "mapDate": "2005-12-27"}
        self.assertEqual(len(history.weather_rows("usdm", "IA", [old, row], "source")), 1)
        for change in ({"mapDate": "2022-01-04"}, {"d2": 80}, {"none": 31}, {"stateAbbreviation": "NE"}):
            with self.assertRaises(ValueError):
                history.weather_rows("usdm", "IA", [{**row, **change}], "source")
        with self.assertRaises(ValueError):
            history.weather_rows("usdm", "IA", [row, row], "source")

    def test_ghcn_missing_values_and_flags_are_preserved(self):
        row = {
            "DATE": "2006-01-01",
            "STATION": "USW00014933",
            "NAME": "DES MOINES",
            "TMAX": "2",
            "TMIN": "-5",
            "TMAX_ATTRIBUTES": ",,0",
            "PRCP_ATTRIBUTES": "T,,0,2400",
        }
        result = history.weather_rows("ghcn", "IA", [row], "source")[0]
        self.assertEqual(result["prcp_mm"], "")
        self.assertEqual(result["prcp_attributes"], "T,,0,2400")
        self.assertEqual(result["published_at"], "")
        with self.assertRaises(ValueError):
            history.weather_rows("ghcn", "IA", [{**row, "TMIN": "3"}], "source")

    def test_verify_checks_indexes_without_report_references(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = root / "raw"
            raw.mkdir()
            record = {"file": "unreferenced-index.html", "source": "usda_index", "sha256": history.digest(b"index")}
            manifest = {
                "start": str(history.START),
                "cutoff": str(history.CUTOFF),
                "evidence_class": "retrospective",
                "datasets": {},
                "inputs": [],
                "indexes": [record],
            }
            (root / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(FileNotFoundError):
                history.verify_history(root, raw)
            (raw / record["file"]).write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "Input hash changed"):
                history.verify_history(root, raw)
            (raw / record["file"]).write_bytes(b"index")
            history.verify_history(root, raw)

    def test_non_finite_weather_is_rejected(self):
        for value in ("NaN", "inf", "-inf"):
            with self.assertRaises(ValueError):
                history.numeric(value)


if __name__ == "__main__":
    unittest.main()
