"""Acquire bounded 2006–2021 agency history; never run the research programme.

Raw downloads and request receipts stay in ignored raw/history/. The original
pilot and its hashes are immutable. Reuse requires matching the pinned manifest.
"""

import argparse
import csv
import io
import json
import re
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse

from acquire import CUTOFF, HERE, LIMIT, NoRedirect, digest, require, safe_date, validate_rows

START = date(2006, 1, 1)
REGIONS = {
    "IA": {"latitude": 42.03, "longitude": -93.65, "station": "USW00014933", "fips": "19"},
    "IL": {"latitude": 40.05, "longitude": -89.40, "station": "USW00093822", "fips": "17"},
    "NE": {"latitude": 41.50, "longitude": -98.00, "station": "USW00014939", "fips": "31"},
}
PUBLICATIONS = (
    "world-agricultural-supply-and-demand-estimates",
    "crop-progress",
    "grain-stocks",
    "acreage",
    "prospective-plantings",
    "crop-production",
)
HOSTS = {"power.larc.nasa.gov", "www.ncei.noaa.gov", "usdmdataservices.unl.edu", "esmis.nal.usda.gov", "www.cftc.gov"}
NOTICES = ("5737-09", "6745-13", "7864-19")
LICENSES = {
    "power": ("https://registry.opendata.aws/nasa-power/", "CC BY 4.0; attribute NASA POWER", True),
    "ghcn": ("https://registry.opendata.aws/noaa-ghcn/", "NOAA government data; attribute NOAA NCEI", True),
    "usdm": ("https://droughtmonitor.unl.edu/About/Permission.aspx", "Statistics redistribution unverified; local only", False),
    "usda": ("https://www.nal.usda.gov/web-policies-and-important-links", "US government report; attribute USDA agency", True),
    "cftc_notice": ("https://www.cftc.gov/sites/default/files/cftc/cftcprivacy.htm", "US government notice; attribute CFTC", True),
}


def bounded_url(url):
    parsed = urlparse(url)
    require(parsed.scheme == "https" and parsed.hostname in HOSTS, "Unreviewed source host")
    # Reject modern years, including encoded query components, before any request.
    # ESMIS object identifiers such as 3n204067p are opaque, not year 2040.
    # Report dates are checked separately against their archive row below.
    dated_parts = unquote(parsed.query) + " " + Path(unquote(parsed.path)).name
    years = re.findall(r"(?<!\d)(20\d{2})", dated_parts)
    require(all(int(y) <= CUTOFF.year for y in years), "Protected year in URL")


def check_request(name, url, metadata):
    bounded_url(url)
    source = metadata["source"]
    if source in {"power", "ghcn", "usdm"}:
        require((name, url, metadata) in list(weather_requests()), "Unreviewed weather request")
    elif source == "cftc_notice":
        notice = metadata["notice"]
        require(notice in NOTICES and url == f"https://www.cftc.gov/PressRoom/PressReleases/{notice}", "Unreviewed CFTC notice")
    elif source == "usda_index":
        publication = metadata["publication"]
        require(publication in PUBLICATIONS, "Invalid publication")
        if "page" in metadata:
            require(type(metadata["page"]) is int and 0 <= metadata["page"] < 500, "Invalid index page")
            require(url == f"https://esmis.nal.usda.gov/publication/{publication}?page={metadata['page']}", "Unexpected index URL")
        else:
            month = metadata["month"]
            require(re.fullmatch(r"20\d{2}-\d{2}", month), "Invalid archive month")
            require(START <= safe_date(month + "-01"), "Archive before start")
            require(url == f"https://esmis.nal.usda.gov/publication/{publication}?date={month}", "Unbounded archive URL")
    else:
        require(source == "usda" and metadata["publication"] in PUBLICATIONS, "Unknown source")
        check_report_url(url, metadata["release_date"], metadata["publication"])


