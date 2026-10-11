from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.cli_selfcheck import _task_directories

runner = CliRunner()
TASK = """id: test.example
version: 1
kind: codegen
created_at: 2026-10-08
prompt_file: prompt.md
scorers:
  - { id: execution, weight: 1.0, command: "python -m pytest -q tests" }
"""


def write_task(directory: Path, text: str = TASK) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "task.yaml").write_text(text, encoding="utf-8")
    (directory / "prompt.md").write_text("Implement it.", encoding="utf-8")
    (directory / "tests").mkdir(exist_ok=True)
    (directory / "solution/oracle").mkdir(parents=True, exist_ok=True)
    (directory / "solution/null").mkdir(parents=True, exist_ok=True)
    return directory


def test_discovery_accepts_suite_and_task_file(tmp_path: Path) -> None:
    task = write_task(tmp_path / "suite" / "tasks" / "example")

    assert _task_directories([tmp_path / "suite", task / "task.yaml"]) == [task]


def test_selfcheck_requires_explicit_trust_confirmation(tmp_path: Path) -> None:
    task = write_task(tmp_path / "example")

    result = runner.invoke(app, ["selfcheck", str(task)])

    assert result.exit_code == 2
    assert "host privileges" in result.output
    assert "--trust-task-code" in result.output


def test_cli_reports_oracle_null_scores_for_trusted_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = write_task(tmp_path / "example")
    calls: list[str] = []

    def run_solution(directory: Path, solution: str, command: list[str]) -> bool:
        assert directory == task
        assert command == ["python", "-m", "pytest", "-q", "tests"]
        calls.append(solution)
        return solution == "oracle"

    monkeypatch.setattr("arena.cli_selfcheck._run_solution", run_solution)

    result = runner.invoke(app, ["selfcheck", "--trust-task-code", str(task)])

    assert result.exit_code == 0, result.output
    assert "test.example: oracle=1.0 null=0.0" in result.output
    assert "Summary: 1 task(s), oracle=1.0, null=0.0" in result.output
    assert calls == ["oracle", "null"]


def test_malformed_task_yaml_fails_cleanly(tmp_path: Path) -> None:
    task = write_task(tmp_path / "example", "id: [unterminated\n")

    result = runner.invoke(app, ["selfcheck", "--trust-task-code", str(task)])

    assert result.exit_code == 1
    assert "while parsing a flow sequence" in result.output


def test_missing_test_assets_fail_cleanly(tmp_path: Path) -> None:
    task = write_task(tmp_path / "example")
    (task / "tests").rmdir()

    result = runner.invoke(app, ["selfcheck", "--trust-task-code", str(task)])

    assert result.exit_code == 1
    assert "missing tests/" in result.output


def test_missing_scorer_executable_fails_cleanly(tmp_path: Path) -> None:
    task = write_task(
        tmp_path / "example",
        TASK.replace("python -m pytest -q tests", "missing-arena-test-executable"),
    )

    result = runner.invoke(app, ["selfcheck", "--trust-task-code", str(task)])

    assert result.exit_code == 1
    assert "scorer executable is unavailable" in result.output


def test_scorer_timeout_fails_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = write_task(tmp_path / "example")

    def timeout(directory: Path, solution: str, command: list[str]) -> bool:
        raise subprocess.TimeoutExpired(command, timeout=120)

    monkeypatch.setattr("arena.cli_selfcheck._run_solution", timeout)

    result = runner.invoke(app, ["selfcheck", "--trust-task-code", str(task)])

    assert result.exit_code == 1
    assert "timed out" in result.output
