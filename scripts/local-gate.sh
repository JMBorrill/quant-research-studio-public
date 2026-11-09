#!/usr/bin/env bash
# The local quality gate: what CI checks, on this machine, stopping at the first failure.
# - Locks: uv.lock against pyproject.toml (uv sync --locked) and the studio's package-lock.json
#   (npm ci); runtime.lock against uv.lock is a test in the Python suite.
# - Lint, and the committed schema and studio types against the contracts.
# - Studio: Vitest, typecheck and build.
# - The whole Python suite and the browser tests, offline in a network namespace with only loopback
#   where this machine has one (Linux with unshare and ip); otherwise online, and it says so.
# - Export parity (scripts/check-exports.sh, which needs network access for the bundles' packages).
# - Historical hashes (scripts/check-hashes.py).
# - The research programme through a real service, and `qrs report verify` on its report
#   (scripts/check-research.sh), offline where it can be, as the suite.
# - The mutation pass over Stage 5's safeguards (scripts/check-mutations.py).
# Needs uv, Node.js 22 and Chromium for Playwright (uv run playwright install chromium).
#
#   scripts/local-gate.sh
set -euo pipefail
cd "$(dirname "$0")/.."
work=$(mktemp -d)
step() { printf '\n== %s\n' "$*"; }

step "Locks"
uv sync --locked
(cd apps/studio && npm ci)

step "Lint"
uvx ruff check src tests examples
uvx ruff format --check src tests examples

step "Contracts: the committed schema and studio types"
uv run qrs schema
(cd apps/studio && npm run gen:types)
git diff --exit-code -- schemas apps/studio/src/contracts.gen.ts

step "Studio"
(cd apps/studio && npm test && npm run typecheck && npm run build)

step "Python suite and browser tests"
tests=".venv/bin/python -m pytest -q && .venv/bin/python -m pytest -q -m e2e"
if command -v ip >/dev/null && unshare -rn true 2>/dev/null; then
  echo "Offline: in a network namespace with only loopback."
  unshare -rn sh -c "ip link set lo up && $tests"
else
  echo "Online: this machine has no network namespace, so offline execution is not checked."
  sh -c "$tests"
fi

step "Export parity"
scripts/check-exports.sh "$work/exports"

step "Historical hashes"
uv run python scripts/check-hashes.py

step "Research programme: run and report verify"
if command -v ip >/dev/null && unshare -rn true 2>/dev/null; then
  unshare -rn sh -c "ip link set lo up && scripts/check-research.sh"
else
  scripts/check-research.sh
fi

step "Mutation pass"
uv run python scripts/check-mutations.py

printf '\nLocal gate passed.\n'
