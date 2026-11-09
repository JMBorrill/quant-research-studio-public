"""Runs the corn research programme from end to end through the service's API: import the datasets,
extract the reports' tone, create the project, explore before the confirmation start, search a
trend-following rule, register both families, evaluate every hypothesis, then build and verify the
report. It uses only the public API and the `qrs` commands (`qrs extract` runs in this process, on the
service's data directory, so it runs where the service does), so the core knows nothing of corn. A
programme runs once in a store: its families can be registered only once.

    qrs serve --home .qrs &                      # the service, whose workers run the jobs
    python research/corn/run.py --home .qrs      # the fixtures; --data DIR for the real data

With real data, DIR holds files of the fixtures' names, as research/corn/README.md describes, and the
declarations are edited to match the provider's columns and the continuous series' contracts."""

import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
DATASETS = ("continuous", "zcz24", "crop_progress", "wasde", "cot", "wasde_documents")


def fill(document, ids: dict[str, str]):
    """A programme document with its ${name} placeholders replaced by the IDs found so far."""
    text = re.sub(r"\$\{([a-z_:]+)\}", lambda m: ids[m[1]], json.dumps(document))
    return json.loads(text)


def load(name: str, ids: dict[str, str]) -> dict:
    return fill(json.loads((HERE / name).read_text()), ids)


def ok(response) -> dict:
    if response.status_code != 200:
        raise RuntimeError(f"{response.request.method} {response.request.url}: {response.status_code} {response.text}")
    return response.json()


def wait(client, path: str, done, timeout: float = 600) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        found = ok(client.get(path))
        if done(found):
            return found
        if time.monotonic() > deadline:
            raise TimeoutError(f"{path} did not finish: {found}")
        time.sleep(0.2)


def finished(info: dict) -> bool:
    return info["status"] not in ("queued", "running")


def run(client, home: Path, data: Path) -> dict:
    """The programme on the service at `client` (an httpx or FastAPI test client) whose data directory
    is `home`, with the CSV files in `data`. Returns every ID and the built report."""
    from qrs import cli

    ids: dict[str, str] = {"answers": str(HERE / "fixtures" / "answers.json")}
    for name in DATASETS:
        declaration = (HERE / "declarations" / f"{name}.json").read_text()
        csv = (data / f"{name}.csv").read_bytes()
        ids[name] = ok(client.post("/api/datasets/import", params={"declaration": declaration}, content=csv, headers={"Content-Type": "text/csv"}))["id"]
    ids["contract"] = ids["zcz24"]

    ids["project"] = ok(client.post("/api/projects", json=load("project.json", ids)))["id"]
    strategy = ok(client.post("/api/strategies", json={"template_id": "ema_crossover", "dataset_id": ids["contract"]}))
    ids["strategy"] = strategy["id"]
    experiment = ok(client.post("/api/experiments", json=load("experiment.json", ids)))
    ids["experiment"] = experiment["id"]
    for trial in experiment["trials"]:
        wait(client, f"/api/runs/{trial['run']['id']}", finished)

    # Both families are registered before anything reads the test period: confirmatory.
    for family in ("market", "strategy"):
        ids[f"family:{family}"] = ok(client.post("/api/families", json=load(f"families/{family}.json", ids)))["id"]

    # The tone of each report, read by a model: replayed through the fixture provider, so offline. An
    # extraction reads every document, test period included, so it comes after registration (the
    # reports are outside the project's scope in any case). It runs as `qrs extract` does, on the
    # service's own data directory.
    extraction = home / "corn-extraction.json"
    extraction.write_text(json.dumps(load("extraction.json", ids)))
    if cli.main(["extract", str(extraction), "--home", str(home), "--record"]) != 0:
        raise RuntimeError("The tone extraction failed.")
    listed = ok(client.get("/api/datasets"))
    ids["tone"] = next(d["id"] for d in listed if d.get("derivation") and d["derivation"]["documents"] == ids["wasde_documents"])

    # Exploration, capped at the confirmation start because it is in the project.
    for path in sorted((HERE / "analyses").glob("*.json")):
        queued = ok(client.post("/api/analyses", json=load(f"analyses/{path.name}", ids)))
        info = wait(client, f"/api/analyses/{queued['id']}", finished)
        if info["status"] != "succeeded":
            raise RuntimeError(f"{path.stem}: {info['error']}")
        ids[f"analysis:{path.stem}"] = queued["id"]

    # The test period, read only by the families' evaluations.
    for family in ("market", "strategy"):
        family_id = ids[f"family:{family}"]
        for hypothesis in json.loads((HERE / "families" / f"{family}.json").read_text())["hypotheses"]:
            ok(client.post(f"/api/families/{family_id}/evaluate", json={"hypothesis": hypothesis["id"]}))
        wait(client, f"/api/families/{family_id}", lambda f: all(o["state"] not in ("running", "not evaluated") for o in f["outcomes"]))

    report = ok(client.post("/api/reports", json=load("report.json", ids)))
    verified = ok(client.post(f"/api/reports/{report['id']}/verify"))
    return {"ids": ids, "report": report, "verified": verified}


def main(argv: list[str]) -> int:
    import httpx

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, required=True, help="The service's data directory.")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="The service.")
    parser.add_argument("--data", type=Path, default=HERE / "fixtures", help="The CSV files (default: the synthetic fixtures).")
    args = parser.parse_args(argv)
    with httpx.Client(base_url=args.url, timeout=120) as client:
        found = run(client, args.home, args.data)
    print(f"report {found['report']['id']}: {'verified' if found['verified']['verified'] else found['verified']['problems']}")
    return 0 if found["verified"]["verified"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
