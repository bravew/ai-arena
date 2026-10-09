#!/usr/bin/env python3
"""Render the pull request smoke job's Markdown summary."""

from __future__ import annotations

import os
from pathlib import Path


def render_summary(status: str, run_url: str) -> str:
    result = "✅ Passed" if status == "success" else "❌ Failed"
    lines = ["## Arena smoke", "", f"**Result:** {result}"]
    if run_url:
        lines.extend(["", f"[View workflow run]({run_url})"])
    lines.extend(
        [
            "",
            "The current branch runs smoke suite validation and oracle/null self-checks. "
            "Cassette replay through the gateway and runner will run once CP2 and CP3 "
            "are integrated.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    status = os.environ.get("ARENA_SMOKE_STATUS", "failure")
    run_url = os.environ.get("ARENA_SMOKE_RUN_URL", "")
    summary = render_summary(status, run_url)
    print(summary, end="")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(summary)


if __name__ == "__main__":
    main()
