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


class _FakeJudge:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[object] = []

    def complete(self, request: object) -> object:
        from arena.judges.client import JudgeReply

        self.requests.append(request)
        return JudgeReply(text=self.text, model="judge-x", tokens_in=10, tokens_out=5)


def _verdict(rubric_path: Path, score: int) -> str:
    from arena.judges.rubric import load_rubric

    rubric = load_rubric(rubric_path)
    return json.dumps(
        {
            "criteria": [
                {"id": criterion.id, "rationale": "fine", "score": score}
                for criterion in rubric.criteria
            ]
        }
    )


def test_rubric_judge_is_built_only_with_a_judge_and_loads_the_rubric() -> None:
    from arena.scorers.task_scorers import JudgeSetup

    task = _task({"id": "rubric-judge", "rubric": "rubrics/content-writing-v1.yaml", "weight": 1})
    assert task_scorers(task).unapplied == ("rubric-judge",)
    setup = JudgeSetup(_FakeJudge(""), "judge-x", "r1", Path.cwd())  # type: ignore[arg-type]
    built = task_scorers(task, judge=setup)
    assert [scorer.id for scorer in built.scorers] == ["rubric-judge:content-writing"]
    assert built.unapplied == ()


def test_rubric_judge_rejects_unknown_options_and_paths_outside_the_root() -> None:
    from arena.scorers.task_scorers import JudgeSetup

    setup = JudgeSetup(_FakeJudge(""), "judge-x", "r1", Path.cwd())  # type: ignore[arg-type]
    with pytest.raises(ScorerError, match="unknown rubric-judge"):
        task_scorers(_task({"id": "rubric-judge", "rubric": "r.yaml", "extra": 1}), judge=setup)
    with pytest.raises(ScorerError, match="leaves the rubric root"):
        task_scorers(_task({"id": "rubric-judge", "rubric": "../../etc/x.yaml"}), judge=setup)


def test_judge_flags_score_content_tasks_through_the_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arena import cli_run

    fake = _FakeJudge(_verdict(Path("rubrics/content-writing-v1.yaml"), 2))
    seen: list[tuple[str, str]] = []

    def transport(url: str, protocol: str) -> _FakeJudge:
        seen.append((url, protocol))
        return fake

    monkeypatch.setattr(cli_run, "_judge_transport", transport)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "suites/content",
            "-c",
            "mock-a",
            "--repeats",
            "1",
            "--judge-model",
            "judge-x",
            "--judge-url",
            "http://127.0.0.1:1",
            "--db",
            str(tmp_path / "arena.db"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen == [("http://127.0.0.1:1", "openai")]
    assert "not applied" not in result.output
    assert len(fake.requests) > 0
    run_id = result.output.split("Run ", 1)[1].split(":", 1)[0]
    report = runner.invoke(app, ["report", run_id, "--home", str(tmp_path)])
    assert report.exit_code == 0, report.output
    assert "1.000" in report.output


def test_unscorable_judge_reply_leaves_the_trial_unscored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arena import cli_run

    monkeypatch.setattr(cli_run, "_judge_transport", lambda url, protocol: _FakeJudge("not json"))
    result = CliRunner().invoke(
        app,
        [
            "run",
            "suites/content",
            "-c",
            "mock-a",
            "--repeats",
            "1",
            "--judge-model",
            "judge-x",
            "--judge-url",
            "http://127.0.0.1:1",
            "--db",
            str(tmp_path / "arena.db"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "was not scored" in result.output
    kinds = [
        json.loads(line)["kind"]
        for line in events_path(tmp_path, result.output.split("Run ", 1)[1].split(":", 1)[0])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert "score_added" not in kinds


def test_judge_flags_must_be_given_together(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["run", "suites/smoke", "-c", "mock-a", "--judge-model", "m", "--db", str(tmp_path / "a")],
    )
    assert result.exit_code == 2
