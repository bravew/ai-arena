from __future__ import annotations

import io
import subprocess
import tarfile
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from arena.core.cas import ArtifactStore
from arena.core.models import Artifact, Task
from arena.scorers.base import ScorerContext, ScorerError
from arena.scorers.docker_sandbox import docker_sandbox_factory, image_for_command
from arena.scorers.execution import (
    CommandResult,
    ExecutionScorer,
    Sandbox,
    SandboxError,
    SandboxFactory,
)
from arena.scorers.task_scorers import task_scorers


class FakeDocker:
    def __init__(self, *, run_code: int = 0, exec_code: int = 0, timeout: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.inputs: list[bytes | None] = []
        self.run_code = run_code
        self.exec_code = exec_code
        self.timeout = timeout

    def __call__(
        self, *args: str, input: bytes | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(args)
        self.inputs.append(input)
        if args[0] == "run":
            return subprocess.CompletedProcess(args, self.run_code, b"id", b"no such image")
        if args[0] == "exec" and "tar" not in args and self.timeout:
            raise subprocess.TimeoutExpired(args, timeout or 0, b"partial", b"")
        code = self.exec_code if args[0] == "exec" and "tar" not in args else 0
        return subprocess.CompletedProcess(args, code, b"out", b"err")


def test_sandbox_is_isolated_files_are_tarred_and_container_is_removed() -> None:
    docker = FakeDocker()
    with docker_sandbox_factory("img:1", docker=docker)() as sandbox:
        sandbox.put_files({"tests/t.py": b"x = 1"})
        result = sandbox.run(["pytest"], timeout_s=5)
    assert result == CommandResult(0, "out", "err")
    run = docker.calls[0]
    for flag in ("--network", "none", "--cap-drop", "ALL", "--read-only", "no-new-privileges"):
        assert flag in run
    assert run[run.index("--user") + 1] == "65534:65534"
    with tarfile.open(fileobj=io.BytesIO(docker.inputs[1] or b"")) as archive:
        assert archive.extractfile("tests/t.py").read() == b"x = 1"  # type: ignore[union-attr]
    assert docker.calls[-1][:2] == ("rm", "-f")


def test_timeout_kills_the_container_and_is_a_result() -> None:
    docker = FakeDocker(timeout=True)
    with docker_sandbox_factory("img:1", docker=docker)() as sandbox:
        result = sandbox.run(["sleep", "9"], timeout_s=1)
    assert result.timed_out
    assert any(call[0] == "kill" for call in docker.calls)


def test_container_that_cannot_start_is_a_sandbox_error() -> None:
    factory = docker_sandbox_factory("img:1", docker=FakeDocker(run_code=125))
    with pytest.raises(SandboxError, match="no such image"), factory():
        pass


def test_unknown_command_has_no_image() -> None:
    assert image_for_command(["go", "test"]) == "golang:1.23"
    with pytest.raises(SandboxError, match="ruby"):
        image_for_command(["ruby", "t.rb"])


class RecordingSandbox:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.files: dict[str, bytes] = {}

    def put_files(self, files: Mapping[str, bytes]) -> None:
        self.files = dict(files)

    def run(self, command: Sequence[str], *, timeout_s: float) -> CommandResult:
        return self.result


def _context(tmp_path: Path, files: dict[str, bytes]) -> ScorerContext:
    blobs = ArtifactStore(tmp_path / "blobs")
    artifacts = {
        path: Artifact(
            trial_id="t", path=path, sha256=blobs.put(data), mime="text/plain", render_hint="code"
        )
        for path, data in files.items()
    }
    return ScorerContext(trial_id="t", artifacts=artifacts, blobs=blobs)


def _factory(sandbox: RecordingSandbox) -> SandboxFactory:
    @contextmanager
    def factory() -> Generator[Sandbox]:
        yield sandbox

    return factory


def _scorer(sandbox: RecordingSandbox) -> ExecutionScorer:
    return ExecutionScorer(
        _factory(sandbox),
        command=["pytest"],
        task_files={"tests/test_a.py": b"hidden"},
        base_files={"README": b"fixture", "solution.py": b"fixture version"},
    )


def test_execution_scorer_passes_on_exit_zero_and_tests_beat_the_contestant(
    tmp_path: Path,
) -> None:
    sandbox = RecordingSandbox(CommandResult(0))
    score = _scorer(sandbox).score(
        _context(tmp_path, {"solution.py": b"mine", "tests/test_a.py": b"cheat"})
    )
    assert score.passed is True and score.normalized == 1.0
    assert sandbox.files["tests/test_a.py"] == b"hidden"
    assert sandbox.files["solution.py"] == b"mine"
    assert sandbox.files["README"] == b"fixture"
    assert score.evidence["overwritten_paths"] == ["tests/test_a.py"]


def test_execution_scorer_fails_on_nonzero_exit_and_timeout(tmp_path: Path) -> None:
    failed = _scorer(RecordingSandbox(CommandResult(1, "", "boom"))).score(_context(tmp_path, {}))
    assert failed.passed is False and "boom" in failed.evidence["output_tail"]
    slow = _scorer(RecordingSandbox(CommandResult(-1, timed_out=True))).score(
        _context(tmp_path, {})
    )
    assert slow.passed is False and slow.rationale.startswith("timed out")


def test_missing_toolchain_is_an_error_not_a_zero(tmp_path: Path) -> None:
    with pytest.raises(ScorerError, match="could not run"):
        _scorer(RecordingSandbox(CommandResult(127, "", "not found"))).score(_context(tmp_path, {}))


def test_task_scorers_applies_execution_only_with_a_task_dir_and_sandbox(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("t")
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "go.mod").write_text("m")
    task = Task(
        id="t",
        version=1,
        kind="codegen",
        prompt_file="p.md",
        scorers=[{"id": "execution", "weight": 1.0, "command": "python -m pytest -q tests"}],
    )
    assert task_scorers(task).unapplied == ("execution",)

    seen: list[Sequence[str]] = []

    def sandbox_for(command: Sequence[str]) -> SandboxFactory:
        seen.append(command)
        return _factory(RecordingSandbox(CommandResult(0)))

    applied = task_scorers(task, tmp_path, sandbox_for)
    assert [scorer.id for scorer in applied.scorers] == ["execution"]
    assert applied.unapplied == ()
    assert seen == [["python", "-m", "pytest", "-q", "tests"]]
    scorer = applied.scorers[0]
    assert isinstance(scorer, ExecutionScorer)
    assert set(scorer.task_files) == {"tests/test_a.py"}
    assert set(scorer.base_files) == {"go.mod"}


def test_execution_without_tests_is_an_error(tmp_path: Path) -> None:
    task = Task(
        id="t",
        version=1,
        kind="codegen",
        prompt_file="p.md",
        scorers=[{"id": "execution", "command": "go test ./..."}],
    )
    with pytest.raises(ScorerError, match="no files in tests/"):
        task_scorers(task, tmp_path, lambda command: _factory(RecordingSandbox(CommandResult(0))))
