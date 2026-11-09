"""Record headers from the reviewed TurtleTrader archive; never fetch price bodies.

This access probe is not a downloader or import approval. The archive's date
coverage and redistribution rights remain unverified even after HTTP 200.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from acquire import NoRedirect  # noqa: E402

URL = "https://www.turtletrader.com/cddata/c.zip"


def probe():
    record = {"url": URL, "method": "HEAD", "attempted_at": datetime.now(UTC).isoformat(), "price_body_requested": False}
    request = urllib.request.Request(URL, method="HEAD")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            record.update(status=response.status, headers={k: response.headers.get(k) for k in ("Content-Type", "Content-Length", "Last-Modified")})
    except urllib.error.HTTPError as exc:
        record.update(status=exc.code, headers={k: exc.headers.get(k) for k in ("Content-Type", "Content-Length", "Last-Modified")})
        exc.close()
    except (OSError, ValueError) as exc:
        record.update(status=None, error_type=type(exc).__name__)
    record["acquisition_approved"] = False
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New metadata receipt; existing evidence is never overwritten")
    args = parser.parse_args()
    with args.out.open("x") as stream:
        json.dump(probe(), stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