def check_report_url(url, day, publication):
    bounded_url(url)
    require(START <= safe_date(day), "Report before start")
    require(urlparse(url).hostname == "esmis.nal.usda.gov" and urlparse(url).path.startswith("/sites/default/release-files/"), "Unexpected report endpoint")
    filename = Path(urlparse(url).path).name.lower()
    if filename in {"latest.txt", "latest.pdf"}:
        alias = archived_alias(url)
        require(alias is not None and alias["archive_date"] == day, "Undated archive file has not been reviewed")
        require(alias["head_status"] == 200 and parsedate_to_datetime(alias["last_modified"]).date() <= CUTOFF, "Archive alias exceeds cutoff")
        return
    full = re.search(
        r"-(\d{2})-(\d{2})-(20\d{2})(?:_revision\d*|_correction|_announcement(?:\.txt)?|__announcement|_special_?report|_historical_(?:broiler|dairy)_revisions|_china_rice_revision)?\.(?:txt|pdf|asc)$",
        filename,
    )
    if full:
        require(f"{full[3]}-{full[1]}-{full[2]}" == day, "Filename/date conflict")
        return
    named = re.fullmatch(r"wasde-\d+-([a-z]+)-(\d{1,2})-(20\d{2})\.(?:txt|pdf)", filename)
    if named:
        require(datetime.strptime(f"{named[1]} {named[2]} {named[3]}", "%B %d %Y").date().isoformat() == day, "Named filename/date conflict")
        return
    notice = re.fullmatch(r"(\d{2})-(\d{2})-(\d{2})_report_reschedule_asb_notice_final\.(?:txt|pdf)", filename)
    if notice:
        require(f"20{notice[3]}-{notice[1]}-{notice[2]}" == day, "Notice filename/date conflict")
        return
    compact = re.fullmatch(r"(wasde|prog|grst|acrg|pspl|crop)(\d{2})(\d{2})(?:_revision\d*)?\.(?:txt|pdf)", filename)
    require(compact is not None, "Unrecognized dated report filename; manual review required")
    prefix = dict(zip(PUBLICATIONS, ("wasde", "prog", "grst", "acrg", "pspl", "crop"), strict=True))[publication]
    require(compact[1] == prefix and int(compact[3]) == int(day[2:4]), "Filename/year or publication conflict")
    require(prefix == "prog" or int(compact[2]) == int(day[5:7]), "Filename/month conflict")


def archived_alias(url):
    path = HERE / "archive_aliases.json"
    if not path.exists():
        return None
    return next((r for r in json.loads(path.read_text())["aliases"] if r["url"] == url), None)


