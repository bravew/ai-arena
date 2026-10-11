from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.core.models import Task
from arena.runners.run_record import events_path
from arena.scorers.base import ScorerError
from arena.scorers.task_scorers import task_scorers


def _task(*scorers: dict[str, object]) -> Task:
    return Task(id="t", version=1, kind="content", prompt_file="prompt.md", scorers=list(scorers))


def test_constraints_become_a_scorer_and_sandbox_scorers_are_reported() -> None:
    task = _task(
        {"id": "constraints", "weight": 1.0, "gate": True, "min_words": 3, "must_include": ["x"]},
        {"id": "execution", "weight": 1.0, "command": "pytest"},
        {"id": "rubric-judge", "weight": 1.0, "rubric": "r"},
    )
    result = task_scorers(task)
    assert [scorer.id for scorer in result.scorers] == ["constraints"]
    assert result.unapplied == ("execution", "rubric-judge")


def test_unknown_constraint_option_is_an_error() -> None:
    with pytest.raises(ScorerError, match="must_not_include"):
        task_scorers(_task({"id": "constraints", "must_not_include": ["x"]}))


def test_scorer_without_an_id_is_an_error() -> None:
    with pytest.raises(ScorerError, match="without an id"):
        task_scorers(_task({"weight": 1.0}))


def test_mock_run_scores_constraint_tasks_and_reports_them(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "suites/smoke",
            "-c",
            "mock-a",
            "--repeats",
            "1",
            "--db",
            str(tmp_path / "arena.db"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "not applied" in result.output
    assert "execution" in result.output
    run_id = result.output.split("Run ", 1)[1].split(":", 1)[0]

    report = runner.invoke(app, ["report", run_id, "--home", str(tmp_path)])
    assert report.exit_code == 0, report.output
    assert "| 1 | mock-a | —" not in report.output

    kinds = [
        json.loads(line)["kind"]
        for line in events_path(tmp_path, run_id).read_text(encoding="utf-8").splitlines()
    ]
    assert kinds.count("score_added") == 2


def test_score_execution_flag_runs_execution_scorers_in_the_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Generator, Mapping, Sequence
    from contextlib import contextmanager

    from arena import cli_run
    from arena.scorers.execution import CommandResult, Sandbox, SandboxFactory

    class PassingSandbox:
        def put_files(self, files: Mapping[str, bytes]) -> None:
            assert any(path.startswith("tests/") for path in files)

        def run(self, command: Sequence[str], *, timeout_s: float) -> CommandResult:
            return CommandResult(0)

    def sandbox_for(command: Sequence[str]) -> SandboxFactory:
        @contextmanager
        def factory() -> Generator[Sandbox]:
            yield PassingSandbox()

        return factory

    monkeypatch.setattr(cli_run, "_sandbox_for", sandbox_for)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "suites/smoke",
            "-c",
            "mock-a",
            "--repeats",
            "1",
            "--score-execution",
            "--db",
            str(tmp_path / "arena.db"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "not applied" not in result.output
    run_id = result.output.split("Run ", 1)[1].split(":", 1)[0]
    kinds = [
        json.loads(line)["kind"]
        for line in events_path(tmp_path, run_id).read_text(encoding="utf-8").splitlines()
    ]
    assert kinds.count("score_added") == 4
