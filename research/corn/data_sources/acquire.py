"""Bounded public-data acquisition and staging, independent of QRS execution.

Only the reviewed pre-2022 assets in assets.json are downloaded. No vendor endpoint,
credentials, registration, strategy, statistical hypothesis test or QRS store is used.
"""

import argparse
import csv
import hashlib
import io
import json
import math
import re
import urllib.request
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
CUTOFF = date(2021, 12, 31)
LIMIT = 64_000_000
CONDITION_CATEGORIES = ("very_poor", "poor", "fair", "good", "excellent")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_date(value):
    day = date.fromisoformat(value[:10])
    require(day <= CUTOFF, "Observation exceeds the pre-2022 acquisition boundary")
    return day


def digest(data):
    return hashlib.sha256(data).hexdigest()


def content_digest(asset, data):
    # POWER includes request-processing duration in every response. Preserve its raw
    # hash, but ignore only that non-observation field when checking for revisions.
    if asset["file"] == "power2021.json":
        payload = json.loads(data)
        payload.pop("times", None)
        return digest(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return digest(data)


def catalog():
    return json.loads((HERE / "assets.json").read_text())["assets"]


def check_asset(asset):
    require(Path(asset["file"]).name == asset["file"], "Asset filename must be a basename")
    require(safe_date(asset["observation_start"]) <= safe_date(asset["observation_end"]), "Invalid observation interval")
    require(asset["status"] == 200 and re.fullmatch(r"[a-f0-9]{64}", asset["sha256"]), "Unverified asset")
    require(asset["url"].startswith("https://"), "HTTPS required")
    require(
        urlparse(asset["url"]).hostname
        in {
            "www.cftc.gov",
            "www.nass.usda.gov",
            "esmis.nal.usda.gov",
            "power.larc.nasa.gov",
            "www.ncei.noaa.gov",
            "usdmdataservices.unl.edu",
            "noaa-gefs-pds.s3.amazonaws.com",
        },
        "Only reviewed public source hosts are allowed",
    )
    years = re.findall(r"(?<!\d)(20\d{2})", unquote(asset["url"]) + " " + asset["file"])
    require(all(int(year) <= CUTOFF.year for year in years), "URL or filename crosses acquisition boundary")


def checked_bytes(raw, asset):
    check_asset(asset)
    data = (raw / asset["file"]).read_bytes()
    require(
        content_digest(asset, data) == asset.get("content_sha256", asset["sha256"]),
        f"Source content changed: {asset['file']}; review revisions before replacing the pinned hash",
    )
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # All reviewed URLs resolved directly. A redirect could expand the date
        # scope before a response hash can be checked, so require a fresh review.
        raise ValueError("Source redirected; review the target before requesting it")


def fetch(raw, assets):
    # Preflight every asset before the first request. The catalog is the URL allowlist.
    for asset in assets:
        check_asset(asset)
    raw.mkdir(parents=True, exist_ok=True)
    attempts = []
    for asset in assets:
        record = {"file": asset["file"], "url": asset["url"], "retrieved_at": datetime.now(UTC).isoformat()}
        try:
            path = raw / asset["file"]
            if path.exists():
                checked_bytes(raw, asset)
                record["status"] = "cached_verified"
            else:
                request = urllib.request.Request(
                    asset["url"],
                    headers={
                        "User-Agent": "corn-research-source-audit/1.0",
                        "Accept": "application/json" if path.suffix == ".json" else "*/*",
                    },
                )
                opener = urllib.request.build_opener(NoRedirect())
                with opener.open(request, timeout=45) as response:
                    data = response.read(LIMIT + 1)
                    require(len(data) <= LIMIT, "Response exceeds 64 MB cap")
                    record.update(http_status=response.status, resolved_url=response.url, sha256=digest(data), bytes=len(data))
                require(
                    content_digest(asset, data) == asset.get("content_sha256", asset["sha256"]),
                    "Remote content changed; no overwrite. Review new vintage separately",
                )
                path.write_bytes(data)
                record["status"] = "downloaded_verified"
        except (OSError, ValueError) as exc:
            record.update(status="failed", error=str(exc))
        attempts.append(record)
        print(asset["file"], record["status"])
    (raw / "fetch_receipt.json").write_text(json.dumps(attempts, indent=2) + "\n")
    require(all(r["status"] != "failed" for r in attempts), "Some assets unavailable; see fetch_receipt.json")


def section(text, start, end):
    require(start in text, f"Missing section: {start}")
    text = text.split(start, 1)[1]
    require(end in text, f"Missing section end: {end}")
    return start + text.split(end, 1)[0]


def usda_rows(block, count=None):
    """Rows of a USDA text table, by name: those with `count` values, or every row with any values."""
    rows = []
    for line in block.splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z .0-9/]*)\s*:\s*(.*)$", line)
        if match:
            name = re.sub(r"\s*1/", "", match[1]).rstrip(" .")
            values = match[2].split()
            if values and (count is None or len(values) == count) and all(re.fullmatch(r"[\d,]+|-|\(NA\)", v) for v in values):
                rows.append((name, values))
    return rows