def replay_digest(source, body):
    if source == "power":
        payload = json.loads(body)
        payload.pop("times", None)
        return digest(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return digest(body)


def download(raw, name, url, metadata):
    """Immutable first receipt; cached bytes must match it, never silently refresh."""
    require(Path(name).name == name, "Invalid raw filename")
    check_request(name, url, metadata)
    pinned = HERE / "history" / "manifest.json"
    expected = None
    if pinned.exists():
        manifest = json.loads(pinned.read_text())
        records = manifest["inputs"] + manifest.get("indexes", [])
        expected = next((r for r in records if r["url"] == url and r["file"] == name), None)
        require(expected is not None, "Request not in pinned vintage; review catalog before acquisition")
        require(expected["file"] == name and all(expected.get(k) == v for k, v in metadata.items()), "Request metadata contradicts pinned vintage")
    path, receipt_path = raw / name, raw / (name + ".receipt.json")
    if path.exists() or receipt_path.exists():
        require(path.exists() and receipt_path.exists(), "Incomplete cached asset; inspect manually")
        receipt = json.loads(receipt_path.read_text())
        require(receipt["url"] == url and receipt["sha256"] == digest(path.read_bytes()), "Cached source changed")
        require(all(receipt.get(k) == v for k, v in metadata.items()), "Cached source scope changed")
        if expected is not None:
            require(
                replay_digest(metadata["source"], path.read_bytes()) == expected.get("content_sha256", expected["sha256"]),
                "Cached asset contradicts pinned vintage",
            )
        return receipt
    alias = archived_alias(url) if Path(urlparse(url).path).name in {"latest.txt", "latest.pdf"} else None
    if alias is not None:
        head = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "corn-research-source-audit/1.0"})
        with urllib.request.build_opener(NoRedirect()).open(head, timeout=30) as response:
            require(response.status == 200 and response.headers.get("Last-Modified") == alias["last_modified"], "Archive alias metadata changed; refuse body")
    request = urllib.request.Request(url, headers={"User-Agent": "corn-research-source-audit/1.0", "Accept": "*/*"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
        require(response.status == 200, "Expected a complete HTTP 200 source response")
        if alias is not None:
            require(response.headers.get("Last-Modified") == alias["last_modified"], "Archive alias changed after HEAD; refuse body")
        body = response.read(LIMIT + 1)
        require(len(body) <= LIMIT, "Source exceeds size cap")
        receipt = {
            "file": name,
            "url": url,
            "resolved_url": response.url,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "http_status": response.status,
            "sha256": digest(body),
            "bytes": len(body),
            "content_type": response.headers.get("Content-Type"),
            "last_modified": response.headers.get("Last-Modified"),
            **metadata,
        }
        if alias is not None:
            receipt["archive_alias_evidence"] = alias
    if expected is not None:
        require(
            replay_digest(metadata["source"], body) == expected.get("content_sha256", expected["sha256"]),
            "Upstream revision; preserve pinned vintage and review separately",
        )
    raw.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def weather_requests():
    for region, point in REGIONS.items():
        queries = {
            "power": (
                "https://power.larc.nasa.gov/api/temporal/daily/point",
                {
                    "parameters": "T2M_MAX,T2M_MIN,PRECTOTCORR",
                    "community": "AG",
                    "longitude": point["longitude"],
                    "latitude": point["latitude"],
                    "start": "20060101",
                    "end": "20211231",
                    "format": "JSON",
                    "time-standard": "UTC",
                },
            ),
            "ghcn": (
                "https://www.ncei.noaa.gov/access/services/data/v1",
                {
                    "dataset": "daily-summaries",
                    "stations": point["station"],
                    "startDate": "2006-01-01",
                    "endDate": "2021-12-31",
                    "dataTypes": "TMAX,TMIN,PRCP",
                    "units": "metric",
                    "includeAttributes": "true",
                    "includeStationName": "true",
                    "format": "json",
                },
            ),
            "usdm": (
                "https://usdmdataservices.unl.edu/api/StateStatistics/GetDroughtSeverityStatisticsByAreaPercent",
                {
                    "aoi": point["fips"],
                    "startdate": "1/1/2006",
                    "enddate": "12/31/2021",
                    "statisticsType": "1",
                },
            ),
        }
        for source, (url, query) in queries.items():
            yield f"{source}_{region}_2006_2021.json", url + "?" + urlencode(query), {"source": source, "region": region}


def release_links(html, publication, month, *, allow_pagination=False):
    """Only dated rows in the selected month; ignore the separate latest-release table.

    Drupal's noon-UTC date field is NOT a publication clock. Keep just its date.
    """
    require('name="date"' in html and "publication-releases-indexed" in html, "Not the expected archive index")
    require(allow_pagination or 'class="pager__item' not in html, "Paginated archive needs explicit review")
    result = set()
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, flags=re.S):
        match = re.search(r'<time datetime="(\d{4}-\d{2}-\d{2})T', row)
        if not match or not match[1].startswith(month):
            continue
        day = safe_date(match[1])
        require(day >= START, "Report predates acquisition window")
        hrefs = [unescape(href) for href in re.findall(r'href="([^"]+)"', row)]
        selected = [href for href in hrefs if href.lower().endswith((".txt", ".asc"))]
        if publication == PUBLICATIONS[0]:
            # Later WASDE text companions contain tables only; retain full PDFs too.
            selected.extend(href for href in hrefs if href.lower().endswith(".pdf"))
        elif not selected:
            selected = [href for href in hrefs if href.lower().endswith(".pdf")]
        require(selected, f"Dated release {day} has no supported TXT/ASC/PDF; manual review required")
        for href in selected:
            href = unescape(href)
            url = "https://esmis.nal.usda.gov" + href if href.startswith("/") else href
            check_report_url(url, str(day), publication)
            result.add((str(day), url))
    return sorted(result)


def acquire_history(raw, publications):
    raw.mkdir(parents=True, exist_ok=True)
    failures = []
    for notice in NOTICES:
        try:
            download(raw, f"cftc_{notice}.html", f"https://www.cftc.gov/PressRoom/PressReleases/{notice}", {"source": "cftc_notice", "notice": notice})
        except (OSError, ValueError) as exc:
            failures.append({"notice": notice, "error": str(exc)})

    def weather(spec):
        name, url, metadata = spec
        try:
            download(raw, name, url, metadata)
            return None
        except (OSError, ValueError) as exc:
            return {"file": name, "url": url, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=3) as pool:
        failures.extend(r for r in pool.map(weather, weather_requests()) if r)
    print("Weather requests complete", flush=True)

    # Index pages contain release metadata only. Parse dates before following any
    # report link; never fetch the separate latest-release box or a protected file.
    def publication_index(publication):
        found, errors, pages, reached_start = {}, [], [], False
        for page in range(500):
            name = f"index_{publication}_page{page}.html"
            url = f"https://esmis.nal.usda.gov/publication/{publication}?page={page}"
            try:
                receipt = download(raw, name, url, {"source": "usda_index", "publication": publication, "page": page})
                html = (raw / name).read_text()
                require('name="date"' in html and "publication-releases-indexed" in html, "Unexpected archive page")
                dates = re.findall(r'<time datetime="(\d{4}-\d{2}-\d{2})T', html)
                require(dates, "Archive returned no dated rows before reaching start")
                months = sorted({d[:7] for d in dates if str(START) <= d <= str(CUTOFF)})
                for month in months:
                    # Isolate rows so one PDF-only release does not hide its neighbours.
                    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, flags=re.S):
                        if f'datetime="{month}' not in row:
                            continue
                        try:
                            fragment = '<form id="publication-releases-indexed"><select name="date"></select></form><tr>' + row + "</tr>"
                            for day, report_url in release_links(fragment, publication, month):
                                found[report_url] = (day, name, receipt["sha256"])
                        except ValueError as exc:
                            errors.append({"index": name, "month": month, "error": str(exc)})
                pages.append(name)
                if min(dates) < str(START):
                    reached_start = True
                    break
                if page % 10 == 0:
                    print(f"Index {publication}: page {page}", flush=True)
            except (OSError, ValueError) as exc:
                errors.append({"index": name, "error": str(exc)})
                break  # Never jump a missing page and claim complete discovery.
        return {"publication": publication, "pages": pages, "reached_start": reached_start, "errors": errors}, found

    archives, requests = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for record, found in pool.map(publication_index, publications):
            archives.append(record)
            publication = record["publication"]
            requests.extend((publication, url, *evidence) for url, evidence in found.items())
            (raw / "acquisition_status.json").write_text(
                json.dumps({"weather_failures": failures, "archives": archives, "report_failures": []}, indent=2) + "\n"
            )

    def report(spec):
        publication, url, day, index_name, index_sha = spec
        file = f"{publication}_{day}_{digest(url.encode())[:10]}{Path(urlparse(url).path).suffix.lower()}"
        try:
            # A prior monthly-index receipt is equally valid date evidence.
            existing = raw / (file + ".receipt.json")
            if existing.exists():
                old = json.loads(existing.read_text())
                index_name, index_sha = old["date_evidence_file"], old["date_evidence_sha256"]
            download(
                raw,
                file,
                url,
                {
                    "source": "usda",
                    "publication": publication,
                    "release_date": day,
                    "date_evidence_file": index_name,
                    "date_evidence_sha256": index_sha,
                    "publication_clock": "unknown; archive date only",
                },
            )
            return None
        except (OSError, ValueError) as exc:
            return {"url": url, "error": str(exc)}

    report_failures = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, result in enumerate(pool.map(report, requests), 1):
            if result:
                report_failures.append(result)
            if i % 50 == 0:
                print(f"Reports {i}/{len(requests)}", flush=True)
            (raw / "acquisition_status.json").write_text(
                json.dumps({"weather_failures": failures, "archives": archives, "report_failures": report_failures}, indent=2) + "\n"
            )
    require(
        not failures and not report_failures and all(r["reached_start"] and not r["errors"] for r in archives),
        "Acquisition partial; see acquisition_status.json",
    )


