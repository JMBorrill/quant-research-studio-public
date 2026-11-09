#!/usr/bin/env bash
# Export parity: every template's bundle, exported for its fixture, runs in a fresh virtualenv
# installed from the bundle's hashed lock alone (every bundle has the same lock) and reproduces the
# CLI run. So does a strategy using the example extension's custom table operator, from the module in
# its bundle. Needs network access to install the bundles' packages. Used by CI and the local gate.
#
#   scripts/check-exports.sh [WORK_DIR]
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
work=${1:-$(mktemp -d)}
mkdir -p "$work"
cd "$work"
export QRS_OPERATOR_MODULES=qrs_roc PYTHONPATH="$root/examples/extension"
qrs() { uv run --project "$root" qrs "$@"; }

uv run --project "$root" python - <<'EOF'
import json
from pathlib import Path
from qrs import templates
Path("strategies").mkdir()
for listed in templates.list_templates():
    # Each template's fixture bars, with the features it reads replayed from its recorded responses.
    chosen = templates.prepare(listed["id"], Path("data"))
    Path("strategies", f"{listed['id']}.json").write_text(templates.instantiate(listed["id"], chosen=chosen).model_dump_json())
# The EMA crossover beside a feature table: the custom candle table widened by a built-in merge.
doc = json.loads(Path("strategies", "ema_crossover.json").read_text())
doc["nodes"] += [
    {"id": "candle", "operator": "ext.candle", "version": 1},
    {"id": "change", "operator": "qrs.returns", "version": 1},
    {"id": "features", "operator": "qrs.merge", "version": 1, "params": {"name": "change"}},
]
wires = [("bars", "bars", "candle", "bars"), ("bars", "bars", "change", "bars"), ("candle", "table", "features", "table"), ("change", "series", "features", "series")]
doc["edges"] += [{"source": {"node": a, "port": b}, "target": {"node": c, "port": d}} for a, b, c, d in wires]
Path("strategies", "extension.json").write_text(json.dumps(doc))
EOF
uv venv --python 3.12 --seed fresh
for strategy in strategies/*.json; do
  name=$(basename "$strategy" .json)
  qrs run "$strategy" --data data --out "cli-$name"
  qrs export "$strategy" "$name.zip" --data data
  unzip -q "$name.zip" -d "bundle-$name"
  fresh/bin/pip install --require-hashes -r "bundle-$name/requirements.txt"
  (cd "bundle-$name" && env -u PYTHONPATH -u QRS_OPERATOR_MODULES ../fresh/bin/python run.py "../out-$name")
  python3 -c "import json, sys; a, b = (json.load(open(f'{d}/record.json')) for d in sys.argv[1:]); assert a['result_sha256'] == b['result_sha256'] and a['runtime']['lock_sha256'] == b['runtime']['lock_sha256'], (a, b); print(sys.argv[2], 'matches CLI:', b['result_sha256'])" "cli-$name" "out-$name"
done
