"""The committed runtime lock is the full, hashed dependency closure exported from uv.lock."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
EXPORT = ["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements-txt"]


def requirements(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.startswith("#")]


def test_runtime_lock_matches_uv_lock():
    exported = subprocess.run(EXPORT, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    committed = (ROOT / "src" / "qrs" / "runtime.lock").read_text()
    assert requirements(committed) == requirements(exported), f"Regenerate it: {' '.join(EXPORT)} > src/qrs/runtime.lock"
    pinned = [line for line in requirements(committed) if not line.startswith(" ")]
    assert pinned and all("==" in line for line in pinned)
    assert committed.count("--hash=sha256:") >= len(pinned)
