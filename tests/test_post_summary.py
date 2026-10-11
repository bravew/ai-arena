from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "post_summary.py"


def test_summary_renders_status_and_run_link() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "ARENA_SMOKE_STATUS": "success",
            "ARENA_SMOKE_RUN_URL": "https://github.com/example/repo/actions/runs/123",
        },
    )

    assert "## Arena smoke" in result.stdout
    assert "**Result:** ✅ Passed" in result.stdout
    assert "[View workflow run](https://github.com/example/repo/actions/runs/123)" in result.stdout


def test_summary_reports_failure_without_run_link() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ | {"ARENA_SMOKE_STATUS": "failure", "ARENA_SMOKE_RUN_URL": ""},
    )

    assert "**Result:** ❌ Failed" in result.stdout
    assert "View workflow run" not in result.stdout