def condition_categories(values):
    """One Crop Progress condition row: a count per category, "-" for zero, summing to 100."""
    require(len(values) == len(CONDITION_CATEGORIES), f"Expected {len(CONDITION_CATEGORIES)} condition columns, got {len(values)}")
    counts = [0 if v == "-" else int(v) for v in values]
    require(sum(counts) == 100, "Condition categories do not sum to 100")
    return dict(zip(CONDITION_CATEGORIES, counts, strict=True))


def crop_condition_rows(block):
    """Every row of the condition table, so a row with the wrong number of columns fails rather than being skipped."""
    rows = []
    for state, values in usda_rows(block):
        if state in ("Previous week", "Previous year"):
            continue
        categories = condition_categories(values)
        rows.append(
            categories
            | {
                "week_ending": "2021-09-26",
                "state": state,
                "good_excellent": categories["good"] + categories["excellent"],
                "release_date": "2021-09-27",
                "scheduled_at": "2021-09-27T16:00:00-04:00",
                "clock_status": "historical_calendar_time_not_delivery_log",
                "source_asset": "prog4021.txt",
            }
        )
    return rows


def validate_rows(name, rows, keys, date_column):
    require(bool(rows), f"Empty dataset: {name}")
    seen = set()
    days = []
    for row in rows:
        key = tuple(row[k] for k in keys)
        require(key not in seen, f"Duplicate key in {name}: {key}")
        seen.add(key)
        days.append(safe_date(row[date_column]))
        for value in row.values():
            if isinstance(value, float):
                require(math.isfinite(value), f"Non-finite value in {name}")
    return {"rows": len(rows), "unique_key": keys, "date_column": date_column, "min_date": str(min(days)), "max_date": str(max(days)), "duplicates": 0}