def numeric(value):
    if value is None or value == "":
        return ""
    value = float(value)
    require(abs(value) < float("inf"), "Non-finite weather value")
    return value


def weather_payload(source, body):
    if source != "usdm" or body.lstrip().startswith((b"[", b"{")):
        return json.loads(body)
    # The USDM API returns CSV when Accept is */*. Preserve these original bytes.
    rows = []
    for row in csv.DictReader(io.StringIO(body.decode("utf-8-sig"))):
        rows.append(
            {
                "mapDate": datetime.strptime(row["MapDate"], "%Y%m%d").date().isoformat(),
                "stateAbbreviation": row["StateAbbreviation"],
                "statisticFormatID": int(row["StatisticFormatID"]),
                **{key.lower(): float(row[key]) for key in ("None", "D0", "D1", "D2", "D3", "D4")},
            }
        )
    return rows


def report_header(body, publication):
    if body.startswith(b"%PDF-"):
        text = subprocess.run(["pdftotext", "-layout", "-", "-"], input=body, capture_output=True, check=True, timeout=30).stdout.decode()
    else:
        text = body.decode("cp1252").replace("\r\n", "\n")
    head = text[:2500]
    titles = {
        "world-agricultural-supply-and-demand-estimates": r"WASDE[-– ]|World Agricultural Supply",
        "crop-progress": r"Crop\s+Progress",
        "grain-stocks": r"Grain\s+Stocks",
        "acreage": r"Acreage",
        "prospective-plantings": r"Prospective\s+Plantings",
        "crop-production": r"Crop\s+Production",
    }
    if publication == PUBLICATIONS[0] and re.match(
        r"U\.S\. (?:Broiler Meat|Milk) Supply and Use, [Rr]evised May 9, 2008|"
        r"June 9, 2006 China and Global Rice Consumption and Stocks Estimates",
        head,
    ):
        # Separate non-corn attachments; comparison/table dates are not publication dates.
        return "", "non_corn_supplement_date_unresolved"
    # The archive also files these identifiable agency notices alongside reports.
    # Retain them as ancillary evidence, not numerical editions of the publication.
    ancillary = bool(
        re.search(
            r"NASS Continues to Suspend Certain Statistical Estimates and Reports|"
            r"Agriculture Secretary Vilsack Announces Plenary and Dinner Speakers for USDA|"
            r"USDA Announces 2014 Agricultural Outlook Forum Plenary Details|"
            r"USDA Announces Cancellation and Postponement of Selected Reports|"
            r"USDA-NASS Offers Direct Links to Upcoming Reports|"
            r"USDA September 10 Crop Progress Report Delayed|"
            r"REVISIONS TO MILK SUPPLY AND USE AND BROILER STOCKS DATA IN USDA|"
            r"USDA to Issue Corrected Crop Production, WASDE Reports",
            re.sub(r"\s+", " ", head),
            re.I,
        )
    )
    require("<html" not in text.lower() and (ancillary or re.search(titles[publication], head, re.I)), "Unrecognized USDA report identity")
    match = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d{2})", head)
    if not match:
        return "", "ancillary_notice_header_date_unresolved" if ancillary else "header_date_unresolved"
    day = datetime.strptime(f"{match[1]} {match[2]} {match[3]}", "%B %d %Y").date()
    require(START <= safe_date(str(day)), "Report header outside acquisition interval")
    return str(day), "ancillary_notice_header_date_only" if ancillary else "header_date_only_not_delivery_clock"


