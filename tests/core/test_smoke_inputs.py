"""The committed smoke inputs: a suite, two mock contestants and a kit fixture, all key-free."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from typer.testing import CliRunner

from arena.cli import app
from arena.core.modelref import ModelRef
from arena.core.models import Contestant, Task, TaskKind
from arena.kits.loading import load_kit
from arena.providers.config import reject_plaintext_secrets

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "suites" / "smoke"
CONTESTANTS = ROOT / "contestants"
KIT = ROOT / "kits" / "fixture-basic"

KINDS: set[TaskKind] = {"codegen", "agentic-code", "content", "web-artifact"}
EXECUTABLE: set[TaskKind] = {"codegen", "agentic-code"}


def read_yaml(path: Path) -> dict[str, Any]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"{path} must hold a mapping"
    return cast(dict[str, Any], raw)


def task_dirs() -> list[Path]:
    return sorted(path.parent for path in (SUITE / "tasks").glob("*/task.yaml"))


def load_task(directory: Path) -> Task:
    return Task.model_validate(read_yaml(directory / "task.yaml"))


def load_contestant(path: Path) -> Contestant:
    """Map the DEV_PLAN §4 contestant file onto the `Contestant` entity."""
    raw = read_yaml(path)
    scaffold = raw.pop("scaffold", "none")
    assert scaffold == "none", f"{path}: mock contestants are bare completions"
    label = raw.pop("id_label")
    model = ModelRef.parse(str(raw.pop("model")))
    return Contestant.model_validate(raw | {"label": label, "model": model})


def test_smoke_suite_has_one_task_of_each_kind() -> None:
    tasks = [load_task(directory) for directory in task_dirs()]
    assert {task.kind for task in tasks} == KINDS
    assert len(tasks) == len(KINDS)
    assert len({task.id for task in tasks}) == len(tasks)
    assert all(task.id.startswith("smoke.") for task in tasks)


def test_every_task_has_its_prompt_and_scorers() -> None:
    assert len(task_dirs()) == len(KINDS)
    for directory in task_dirs():
        task = load_task(directory)
        assert (directory / task.prompt_file).read_text(encoding="utf-8").strip()
        assert task.scorers, f"{task.id} has no scorer"


def test_executable_tasks_ship_oracle_and_null_solutions() -> None:
    assert len(task_dirs()) == len(KINDS)
    for directory in task_dirs():
        task = load_task(directory)
        has_solutions = (directory / "solution" / "oracle").is_dir() and (
            directory / "solution" / "null"
        ).is_dir()
        assert has_solutions == (task.kind in EXECUTABLE), task.id


def run_hidden_tests(directory: Path, solution: str, workspace: Path) -> int:
    """Build the workspace (fixtures, then the solution, then hidden tests) and run its tests."""
    for source in (directory / "fixtures", directory / "solution" / solution):
        if source.is_dir():
            shutil.copytree(source, workspace, dirs_exist_ok=True)
    shutil.copytree(directory / "tests", workspace / "tests")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", "."],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode


@pytest.mark.parametrize("solution,passes", [("oracle", True), ("null", False)])
def test_oracle_passes_and_null_fails_the_hidden_tests(
    tmp_path: Path, solution: str, passes: bool
) -> None:
    executable = [d for d in task_dirs() if load_task(d).kind in EXECUTABLE]
    assert len(executable) == len(EXECUTABLE)
    for directory in executable:
        workspace = tmp_path / directory.name
        code = run_hidden_tests(directory, solution, workspace)
        assert (code == 0) is passes, f"{directory.name}: {solution} exited {code}"


def test_two_mock_contestants_with_distinct_identities() -> None:
    files = sorted(CONTESTANTS.glob("mock-*.yaml"))
    assert [path.name for path in files] == ["mock-a.yaml", "mock-b.yaml"]
    contestants = [load_contestant(path) for path in files]
    assert {contestant.model.provider for contestant in contestants} == {"mock"}
    assert len({contestant.id for contestant in contestants}) == 2
    assert [contestant.label for contestant in contestants] == ["mock-a", "mock-b"]


def test_kit_fixture_has_skill_instructions_and_one_mcp_entry() -> None:
    kit = load_kit(KIT / "kit.yaml")
    assert kit.id == "fixture-basic"
    assert kit.instructions is not None
    assert (KIT / kit.instructions).read_text(encoding="utf-8").strip()
    assert [skill.path for skill in kit.skills] == ["skills/smoke-greeting"]
    assert all(skill.git is None for skill in kit.skills)
    assert (KIT / "skills" / "smoke-greeting" / "SKILL.md").is_file()
    assert len(kit.mcp) == 1
    assert kit.hash != "pending"
    assert load_kit(KIT / "kit.yaml").hash == kit.hash


def test_smoke_inputs_hold_no_plaintext_secrets() -> None:
    files = [*SUITE.rglob("*.yaml"), *CONTESTANTS.glob("mock-*.yaml"), *KIT.rglob("*.yaml")]
    assert files
    for path in files:
        assert reject_plaintext_secrets(read_yaml(path), path) == []


def test_arena_validate_accepts_the_smoke_inputs() -> None:
    result = CliRunner().invoke(app, ["validate", str(SUITE), str(CONTESTANTS), str(ROOT / "kits")])
    assert result.exit_code == 0, result.output
