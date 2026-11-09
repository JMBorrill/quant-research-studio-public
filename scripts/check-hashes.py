"""Historical hashes every change must keep: the Stage 2 demo's dataset, and its code and result
through `qrs run` and its exported bundle; and each bars-only template's code and result on its
fixture through `qrs run`, recorded on Linux x86_64 at Phase 4A (5624dda); and the two templates
reading features, on their fixtures with their features replayed, recorded at Stage 5 (7dcb178). Dataset IDs and code
hashes are compared everywhere; result hashes only on Linux x86_64, as metrics may differ in their
last bits on other platforms. Used by the local gate.

    uv run python scripts/check-hashes.py"""

import json
import platform
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from qrs import importer, templates

DEMO_DATASET = "sha256:91a9b5201e1f05417d89662020f3b5760a195d26518b8030daba8af04e7c9248"
EXPECTED = {  # strategy: (code_sha256, result_sha256)
    "demo": ("d8fad9fae5a10cc52f7eb53bb08c45b56aa70118e1f516bc577809468c81d1ec", "24357159ece368e027d8249f2065a87ae0725b2bec05353262289125d6239a6c"),
    "demo bundle": ("d8fad9fae5a10cc52f7eb53bb08c45b56aa70118e1f516bc577809468c81d1ec", "24357159ece368e027d8249f2065a87ae0725b2bec05353262289125d6239a6c"),
    "bollinger_rsi": ("fff3fd9a6189ec6ebce7d6eca8abc447cff737e43f74589676fe6c605d700cf3", "9debb8e16178be49aad522a2af422c4beca4b78afe58d44eae6c7dbf2be3e173"),
    "ema_crossover": ("2e0a0d8420ce5398b84ee69aa9cbc70ddd39659a99b7ec387cead8fff8d53e48", "277a3eab0cdc5709343c36c508120a4b432d8a9016ff3bee8928d27f053efade"),
    "sma_crossover": ("c280fe54eae05393bc3e7f4710084281e6699f01506dab2e31c4d0941ac77e56", "336b2b5336a0cc657cd6da8f628052f8240081724908f07faca4ba7123352dda"),
    "ridge_forecast": ("bfd0909dac1f0f19137e31e5db6fa940b02aa3a5d77957eddba619e40509da28", "721ab23d3e4f93e6ab383147eb5f6a578267ca2e64cc50bd29a1006f78d86cfa"),
    "macro_ridge": ("de7e133cc89a05c97c1ac0b6c688e8cb1fd235a4364dc4339ec316735fb89ca9", "9ac34d3aeacb51249a773bc4fb13eefa69f00802f4b5085f35617706bef24b67"),
    "news_trend": ("0da0d3129eac86a8a5cd74dc4ddab2d0b588417b5853e2b9c02d5a9798703aaf", "c0bb9d7e5fbade4e7f962cc22f8099e6d8866becd31afe5160754caedea65126"),
}
COMPARE_RESULTS = (platform.system(), platform.machine()) == ("Linux", "x86_64")


def qrs(*args) -> None:
    subprocess.run([sys.executable, "-m", "qrs.cli", *map(str, args)], check=True, capture_output=True)


def run(strategy: Path, data: Path, out: Path) -> dict:
    qrs("run", strategy, "--data", data, "--out", out)
    return json.loads((out / "record.json").read_text())


def records(work: Path) -> dict[str, dict]:
    found = {}
    qrs("demo", "--home", work / "demo")
    strategy = work / "demo.json"
    strategy.write_text(templates.instantiate("ema_crossover", DEMO_DATASET).model_dump_json())
    found["demo"] = run(strategy, work / "demo", work / "demo-run")
    qrs("export", strategy, work / "demo.zip", "--data", work / "demo")
    zipfile.ZipFile(work / "demo.zip").extractall(work / "bundle")
    subprocess.run([sys.executable, "run.py", work / "bundle-run"], cwd=work / "bundle", check=True, capture_output=True)
    found["demo bundle"] = json.loads((work / "bundle-run" / "record.json").read_text())
    for template_id in ("bollinger_rsi", "ema_crossover", "sma_crossover", "ridge_forecast"):
        template, data = templates.load(template_id), work / template_id
        dataset_id = importer.import_csv(data, (templates.TEMPLATE_DIR / f"{template_id}.csv").read_bytes(), template.fixture.declaration)
        strategy = work / f"{template_id}.json"
        strategy.write_text(templates.instantiate(template_id, dataset_id).model_dump_json())
        found[template_id] = run(strategy, data, work / f"{template_id}-run")
    for template_id in ("macro_ridge", "news_trend"):
        data = work / template_id
        strategy = work / f"{template_id}.json"
        strategy.write_text(templates.instantiate(template_id, chosen=templates.prepare(template_id, data)).model_dump_json())
        found[template_id] = run(strategy, data, work / f"{template_id}-run")
    return found


def main() -> int:
    found = records(Path(tempfile.mkdtemp()))
    failures = [] if DEMO_DATASET in found["demo"]["datasets"] else [f"demo: dataset is not {DEMO_DATASET}"]
    for name, (code, result) in EXPECTED.items():
        if found[name]["code_sha256"] != code:
            failures.append(f"{name}: code {found[name]['code_sha256']}, expected {code}")
        if COMPARE_RESULTS and found[name]["result_sha256"] != result:
            failures.append(f"{name}: result {found[name]['result_sha256']}, expected {result}")
    for failure in failures:
        print(failure)
    compared = "dataset, code and result" if COMPARE_RESULTS else "dataset and code (results only on Linux x86_64)"
    print(f"historical hashes ({compared}): {'CHANGED' if failures else 'unchanged'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