def weather_rows(source, region, payload, filename):
    rows = []
    if source == "power":
        require(payload["header"]["time_standard"] == "UTC", "POWER time standard changed")
        require(payload["geometry"]["coordinates"][:2] == [REGIONS[region]["longitude"], REGIONS[region]["latitude"]], "POWER location changed")
        params = payload["properties"]["parameter"]
        require(set(params) == {"T2M_MAX", "T2M_MIN", "PRECTOTCORR"}, "POWER parameters changed")
        require(all(set(v) == set(params["T2M_MAX"]) for v in params.values()), "POWER dates differ")
        require(payload["parameters"]["T2M_MAX"]["units"] == "C" and payload["parameters"]["T2M_MIN"]["units"] == "C", "POWER temperature units changed")
        require(payload["parameters"]["PRECTOTCORR"]["units"] == "mm/day", "POWER precipitation units changed")
        for key in sorted(params["T2M_MAX"]):
            values = ["" if params[p][key] == payload["header"]["fill_value"] else numeric(params[p][key]) for p in ("T2M_MAX", "T2M_MIN", "PRECTOTCORR")]
            rows.append(
                {"date": datetime.strptime(key, "%Y%m%d").date().isoformat(), "region": region, "tmax_c": values[0], "tmin_c": values[1], "prcp_mm": values[2]}
            )
    elif source == "ghcn":
        for record in payload:
            require(record["STATION"] == REGIONS[region]["station"], "GHCN station changed")
            rows.append(
                {
                    "date": record["DATE"],
                    "region": region,
                    "station": record["STATION"],
                    "station_name": record["NAME"],
                    **{column: numeric(record.get(field)) for field, column in (("TMAX", "tmax_c"), ("TMIN", "tmin_c"), ("PRCP", "prcp_mm"))},
                    **{field.lower() + "_attributes": record.get(field + "_ATTRIBUTES", "") for field in ("TMAX", "TMIN", "PRCP")},
                }
            )
    else:
        for record in payload:
            require(record["stateAbbreviation"] == region and record["statisticFormatID"] == 1, "USDM scope changed")
            values = {key: numeric(record[key]) for key in ("none", "d0", "d1", "d2", "d3", "d4")}
            require(all(v != "" and 0 <= v <= 100 for v in values.values()), "Invalid drought percentage")
            levels = [values[f"d{i}"] for i in range(5)]
            require(levels == sorted(levels, reverse=True) and abs(values["none"] + values["d0"] - 100) < 0.02, "Invalid cumulative drought areas")
            # API may include the preceding week's overlapping map. Check cutoff before filtering.
            day = safe_date(record["mapDate"][:10])
            if day < START:
                continue
            rows.append({"date": str(day), "region": region, **values})
    for row in rows:
        require(START <= safe_date(row["date"]) <= CUTOFF, "Weather outside requested interval")
        if source != "usdm":
            low, high, rain = (row[k] for k in ("tmin_c", "tmax_c", "prcp_mm"))
            require(low == "" or high == "" or low <= high, "Inverted temperatures")
            require(rain == "" or rain >= 0, "Negative precipitation")
        row.update(published_at="", source_asset=filename)
    validate_rows(filename, rows, ["region", "date"], "date")
    return rows


