"""Run task execution scorers against their oracle and null solutions."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

import typer
import yaml

from arena.core.models import Task, TaskKind
from arena.providers.config import ConfigLoader

EXECUTABLE_KINDS: set[TaskKind] = {"codegen", "agentic-code"}


def _task_directories(paths: list[Path]) -> list[Path]:
    directories: set[Path] = set()
    for path in paths:
        if path.is_dir():
            directories.update(candidate.parent for candidate in path.rglob("task.yaml"))
        elif path.name == "task.yaml":
            directories.add(path.parent)
        else:
            raise ValueError(f"{path}: expected a directory or task.yaml")
    return sorted(directories)


def _read_task(directory: Path) -> Task:
    task_path = directory / "task.yaml"
    raw: Any = yaml.load(task_path.read_text(encoding="utf-8"), Loader=ConfigLoader)
    if not isinstance(raw, dict):
        raise ValueError(f"{task_path}: expected a YAML mapping")
    return Task.model_validate(cast(dict[str, Any], raw))


def _execution_command(task: Task, directory: Path) -> list[str]:
    commands = [
        scorer.get("command")
        for scorer in task.scorers
        if scorer.get("id") == "execution" and isinstance(scorer.get("command"), str)
    ]
    if len(commands) != 1:
        raise ValueError(f"{directory / 'task.yaml'}: expected one execution scorer command")
    command = shlex.split(cast(str, commands[0]))
    if not command:
        raise ValueError(f"{directory / 'task.yaml'}: execution scorer command is empty")
    return command


def _prepare_workspace(directory: Path, solution: str, workspace: Path) -> None:
    for source in (directory / "fixtures", directory / "solution" / solution):
        if source.is_dir():
            shutil.copytree(source, workspace, dirs_exist_ok=True)
    tests = directory / "tests"
    if not tests.is_dir():
        raise ValueError(f"{directory}: executable task is missing tests/")
    shutil.copytree(tests, workspace / "tests")


def _run_solution(directory: Path, solution: str, command: list[str]) -> bool:
    with tempfile.TemporaryDirectory(prefix="arena-selfcheck-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        home = root / "home"
        workspace.mkdir()
        home.mkdir()
        _prepare_workspace(directory, solution, workspace)
        env = {
            "HOME": str(home),
            "PATH": os.environ.get("PATH", os.defpath),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        try:
            result = subprocess.run(
                command,
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except FileNotFoundError as error:
            raise ValueError(f"scorer executable is unavailable: {command[0]}") from error
        return result.returncode == 0


def selfcheck(paths: list[Path]) -> None:
    """Check executable task oracles pass and null solutions fail their tests."""
    try:
        directories = _task_directories(paths)
        results: list[tuple[str, bool, bool]] = []
        for directory in directories:
            task = _read_task(directory)
            if task.kind not in EXECUTABLE_KINDS:
                continue
            if not task.created_at:
                raise ValueError(f"{directory / 'task.yaml'}: created_at is required")
            command = _execution_command(task, directory)
            for solution in ("oracle", "null"):
                if not (directory / "solution" / solution).is_dir():
                    raise ValueError(f"{directory}: missing solution/{solution}/")
            oracle_passes = _run_solution(directory, "oracle", command)
            null_passes = _run_solution(directory, "null", command)
            results.append((task.id, oracle_passes, null_passes))
    except (OSError, UnicodeError, yaml.YAMLError, ValueError, subprocess.TimeoutExpired) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error

    if not results:
        typer.echo("No executable tasks found.", err=True)
        raise typer.Exit(code=1)

    for task_id, oracle_passes, null_passes in results:
        oracle_score = 1.0 if oracle_passes else 0.0
        null_score = 1.0 if null_passes else 0.0
        typer.echo(f"{task_id}: oracle={oracle_score:.1f} null={null_score:.1f}")

    oracle_total = sum(oracle for _, oracle, _ in results)
    null_total = sum(null for _, _, null in results)
    count = len(results)
    oracle_average = oracle_total / count
    null_average = null_total / count
    typer.echo(
        f"Summary: {count} task(s), oracle={oracle_average:.1f}, null={null_average:.1f}"
    )
    if oracle_total != count or null_total != 0:
        raise typer.Exit(code=1)
