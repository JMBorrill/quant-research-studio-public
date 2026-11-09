#!/usr/bin/env bash
# The research programme against a real service: research/corn/run.py on its synthetic fixtures,
# through `qrs serve` and its workers, then `qrs report verify` from the command line on the report it
# built. Needs no network. Used by the local gate.
#
#   scripts/check-research.sh
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
python="$root/.venv/bin/python"  # the synced environment, as the gate runs the suite: no network needed
home=$(mktemp -d)
port=$("$python" -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])")
"$python" -m qrs.cli serve --home "$home" --port "$port" > "$home/serve.log" 2>&1 &
server=$!
trap 'kill $server 2>/dev/null || true' EXIT
for _ in $(seq 100); do
  curl -sf "http://127.0.0.1:$port/api/operators" > /dev/null && break
  sleep 0.2
done
"$python" "$root/research/corn/run.py" --home "$home" --url "http://127.0.0.1:$port"
report=$(ls "$home/reports")
"$python" -m qrs.cli report verify "$home/reports/$report" --home "$home"