def prepare(raw, out, assets):
    require(not out.exists(), "Output directory already exists; use a new directory to preserve the prior vintage")
    data = {a["file"]: checked_bytes(raw, a) for a in assets}
    outputs = {}
    evidence = {}

    # No nominal Friday timestamp: the annual archives are revised current extracts.
    cot = []
    for name, body in data.items():
        if not name.startswith("cot"):
            continue
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            members = archive.infolist()
            require(len(members) == 1 and members[0].file_size <= 160_000_000, "Unexpected CFTC archive layout")
            reader = csv.DictReader(io.TextIOWrapper(archive.open(members[0]), encoding="utf-8-sig"))
            for r in reader:
                safe_date(r["Report_Date_as_YYYY-MM-DD"])
                if r["CFTC_Contract_Market_Code"].strip() != "002602":
                    continue
                require(r["Market_and_Exchange_Names"].strip() == "CORN - CHICAGO BOARD OF TRADE", "Market identity mismatch")
                long, short, oi = (int(r[k]) for k in ("M_Money_Positions_Long_All", "M_Money_Positions_Short_All", "Open_Interest_All"))
                require(0 <= long <= oi and 0 <= short <= oi, "Invalid CFTC counts")
                cot.append(
                    {
                        "as_of": r["Report_Date_as_YYYY-MM-DD"],
                        "market_code": "002602",
                        "long": long,
                        "short": short,
                        "net": long - short,
                        "open_interest": oi,
                        "published_at": "",
                        "source_asset": name,
                    }
                )
    outputs["cot_corn.csv"] = (sorted(cot, key=lambda r: r["as_of"]), ["market_code", "as_of"], "as_of")

    crop = data["prog4021.txt"].decode("cp1252").replace("\r\n", "\n")
    require("Released September 27, 2021" in crop, "Unexpected Crop Progress vintage")
    block = section(crop, "Corn Condition - Selected States: Week Ending September 26, 2021", "Soybeans Dropping Leaves")
    evidence["crop_progress_excerpt.txt"] = block
    rows = crop_condition_rows(block)
    require(len(rows) == 19, "Expected 18 states and their national aggregate")
    outputs["crop_condition.csv"] = (rows, ["state", "week_ending"], "week_ending")

    wasde = data["wasde0921.txt"].decode("cp1252").replace("\r\n", "\n")
    block = section(wasde, "U.S. Feed Grain and Corn Supply and Use", "U.S. Sorghum, Barley, and Oats")
    evidence["wasde_excerpt.txt"] = block
    corn = block.split("\nCORN\n", 1)[1]
    line = next(line for line in corn.splitlines() if line.startswith("Ending Stocks"))
    values = [int(v) for v in line.split()[2:]]
    require(len(values) == 4 and "Million Bushels" in corn, "WASDE table schema changed")
    rows = [
        {
            "release_date": "2021-09-10",
            "marketing_year": year,
            "column_vintage": vintage,
            "ending_stocks_million_bushels": value,
            "published_at": "",
            "source_asset": "wasde0921.txt",
        }
        for year, vintage, value in zip(["2019/20", "2020/21", "2021/22", "2021/22"], ["Sep", "Sep", "Aug_comparator", "Sep"], values, strict=True)
    ]
    outputs["wasde_corn.csv"] = (rows, ["release_date", "marketing_year", "column_vintage"], "release_date")

    stocks = data["grst0921.txt"].decode("cp1252").replace("\r\n", "\n")
    require("Released September 30, 2021" in stocks, "Unexpected Grain Stocks vintage")
    block = section(stocks, "Grain Stocks by Position and Month in Domestic Units - United States: 2020 and 2021\n[", "Sorghum ")
    evidence["grain_stocks_excerpt.txt"] = block
    rows = []
    for month, values in usda_rows(block, 6):
        for year, v in [(2020, values[:3]), (2021, values[3:])]:
            on, off, total = (int(x.replace(",", "")) for x in v)
            require(on + off == total, "Stocks do not reconcile")
            month_number = {"March 1": 3, "June 1": 6, "September 1": 9}[month]
            rows.append(
                {
                    "as_of": f"{year}-{month_number:02d}-01",
                    "release_date": "2021-09-30",
                    "on_farm_thousand_bushels": on,
                    "off_farm_thousand_bushels": off,
                    "total_thousand_bushels": total,
                    "scheduled_at": "2021-09-30T12:00:00-04:00",
                    "clock_status": "historical_calendar_time_not_delivery_log",
                    "source_asset": "grst0921.txt",
                }
            )
    require(len(rows) == 6, "Expected six corn stock vintage observations")
    outputs["grain_stocks_corn.csv"] = (rows, ["as_of", "release_date"], "as_of")

    geography = data["cropan21.txt"].decode("cp1252").replace("\r\n", "\n")
    require("2020 Summary" in geography[:500] and "January 2021" in geography[:500], "Unexpected crop annual vintage")
    block = section(
        geography,
        "Corn Area Planted for All Purposes and Harvested for Grain, Yield, and Production -\nStates and United States: 2018-2020\n",
        "See footnote(s) at end of table.",
    )
    evidence["geography_excerpt.txt"] = block
    rows = []
    for state, values in usda_rows(block, 6):
        planted = int(values[2].replace(",", ""))
        harvested = "" if values[5] == "(NA)" else int(values[5].replace(",", ""))
        require(harvested == "" or 0 <= harvested <= planted, "Invalid acreage")
        rows.append(
            {
                "crop_year_end": "2020-12-31",
                "state": state,
                "planted_thousand_acres": planted,
                "harvested_grain_thousand_acres": harvested,
                "harvested_missing_code": "NA" if harvested == "" else "",
                "release_date": "2021-01-12",
                "scheduled_at": "2021-01-12T12:00:00-05:00",
                "source_asset": "cropan21.txt",
            }
        )
    national = next(r for r in rows if r["state"] == "United States")
    require(sum(r["planted_thousand_acres"] for r in rows if r is not national) == national["planted_thousand_acres"], "State acres do not reconcile")
    outputs["corn_geography_2020.csv"] = (rows, ["state", "crop_year_end"], "crop_year_end")

    power = json.loads(data["power2021.json"])
    require(power["header"]["time_standard"] == "UTC", "POWER time standard changed")
    pars = power["properties"]["parameter"]
    require(set(pars) == {"T2M_MAX", "T2M_MIN", "PRECTOTCORR"}, "POWER schema changed")
    require(all(set(v) == set(pars["T2M_MAX"]) for v in pars.values()), "POWER date mismatch")
    rows = []
    for day in sorted(pars["T2M_MAX"]):
        values = {k: v[day] for k, v in pars.items()}
        require(all(v != power["header"]["fill_value"] for v in values.values()), "POWER missing data; handle explicitly")
        require(values["T2M_MIN"] <= values["T2M_MAX"] and values["PRECTOTCORR"] >= 0, "Invalid weather")
        rows.append({"date": datetime.strptime(day, "%Y%m%d").date().isoformat(), **values, "published_at": "", "source_asset": "power2021.json"})
    outputs["nasa_power_iowa_2021.csv"] = (rows, ["date"], "date")

    ghcn = json.loads(data["ghcn2021.json"])
    rows = []
    for r in ghcn:
        require(r["STATION"] == "USW00014933", "Unexpected station")
        require(float(r["TMIN"]) <= float(r["TMAX"]) and float(r["PRCP"]) >= 0, "Invalid station weather")
        rows.append(
            {
                "date": r["DATE"],
                "station": r["STATION"],
                "tmax_c": float(r["TMAX"]),
                "tmin_c": float(r["TMIN"]),
                "prcp_mm": float(r["PRCP"]),
                "tmax_attributes": r["TMAX_ATTRIBUTES"],
                "tmin_attributes": r["TMIN_ATTRIBUTES"],
                "prcp_attributes": r["PRCP_ATTRIBUTES"],
                "published_at": "",
                "source_asset": "ghcn2021.json",
            }
        )
    outputs["noaa_ghcn_iowa_2021.csv"] = (rows, ["station", "date"], "date")

    # USDM: permitted local API read; publication rights not assumed from map attribution.
    drought = json.loads(data["drought2021.json"])
    drought_rows = []
    for r in drought:
        require(r["stateAbbreviation"] == "IA" and r["statisticFormatID"] == 1, "Unexpected drought scope")
        levels = [r[f"d{i}"] for i in range(5)]
        require(all(0 <= v <= 100 for v in [r["none"], *levels]), "Invalid drought percentages")
        require(levels == sorted(levels, reverse=True) and abs(r["none"] + levels[0] - 100) < 0.02, "Drought cumulative totals inconsistent")
        drought_rows.append({"date": r["mapDate"][:10]})
    drought_profile = validate_rows("usdm_local_only", drought_rows, ["date"], "date")

    index = data["gefs20210601.idx"].decode()
    require(all(":d=2021060100:" in line for line in index.splitlines()), "Unexpected GEFS initialization")
    require(":APCP:" in index and ":TMP:2 m above ground:" in index, "GEFS required fields absent")
    evidence["gefs_20210601_00_f006.idx"] = index

    # Validate before writing anything; immutable output directories prevent vintage loss.
    receipt = {
        "evidence_class": "retrospective",
        "cutoff": str(CUTOFF),
        "datasets": {},
        "private_usdm_validation": drought_profile,
        "inputs": [
            {k: a[k] for k in ("file", "sha256", "source_id", "retrieved_at", "license_url", "redistributable")}
            | {"prepared_input_sha256": digest(data[a["file"]])}
            for a in assets
        ],
    }
    for name, (rows, keys, date_column) in outputs.items():
        receipt["datasets"][name] = validate_rows(name, rows, keys, date_column)
    out.mkdir(parents=True)
    for name, (rows, _, _) in outputs.items():
        buf = io.StringIO(newline="")
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        body = buf.getvalue().encode()
        (out / name).write_bytes(body)
        receipt["datasets"][name]["sha256"] = digest(body)
    (out / "evidence").mkdir()
    receipt["evidence"] = {}
    for name, value in evidence.items():
        body = value.encode()
        (out / "evidence" / name).write_bytes(body)
        receipt["evidence"][name] = digest(body)
    (out / "manifest.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt["datasets"], indent=2))


def verify(out):
    manifest = json.loads((out / "manifest.json").read_text())
    require(manifest["evidence_class"] == "retrospective" and manifest["cutoff"] == str(CUTOFF), "Invalid evidence scope")
    for name, record in manifest["datasets"].items():
        require(Path(name).name == name, "Invalid sample path")
        body = (out / name).read_bytes()
        require(digest(body) == record["sha256"], f"Sample changed: {name}")
        rows = list(csv.DictReader(io.StringIO(body.decode())))
        profile = validate_rows(name, rows, record["unique_key"], record["date_column"])
        require(all(record[k] == v for k, v in profile.items()), f"Profile mismatch: {name}")
        for row in rows:
            for field in ("published_at", "release_date", "scheduled_at"):
                if row.get(field):
                    safe_date(row[field])
    for name, sha in manifest["evidence"].items():
        require(Path(name).name == name, "Invalid evidence path")
        require(digest((out / "evidence" / name).read_bytes()) == sha, "Evidence changed")
    print(f"Verified {len(manifest['datasets'])} datasets and {len(manifest['evidence'])} evidence excerpts; no QRS execution")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["fetch", "prepare", "verify"])
    parser.add_argument("--raw", type=Path, default=HERE / "raw")
    parser.add_argument("--out", type=Path, default=HERE / "samples")
    args = parser.parse_args()
    if args.action == "verify":
        verify(args.out)
    elif args.action == "fetch":
        fetch(args.raw, catalog())
    else:
        prepare(args.raw, args.out, catalog())


if __name__ == "__main__":
    main()
