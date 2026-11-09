"""Standalone Python export. A bundle holds the canonical strategy, the compiled strategy.py, a
snapshot of the exact qrs source, any extension operator modules, the hashed dependency lock, the
datasets whose terms allow redistribution, and run.py. The ZIP is deterministic: the same
strategy, runtime and data always produce byte-identical bundles."""

import io
import zipfile
from pathlib import Path

from qrs import datasets, graph, registry
from qrs.compiler import compile_for_data
from qrs.contracts import StrategyDocument
from qrs.hashing import sha256_hex
from qrs.runtime import PACKAGE_DIR, source_files

RUN_PY = '''"""Run this exported strategy without the studio: python run.py [output_dir]"""

import json
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))

import qrs  # noqa: E402
from qrs.runtime import backtest_nodes, execute  # noqa: E402

if Path(qrs.__file__).resolve().parent != here / "qrs":
    sys.exit(f"Expected the bundled qrs runtime but imported {qrs.__file__}")

out = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "output"
record = execute(here / "strategy.py", here / "data", out)
for node in backtest_nodes(record):
    print(node, json.dumps(json.loads((out / "results" / node / "summary.json").read_text())["metrics"], indent=2))
print("code_sha256", record["code_sha256"])
print("result_sha256", record["result_sha256"])
'''


def build(doc: StrategyDocument, data_dir: Path) -> bytes:
    code = compile_for_data(doc, data_dir)
    files = {
        "strategy.json": doc.model_dump_json(indent=2).encode() + b"\n",
        "strategy.py": code.encode(),
        "run.py": RUN_PY.encode(),
        "requirements.txt": (PACKAGE_DIR / "runtime.lock").read_bytes(),
    }
    for path in source_files():
        files[f"qrs/{path.relative_to(PACKAGE_DIR).as_posix()}"] = path.read_bytes()
    for node in doc.nodes:
        op = registry.get(node.operator, node.version)
        if op.module.split(".")[0] != "qrs":
            source = Path(op.source_file())
            if source.name == "__init__.py":
                raise ValueError(f"Operator {op.key} lives in a package; only single-file extension modules can be exported.")
            files[op.module.replace(".", "/") + ".py"] = source.read_bytes()

    data_lines = []
    for dataset_id in graph.dataset_ids(doc):
        manifest = datasets.read_manifest(data_dir, dataset_id)
        folder = datasets.directory(data_dir, dataset_id)
        if manifest.redistributable:
            for path in sorted(folder.iterdir()):
                files[f"data/datasets/{folder.name}/{path.name}"] = path.read_bytes()
            status = "included"
        else:
            status = f"not included (not redistributable): copy it to data/datasets/{folder.name}/"
        data_lines.append(f"- {dataset_id} — {manifest.name} ({manifest.evidence_class}) — {status}")

    files["README.md"] = f"""# {doc.name}

Exported from Quant Research Studio. Compiled strategy SHA-256: `{sha256_hex(code.encode())}`

Datasets:
{chr(10).join(data_lines)}

Run with Python 3.12:

    python3.12 -m venv .venv
    .venv/bin/pip install --require-hashes -r requirements.txt
    .venv/bin/python run.py output

Results are written to output/. `output/record.json` records the code, runtime, dataset and
output hashes; its `result_sha256` matches the studio run of the same strategy revision.
""".encode()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    return buffer.getvalue()
