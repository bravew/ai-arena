"""`arena plan`, `arena run`, `arena ls` and `arena resume` commands."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any, cast

import typer
import yaml

from arena.catalog.config import CatalogModel, load_catalog
from arena.cli import app
from arena.core.ids import content_id
from arena.core.modelref import ModelRef
from arena.core.models import Contestant, Task
from arena.core.store import Store
from arena.kits.matrix import expand_matrix
from arena.providers.config import parse_yaml
from arena.runners.completion import CompletionResult, mock_completion
from arena.runners.scheduler import TrialJob, expand_jobs, run_jobs


def _suite_dir(value: str) -> Path:
    path = Path(value)
    if path.is_dir():
        return path
    candidate = Path("suites") / value
    if candidate.is_dir():
        return candidate
    raise ValueError(f"suite directory not found: {value}")


def _suite_tasks(path: str) -> list[Task]:
    directory = _suite_dir(path)
    task_files = sorted(directory.glob("tasks/*/task.yaml"))
    if not task_files:
        raise ValueError(f"suite {path} contains no tasks/*/task.yaml files")
    return [Task.model_validate(parse_yaml(task_file)) for task_file in task_files]


def _contestant_from_yaml(path: Path) -> list[Contestant]:
    raw_value = parse_yaml(path)
    if not isinstance(raw_value, dict):
        raise ValueError(f"{path}: expected a mapping")
    raw = cast(dict[str, Any], raw_value)
    if "matrix" in raw:
        return expand_matrix(raw)
    config = dict(raw)
    config["label"] = config.pop("id_label", config.get("label"))
    config["model"] = ModelRef.parse(str(config["model"]))
    scaffold = config.get("scaffold", "none")
    config["scaffold"] = None if scaffold is None or scaffold == "none" else scaffold
    return [Contestant.model_validate(config)]


def _contestants(paths: list[str]) -> list[Contestant]:
    expanded: list[Contestant] = []
    for value in paths:
        path = Path(value)
        if not path.is_file():
            candidate = Path("contestants") / f"{value}.yaml"
            if candidate.is_file():
                path = candidate
            else:
                raise ValueError(f"contestant file not found: {value}")
        expanded.extend(_contestant_from_yaml(path))
    ids = [contestant.id for contestant in expanded]
    if len(ids) != len(set(ids)):
        raise ValueError("contestant arguments expand to duplicate contestants")
    return expanded


def _catalog_models(path: Path) -> dict[str, CatalogModel]:
    catalog = load_catalog(path)
    return {model.ref: model for model in catalog.models}


def _estimate_cost(jobs: list[TrialJob], models: dict[str, CatalogModel]) -> float | None:
    total = 0.0
    for job in jobs:
        model = models.get(str(job.contestant.model))
        if model is None or model.price_per_mtok is None:
            return None
        params: dict[str, Any] = dict(job.contestant.params)
        input_tokens = int(params.get("estimated_input_tokens", 0))
        output_tokens = int(params.get("estimated_output_tokens", params.get("max_tokens", 0)))
        input_cost = input_tokens * model.price_per_mtok.get("in", 0)
        output_cost = output_tokens * model.price_per_mtok.get("out", 0)
        total += (input_cost + output_cost) / 1_000_000
    return total


def _jobs(
    suite: str, contestant_paths: list[str], repeats: int
) -> tuple[list[Task], list[Contestant], list[TrialJob]]:
    tasks = _suite_tasks(suite)
    contestants = _contestants(contestant_paths)
    return tasks, contestants, expand_jobs(tasks, contestants, repeats)


@app.command("plan")
def plan(
    suite: Annotated[str, typer.Argument(help="Suite directory or suites/<name> path.")],
    contestants: Annotated[list[str], typer.Option("-c", "--contestant")],
    repeats: Annotated[int, typer.Option(min=1)] = 3,
    catalog: Annotated[Path, typer.Option(help="Model price catalog.")] = Path(
        "catalog/models.yaml"
    ),
) -> None:
    """Print the expanded run plan without writing anything."""
    try:
        _, expanded, jobs = _jobs(str(suite), contestants, repeats)
        models = _catalog_models(catalog)
    except (OSError, ValueError, yaml.YAMLError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo("Expanded contestants:")
    for contestant in expanded:
        typer.echo(f"  {contestant.label or contestant.id}: {contestant.model}")
    typer.echo("Unsupported pairs: none (no adapter declares an unsupported pair)")
    cost = _estimate_cost(jobs, models)
    typer.echo(f"Trials: {len(jobs)}")
    typer.echo(f"Estimated cost: {'unknown' if cost is None else f'${cost:.6f}'}")


@app.command("run")
def run(
    suite: Annotated[str, typer.Argument(help="Suite directory or suites/<name> path.")],
    contestants: Annotated[list[str], typer.Option("-c", "--contestant")],
    repeats: Annotated[int, typer.Option(min=1)] = 3,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    resume_id: Annotated[str | None, typer.Option("--resume")] = None,
    budget_usd: Annotated[float | None, typer.Option(min=0)] = None,
    max_concurrency: Annotated[int, typer.Option(min=1)] = 8,
    per_contestant: Annotated[int, typer.Option(min=1)] = 2,
    db: Annotated[Path, typer.Option(help="SQLite run store.")] = Path(".arena/arena.db"),
) -> None:
    """Execute completion trials with deterministic caching and resumability."""
    try:
        _, expanded, jobs = _jobs(str(suite), contestants, repeats)
    except (OSError, ValueError, yaml.YAMLError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    try:
        suite_dir = _suite_dir(suite)
        models = _catalog_models(Path("catalog/models.yaml"))
    except (OSError, ValueError, yaml.YAMLError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    run_config = {
        "suite": str(suite_dir.resolve()),
        "contestants": [contestant.id for contestant in expanded],
        "repeats": repeats,
    }
    run_id = resume_id or content_id(run_config)

    def estimate(job: TrialJob) -> float | None:
        model = models.get(str(job.contestant.model))
        if model is None or model.price_per_mtok is None:
            return None
        params = dict(job.contestant.params)
        input_tokens = int(params.get("estimated_input_tokens", 0))
        output_tokens = int(params.get("estimated_output_tokens", params.get("max_tokens", 0)))
        return (
            input_tokens * model.price_per_mtok.get("in", 0)
            + output_tokens * model.price_per_mtok.get("out", 0)
        ) / 1_000_000

    task_paths = {
        Task.model_validate(parse_yaml(task_path)).id: task_path
        for task_path in sorted((suite_dir / "tasks").glob("*/task.yaml"))
    }

    async def execute(job: TrialJob) -> CompletionResult:
        task_path = task_paths[job.task.id]
        prompt_path = task_path.parent / job.task.prompt_file
        prompt = prompt_path.read_text(encoding="utf-8")
        return await mock_completion(job.contestant, job.task, prompt)

    if dry_run:
        summary = asyncio.run(
            run_jobs(
                None,
                run_id,
                jobs,
                lambda job: execute(job),
                dry_run=True,
                budget_usd=budget_usd,
                estimate_cost=estimate,
            )
        )
    else:
        with Store(db) as store:
            summary = asyncio.run(
                run_jobs(
                    store,
                    run_id,
                    jobs,
                    execute,
                    resume=resume_id is not None,
                    run_config=run_config,
                    budget_usd=budget_usd,
                    estimate_cost=estimate,
                    max_concurrency=max_concurrency,
                    per_contestant=per_contestant,
                )
            )
    typer.echo(
        f"Run {run_id}: planned={summary.planned} created={summary.created} "
        f"completed={summary.completed} cache_hits={summary.cache_hits} skipped={summary.skipped}"
    )


@app.command("ls")
def list_runs(
    db: Annotated[Path, typer.Option(help="SQLite run store.")] = Path(".arena/arena.db"),
) -> None:
    """List runs recorded in the local store."""
    if not db.exists():
        typer.echo("No runs.")
        return
    with Store(db) as store:
        for row in store.execute("SELECT id, status, created_at FROM runs ORDER BY created_at, id"):
            typer.echo(f"{row['id']}\t{row['status']}\t{row['created_at']}")


@app.command("resume")
def resume_run(
    run_id: Annotated[str, typer.Argument()],
    suite: Annotated[str, typer.Argument()],
    contestants: Annotated[list[str], typer.Option("-c", "--contestant")],
    repeats: Annotated[int, typer.Option(min=1)] = 3,
    db: Annotated[Path, typer.Option(help="SQLite run store.")] = Path(".arena/arena.db"),
) -> None:
    """Resume errored and missing trials for a recorded run."""
    run(
        suite,
        contestants,
        repeats,
        False,
        run_id,
        None,
        8,
        2,
        db,
    )