def csv_bytes(rows):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def region_profile(rows, source):
    result = {}
    expected = {str(START + timedelta(days=n)) for n in range((CUTOFF - START).days + 1) if source != "usdm" or (START + timedelta(days=n)).weekday() == 1}
    for region in REGIONS:
        subset = [r for r in rows if r["region"] == region]
        dates = {r["date"] for r in subset}
        result[region] = {
            "rows": len(subset),
            "missing_dates": sorted(expected - dates),
            "unexpected_dates": sorted(dates - expected),
            "missing_values": {k: sum(r[k] == "" for r in subset) for k in ("tmax_c", "tmin_c", "prcp_mm") if source != "usdm"},
        }
        if source == "ghcn":
            result[region]["quality_flagged"] = {
                key: sum(bool(r[key].split(",")[1]) for r in subset if len(r[key].split(",")) > 1)
                for key in ("tmax_attributes", "tmin_attributes", "prcp_attributes")
            }
            result[region]["trace_precipitation"] = sum(r["prcp_attributes"].startswith("T,") for r in subset)
    return result


def prepare_history(raw, out):
    require(not out.exists(), "Use a new output directory; preserve prior vintage")
    inputs, indexes, outputs, release_rows = [], [], {}, []
    status = json.loads((raw / "acquisition_status.json").read_text())
    for path in sorted(raw.glob("*.receipt.json")):
        record = json.loads(path.read_text())
        source = record["source"]
        fields = {
            "power": ("region",),
            "ghcn": ("region",),
            "usdm": ("region",),
            "cftc_notice": ("notice",),
            "usda_index": ("publication", "page") if "page" in record else ("publication", "month"),
            "usda": ("publication", "release_date", "date_evidence_file", "date_evidence_sha256", "publication_clock"),
        }[source]
        metadata = {k: record[k] for k in ("source", *fields)}
        # Cache exists: this reuses request allowlisting, immutable receipt and
        # committed-vintage checks without performing a network request.
        record = download(raw, record["file"], record["url"], metadata)
        body = (raw / record["file"]).read_bytes()
        require(digest(body) == record["sha256"], "Raw source hash mismatch")
        if source == "usda_index":
            indexes.append(record)
            continue
        licence, basis, shareable = LICENSES[source]
        record.update(license_url=licence, license_basis=basis, redistributable=shareable, evidence_class="retrospective", first_print_verified=False)
        inputs.append(record)
        if source == "power":
            record["content_sha256"] = replay_digest(source, body)
            record["content_hash_excludes"] = ["times (request duration only)"]
        if source == "cftc_notice":
            require(record["notice"].encode() in body and b"<html" in body.lower(), "Unexpected CFTC notice")
            continue
        if source == "usda":
            day = safe_date(record["release_date"])
            require(day >= START, "Report outside interval")
            header_date, header_status = report_header(body, record["publication"])
            check_report_url(record["url"], str(day), record["publication"])
            record["report_header_date"] = header_date
            record["document_kind"] = "ancillary_notice" if header_status.startswith("ancillary_notice") else "report_or_revision"
            if header_status.startswith("non_corn_supplement"):
                record["document_kind"] = "non_corn_supplement"
            record["date_status"] = "archive_header_conflict" if header_date and header_date != str(day) else header_status
            release_rows.append(
                {
                    "publication": record["publication"],
                    "document_kind": record["document_kind"],
                    "release_date": str(day),
                    "published_at": "",
                    "report_header_date": header_date,
                    "date_status": record["date_status"],
                    "clock_status": record["publication_clock"],
                    "source_asset": record["file"],
                    "source_sha256": record["sha256"],
                    "url": record["url"],
                    "date_evidence_file": record["date_evidence_file"],
                    "date_evidence_sha256": record["date_evidence_sha256"],
                }
            )
        else:
            rows = weather_rows(source, record["region"], weather_payload(source, body), record["file"])
            outputs.setdefault(source, []).extend(rows)
    manifest = {
        "evidence_class": "retrospective",
        "cutoff": str(CUTOFF),
        "start": str(START),
        "inputs": inputs,
        "indexes": indexes,
        "datasets": {},
        "acquisition_status": status,
    }
    files = {}
    # Dates announced in notices are distinct from observed publication clocks.
    notices = {r["notice"]: r for r in inputs if r["source"] == "cftc_notice"}
    cot_path = HERE / "samples" / "cot_corn.csv"
    pilot = json.loads((HERE / "samples" / "manifest.json").read_text())
    require(digest(cot_path.read_bytes()) == pilot["datasets"]["cot_corn.csv"]["sha256"], "Original CFTC sample changed")
    cot_ledger = []
    for row in csv.DictReader(io.StringIO(cot_path.read_text())):
        as_of = row["as_of"]
        notice, announced = ("5737-09", "2009-10-20") if as_of < "2009-09-01" else ("", "")
        if as_of == "2018-12-24":
            notice, announced = "7864-19", "2019-02-01"
        evidence = notices.get(notice)
        cot_ledger.append(
            {
                "as_of": as_of,
                "market_code": row["market_code"],
                "published_at": "",
                "announced_available_date": announced if evidence else "",
                "status": "announced_date_not_delivery_log" if evidence else "unknown",
                "notice_url": evidence["url"] if evidence else "",
                "notice_sha256": evidence["sha256"] if evidence else "",
                "positions_source_sha256": pilot["datasets"]["cot_corn.csv"]["sha256"],
            }
        )
    body = csv_bytes(cot_ledger)
    files["cot_release_evidence.csv"] = body
    manifest["datasets"]["cot_release_evidence.csv"] = {
        **validate_rows("cot_release_evidence", cot_ledger, ["market_code", "as_of"], "as_of"),
        "sha256": digest(body),
        "redistributable": True,
        "publication_unknown": len(cot_ledger),
    }
    if release_rows:
        outputs["usda_releases"] = sorted(release_rows, key=lambda r: (r["publication"], r["release_date"], r["source_asset"]))
    for source, rows in outputs.items():
        rows = sorted(rows, key=lambda r: tuple(str(v) for v in r.values()))
        is_usda = source == "usda_releases"
        keys, column = (["publication", "release_date", "source_asset"], "release_date") if is_usda else (["region", "date"], "date")
        name = source + ".csv"
        profile = validate_rows(name, rows, keys, column)
        body = csv_bytes(rows)
        profile.update(sha256=digest(body), redistributable=source != "usdm", publication_unknown=len(rows))
        if not is_usda:
            profile["regions"] = region_profile(rows, source)
        manifest["datasets"][name] = profile
        if source == "usdm":
            # Unresolved statistics redistribution rights: bytes only in ignored private storage.
            private = raw / "prepared_usdm.csv"
            if private.exists():
                require(private.read_bytes() == body, "Private output changed")
            else:
                private.write_bytes(body)
        else:
            files[name] = body
    out.mkdir(parents=True)
    for name, body in files.items():
        (out / name).write_bytes(body)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: {f: v[f] for f in ("rows", "min_date", "max_date")} for k, v in manifest["datasets"].items()}, indent=2))


