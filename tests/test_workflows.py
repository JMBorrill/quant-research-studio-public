"""The CI workflows: one uv version for both, and every push to main checked."""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"


def setting(name: str, workflow: str) -> str:
    return re.search(rf"^\s*{name}: (.+)$", (WORKFLOWS / workflow).read_text(), re.MULTILINE).group(1)


def test_both_workflows_pin_the_uv_version_that_writes_the_lock():
    # runtime.lock is regenerated with ci.yml's uv (CLAUDE.md); macOS must sync and export with the same.
    assert setting("UV_VERSION", "ci.yml") == setting("UV_VERSION", "full-verification.yml")


def test_only_a_pull_requests_runs_share_a_concurrency_group():
    # A newer run replaces a pending one in its group, so a group shared by pushes would skip merges.
    assert setting("group", "ci.yml") == "${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}"
