"""Build the scorers a task YAML lists, for the ones `arena run` can apply.

A task lists scorers as `{id, weight, gate, ...options}`. `constraints` maps to a
`ConstraintScorer` over the trial's `answer.md`. `execution` maps to an `ExecutionScorer` that
runs the task's command in a sandbox, when the caller supplies a sandbox for it. `rubric-judge`
maps to a `RubricJudge` when the caller supplies a judge (a transport and model), otherwise it is
left unapplied and `arena run` says so.

An option this module does not know is an error, never ignored: a scorer that quietly dropped
a check would report a pass nobody earned.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arena.core.models import Task
from arena.judges.client import JudgeTransport
from arena.judges.rubric import RubricJudge, load_rubric
from arena.scorers.base import Scorer, ScorerError
from arena.scorers.constraint import Check, ConstraintScorer, RequiredKeywords, WordCount
from arena.scorers.execution import ExecutionScorer, SandboxFactory

ANSWER_PATH = "answer.md"
_COMMON_KEYS = frozenset({"id", "weight", "gate"})
_CONSTRAINT_KEYS = frozenset({"min_words", "max_words", "must_include"})


SandboxForCommand = Callable[[Sequence[str]], SandboxFactory]


@dataclass(frozen=True)
class JudgeSetup:
    """What a rubric judge needs: where to send requests, which model, and where rubrics live."""

    transport: JudgeTransport
    model: str
    run_id: str
    rubric_root: Path


@dataclass(frozen=True)
class TaskScorers:
    scorers: tuple[Scorer, ...]
    unapplied: tuple[str, ...]


def task_scorers(
    task: Task,
    task_dir: Path | None = None,
    sandbox_for: SandboxForCommand | None = None,
    judge: JudgeSetup | None = None,
) -> TaskScorers:
    """The scorers `arena run` can apply to `task`, and the ids of those it cannot.

    `execution` is applied only when both `task_dir` (holding `tests/` and optional
    `fixtures/`) and `sandbox_for`, which picks a sandbox for the task's command, are given.
    `rubric-judge` is applied only when `judge` is given.
    """
    scorers: list[Scorer] = []
    unapplied: list[str] = []
    for entry in task.scorers:
        scorer_id = entry.get("id")
        if scorer_id == "constraints":
            scorers.append(_constraints(task, entry))
        elif scorer_id == "execution" and task_dir is not None and sandbox_for is not None:
            scorers.append(_execution(task, entry, task_dir, sandbox_for))
        elif scorer_id == "rubric-judge" and judge is not None:
            scorers.append(_rubric_judge(task, entry, judge))
        elif isinstance(scorer_id, str):
            unapplied.append(scorer_id)
        else:
            raise ScorerError(f"task {task.id} has a scorer without an id")
    return TaskScorers(tuple(scorers), tuple(unapplied))


def _constraints(task: Task, entry: Mapping[str, Any]) -> ConstraintScorer:
    unknown = sorted(set(entry) - _COMMON_KEYS - _CONSTRAINT_KEYS)
    if unknown:
        raise ScorerError(f"task {task.id}: unknown constraints option(s) {', '.join(unknown)}")
    checks: list[Check] = []
    if "min_words" in entry or "max_words" in entry:
        checks.append(WordCount(min=entry.get("min_words"), max=entry.get("max_words")))
    if "must_include" in entry:
        checks.append(RequiredKeywords(keywords=list(entry["must_include"])))
    return ConstraintScorer(ANSWER_PATH, checks, scorer_id="constraints")


def _execution(
    task: Task, entry: Mapping[str, Any], directory: Path, sandbox_for: SandboxForCommand
) -> ExecutionScorer:
    unknown = sorted(set(entry) - _COMMON_KEYS - {"command"})
    if unknown:
        raise ScorerError(f"task {task.id}: unknown execution option(s) {', '.join(unknown)}")
    command = entry.get("command")
    if not isinstance(command, str) or not shlex.split(command):
        raise ScorerError(f"task {task.id}: execution needs a non-empty command")
    argv = shlex.split(command)
    tests = _tree(directory / "tests", "tests")
    if not tests:
        raise ScorerError(f"task {task.id}: execution task has no files in tests/")
    return ExecutionScorer(
        sandbox_for(argv),
        command=argv,
        task_files=tests,
        base_files=fixture_files(directory),
    )


def fixture_files(directory: Path) -> dict[str, bytes]:
    """The task's starting repository (`fixtures/`), keyed by path relative to its root."""
    return _tree(directory / "fixtures", "")


def _tree(root: Path, prefix: str) -> dict[str, bytes]:
    """Every file under `root`, keyed by its path relative to the sandbox working directory."""
    if not root.is_dir():
        return {}
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            relative = path.relative_to(root).as_posix()
            files[f"{prefix}/{relative}" if prefix else relative] = path.read_bytes()
    return files


def _rubric_judge(task: Task, entry: Mapping[str, Any], judge: JudgeSetup) -> RubricJudge:
    unknown = sorted(set(entry) - _COMMON_KEYS - {"rubric"})
    if unknown:
        raise ScorerError(f"task {task.id}: unknown rubric-judge option(s) {', '.join(unknown)}")
    rubric = entry.get("rubric")
    if not isinstance(rubric, str) or not rubric:
        raise ScorerError(f"task {task.id}: rubric-judge needs a rubric path")
    path = (judge.rubric_root / rubric).resolve()
    if not path.is_relative_to(judge.rubric_root.resolve()):
        raise ScorerError(f"task {task.id}: rubric {rubric!r} leaves the rubric root")
    return RubricJudge(
        load_rubric(path),
        judge.transport,
        model=judge.model,
        run_id=judge.run_id,
        artifact_path=ANSWER_PATH,
    )