def verify_history(out, raw=None):
    manifest = json.loads((out / "manifest.json").read_text())
    require(manifest["cutoff"] == str(CUTOFF) and manifest["start"] == str(START) and manifest["evidence_class"] == "retrospective", "History scope changed")
    for name, record in manifest["datasets"].items():
        require(Path(name).name == name, "Invalid output path")
        if not record["redistributable"]:
            require(not (out / name).exists(), "Restricted output in public bundle")
            if raw is None:
                continue
            path = raw / "prepared_usdm.csv"
        else:
            path = out / name
        body = path.read_bytes()
        require(digest(body) == record["sha256"], f"Output hash changed: {name}")
        rows = list(csv.DictReader(io.StringIO(body.decode())))
        profile = validate_rows(name, rows, record["unique_key"], record["date_column"])
        require(all(record[k] == v for k, v in profile.items()), "History profile changed")
        require(all(safe_date(r[record["date_column"]]) >= START for r in rows), "History before start")
        require(all(not r["published_at"] for r in rows), "Unreviewed publication clock")
        require(record["publication_unknown"] == len(rows), "Unknown-publication count changed")
        if name in ("power.csv", "ghcn.csv", "usdm.csv"):
            require(record["regions"] == region_profile(rows, name.removesuffix(".csv")), "Regional profile changed")
    if raw is not None:
        for record in manifest["inputs"] + manifest.get("indexes", []):
            require(Path(record["file"]).name == record["file"], "Invalid input path")
            require(digest((raw / record["file"]).read_bytes()) == record["sha256"], "Input hash changed")
            if record["source"] == "usda":
                require(digest((raw / record["date_evidence_file"]).read_bytes()) == record["date_evidence_sha256"], "Release date evidence changed")
    print(f"Verified {len(manifest['datasets'])} historical datasets; raw verification={raw is not None}; no QRS execution")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["acquire", "prepare", "verify"])
    parser.add_argument("--raw", type=Path, default=HERE / "raw" / "history")
    parser.add_argument("--out", type=Path, default=HERE / "history")
    parser.add_argument("--publications", nargs="+", choices=PUBLICATIONS, default=list(PUBLICATIONS))
    args = parser.parse_args()
    if args.action == "acquire":
        acquire_history(args.raw, args.publications)
    elif args.action == "prepare":
        prepare_history(args.raw, args.out)
    else:
        verify_history(args.out, args.raw if args.raw.exists() else None)


if __name__ == "__main__":
    main()
