"""`arena plan`, `arena run`, `arena ls` and `arena resume` commands."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, cast

import typer
import yaml

from arena.catalog.config import CatalogModel, load_catalog
from arena.cli import app
from arena.cli_score import StoreArtifactIndex, save_score
from arena.core.cas import ArtifactStore
from arena.core.ids import content_id
from arena.core.modelref import ModelRef
from arena.core.models import Contestant, Score, Task
from arena.core.store import Store
from arena.gateway.auth import token_for_trial
from arena.judges.client import HttpJudgeTransport, JudgeTransport, WireProtocol
from arena.kits.matrix import expand_matrix
from arena.providers.config import load_providers, parse_yaml
from arena.runners.agent_trial import adapter_for, execute_agent_trial
from arena.runners.completion import CompletionResult, mock_completion
from arena.runners.metered_gateway import GatewayContainer, GatewayRuntimeError, RunGateway
from arena.runners.run_record import RunRecorder, contestant_snapshot
from arena.runners.scheduler import TrialJob, expand_jobs, run_jobs
from arena.scorers.base import Scorer, ScorerContext, ScorerError, validate_score
from arena.scorers.docker_sandbox import docker_sandbox_factory, image_for_command
from arena.scorers.execution import SandboxFactory
from arena.scorers.task_scorers import JudgeSetup, TaskScorers, task_scorers


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


def _judge_transport(url: str, protocol: str) -> JudgeTransport:
    return HttpJudgeTransport(url, protocol=cast(WireProtocol, protocol))


def _sandbox_for(command: Sequence[str]) -> SandboxFactory:
    return docker_sandbox_factory(image_for_command(command))


def _store_answer(store: Store, artifacts: ArtifactStore, trial_id: str, text: str) -> None:
    """Keep a completion's text as the trial's `answer.md`, so it can be scored and shown."""
    store.execute(
        "INSERT OR REPLACE INTO trial_artifacts(trial_id, path, sha256, mime, render_hint) "
        "VALUES (?, 'answer.md', ?, 'text/markdown', 'markdown')",
        (trial_id, artifacts.put(text.encode("utf-8"))),
    )


async def _score_trial(
    store: Store,
    artifacts: ArtifactStore,
    recorder: RunRecorder,
    trial_id: str,
    scorers: Sequence[Scorer],
    prompt: str = "",
) -> None:
    """Score a trial's stored artifacts and record each result.

    A scorer that cannot run leaves the trial unscored and prints why, since an unknown score
    must not be written as a result. `arena score` can score it again.
    """
    if not scorers:
        return
    context = ScorerContext(
        trial_id=trial_id,
        artifacts={item.path: item for item in StoreArtifactIndex(store).for_trial(trial_id)},
        blobs=artifacts,
        config={"prompt": prompt},
    )

    def run_scorers() -> list[Score]:
        return [validate_score(scorer, context, scorer.score(context)) for scorer in scorers]

    try:
        results = await asyncio.to_thread(run_scorers)
    except ScorerError as error:
        typer.echo(f"warning: trial {trial_id} was not scored: {error}", err=True)
        return
    with store.transaction() as connection:
        for score in results:
            save_score(connection, score)
    for score in results:
        recorder.emit(
            "score_added",
            trial_id,
            scorer_id=score.scorer_id,
            scorer_version=score.scorer_version,
            normalized=score.normalized,
            passed=score.passed,
        )


def _jobs(
    suite: str, contestant_paths: list[str], repeats: int
) -> tuple[list[Task], list[Contestant], list[TrialJob]]:
    tasks = _suite_tasks(suite)
    contestants = _contestants(contestant_paths)
    return tasks, contestants, expand_jobs(tasks, contestants, repeats)


def _prior_run_spend(db: Path, run_id: str) -> float:
    """Metered spend already recorded for a run, so a resumed gateway keeps the cap."""
    if not db.exists() or db.stat().st_size == 0:
        return 0.0
    with Store(db) as store:
        row = store.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE run_id=?", (run_id,)
        ).fetchone()
    return float(row[0]) if row is not None else 0.0


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
    providers: Annotated[Path, typer.Option("--providers")] = Path("providers.yaml"),
    catalog: Annotated[Path, typer.Option("--catalog")] = Path("catalog/models.yaml"),
    home: Annotated[Path | None, typer.Option("--home")] = None,
    score_execution: Annotated[
        bool,
        typer.Option(
            "--score-execution",
            help="Also run each task's execution scorer in a Docker sandbox (needs Docker).",
        ),
    ] = False,
    judge_model: Annotated[
        str | None,
        typer.Option("--judge-model", help="Model that grades rubric-judge scorers."),
    ] = None,
    judge_url: Annotated[
        str | None,
        typer.Option("--judge-url", help="Gateway or endpoint the judge model is called through."),
    ] = None,
    judge_protocol: Annotated[
        str, typer.Option("--judge-protocol", help="Wire protocol: openai or anthropic.")
    ] = "openai",
) -> None:
    """Execute deterministic mock or metered agent trials with resumability."""
    try:
        tasks, expanded, jobs = _jobs(str(suite), contestants, repeats)
    except (OSError, ValueError, yaml.YAMLError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    try:
        suite_dir = _suite_dir(suite)
        models = _catalog_models(catalog)
    except (OSError, ValueError, yaml.YAMLError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    run_config = {
        "suite": str(suite_dir.resolve()),
        "suite_id": suite_dir.name,
        "contestants": [contestant_snapshot(contestant) for contestant in expanded],
        "repeats": repeats,
    }
    run_id = resume_id or content_id(run_config)
    non_mock = sorted(
        {
            contestant.model.provider
            for contestant in expanded
            if contestant.model.provider != "mock"
        }
    )
    non_mock_jobs = [
        job
        for job in jobs
        if job.contestant.model.provider != "mock" and job.contestant.scaffold is not None
    ]
    completion_jobs = [
        job
        for job in jobs
        if job.contestant.model.provider != "mock" and job.contestant.scaffold is None
    ]
    if completion_jobs and not dry_run:
        for job in completion_jobs:
            model = models.get(str(job.contestant.model))
            if model is None or (
                model.pricing != "subscription"
                and (
                    model.price_per_mtok is None
                    or model.price_per_mtok.get("in", 0) <= 0
                    or model.price_per_mtok.get("out", 0) <= 0
                )
            ):
                typer.echo(f"error: no usable catalog price for {job.contestant.model}", err=True)
                raise typer.Exit(code=2)
        typer.echo(
            "error: non-mock completion contestants remain unsupported; "
            "configure an agent scaffold",
            err=True,
        )
        raise typer.Exit(code=2)
    if non_mock_jobs:
        for job in non_mock_jobs:
            model = models.get(str(job.contestant.model))
            if model is None or (
                model.pricing != "subscription"
                and (
                    model.price_per_mtok is None
                    or model.price_per_mtok.get("in", 0) <= 0
                    or model.price_per_mtok.get("out", 0) <= 0
                )
            ):
                typer.echo(f"error: no usable catalog price for {job.contestant.model}", err=True)
                raise typer.Exit(code=2)
            try:
                token_for_trial(content_id({"run_id": run_id, "job_id": job.id}))
                adapter_for(job.contestant)
            except ValueError as error:
                typer.echo(f"error: invalid agent contestant: {error}", err=True)
                raise typer.Exit(code=2) from error
    if non_mock_jobs and not dry_run and (budget_usd is not None and not 0 < budget_usd <= 20):
        typer.echo("error: non-mock run budget must be greater than $0 and at most $20", err=True)
        raise typer.Exit(code=2)

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

    task_prompts = {
        task_id: (path.parent / Task.model_validate(parse_yaml(path)).prompt_file).read_text(
            encoding="utf-8"
        )
        for task_id, path in task_paths.items()
    }
    judge_setup: JudgeSetup | None = None
    if (judge_model is None) != (judge_url is None):
        typer.echo("error: --judge-model and --judge-url go together", err=True)
        raise typer.Exit(code=2)
    if judge_model is not None and judge_url is not None:
        if judge_protocol not in ("openai", "anthropic"):
            typer.echo("error: --judge-protocol must be openai or anthropic", err=True)
            raise typer.Exit(code=2)
        judge_setup = JudgeSetup(
            transport=_judge_transport(judge_url, judge_protocol),
            model=judge_model,
            run_id=run_id,
            rubric_root=Path.cwd(),
        )

    async def execute(job: TrialJob) -> CompletionResult:
        task_path = task_paths[job.task.id]
        prompt_path = task_path.parent / job.task.prompt_file
        if job.contestant.model.provider == "mock":
            prompt = prompt_path.read_text(encoding="utf-8")
            return await mock_completion(job.contestant, job.task, prompt)
        raise RuntimeError("non-mock agent execution requires the run gateway lifecycle")

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
        recorder = RunRecorder(run_id)
        try:
            providers_config = load_providers(providers) if non_mock else None
            gateway_container: GatewayContainer | None = None
            remaining_budget = budget_usd or 20.0
            if non_mock:
                assert providers_config is not None
                if any(job.contestant.scaffold is None for job in non_mock_jobs):
                    raise GatewayRuntimeError(
                        "non-mock contestants must select a configured agent scaffold"
                    )
                remaining_budget -= _prior_run_spend(db, run_id)
                if remaining_budget <= 0:
                    raise GatewayRuntimeError(
                        "the run budget is already spent by calls recorded before resume"
                    )
                gateway_container = GatewayContainer(
                    project_root=Path.cwd(),
                    run_id=run_id,
                    trial_models={
                        content_id({"run_id": run_id, "job_id": job.id}): (
                            f"{job.contestant.model.provider}/{job.contestant.model.model}"
                        )
                        for job in non_mock_jobs
                    },
                    providers_path=providers,
                    catalog_path=catalog,
                    database_path=db,
                    event_root=db.parent / "runs",
                    subscriptions_root=(home or Path(".arena")) / "subscriptions",
                    budget_usd=remaining_budget,
                    providers=providers_config,
                    provider_ids={job.contestant.model.provider for job in non_mock_jobs},
                )
                gateway_container.preflight(
                    [adapter_for(job.contestant).image for job in non_mock_jobs]
                )
            if gateway_container is not None:
                db.parent.mkdir(parents=True, exist_ok=True)
                db.touch(exist_ok=True)
                try:
                    gateway_container.start()
                    if not gateway_container.running:
                        raise GatewayRuntimeError("gateway did not enter the running state")
                except (GatewayRuntimeError, OSError) as error:
                    typer.echo(f"error: gateway preflight failed: {error}", err=True)
                    raise typer.Exit(code=2) from error
            try:
                with Store(db) as store:
                    artifact_store = ArtifactStore(db.parent / "artifacts")
                    if gateway_container is not None:
                        gateway = RunGateway(run_id, store, db.parent / "runs")

                        async def execute_with_gateway(job: TrialJob) -> CompletionResult:
                            task_path = task_paths[job.task.id]
                            if job.contestant.model.provider == "mock":
                                task_prompt = task_path.parent / job.task.prompt_file
                                return await mock_completion(
                                    job.contestant,
                                    job.task,
                                    task_prompt.read_text(encoding="utf-8"),
                                )
                            model = models.get(str(job.contestant.model))
                            if model is None:
                                raise GatewayRuntimeError(
                                    f"model is missing from the catalog: {job.contestant.model}"
                                )
                            if model.pricing == "subscription":
                                raise GatewayRuntimeError(
                                    "subscription accounting requires an imported account runtime"
                                )
                            budget = remaining_budget
                            if (
                                model.price_per_mtok is None
                                or model.price_per_mtok.get("in", 0) <= 0
                                or model.price_per_mtok.get("out", 0) <= 0
                            ):
                                raise GatewayRuntimeError(
                                    f"model has no usable catalog price: {job.contestant.model}"
                                )
                            return await execute_agent_trial(
                                job,
                                run_id,
                                task_file=task_path.parent / job.task.prompt_file,
                                workspace_root=db.parent / "workspaces",
                                store=store,
                                artifacts=artifact_store,
                                gateway=gateway,
                                gateway_url=gateway_container.url,
                                gateway_container=gateway_container.name,
                                budget_usd=budget,
                            )

                        executor = execute_with_gateway
                    else:
                        executor = execute

                    def built(task: Task) -> TaskScorers:
                        return task_scorers(
                            task,
                            task_paths[task.id].parent if score_execution else None,
                            _sandbox_for if score_execution else None,
                            judge_setup,
                        )

                    async def recorded(job: TrialJob) -> CompletionResult:
                        trial_id = content_id({"run_id": run_id, "job_id": job.id})
                        ids = {"contestant_id": job.contestant.id, "task_id": job.task.id}
                        recorder.emit("trial_started", trial_id, **ids)
                        try:
                            result = await executor(job)
                            if job.contestant.model.provider == "mock":
                                _store_answer(store, artifact_store, trial_id, result.text)
                            await _score_trial(
                                store,
                                artifact_store,
                                recorder,
                                trial_id,
                                built(job.task).scorers,
                                task_prompts[job.task.id],
                            )
                        except BaseException:
                            recorder.emit("trial_finished", trial_id, status="errored", **ids)
                            raise
                        recorder.emit("trial_finished", trial_id, status="succeeded", **ids)
                        return result

                    unapplied = sorted({sid for task in tasks for sid in built(task).unapplied})
                    if unapplied:
                        typer.echo(
                            "note: not applied by `arena run` (add --score-execution or "
                            "--judge-model with --judge-url): " + ", ".join(unapplied)
                        )
                    recorder.emit("run_started", suite_id=suite_dir.name)
                    run_status = "errored"
                    try:
                        summary = asyncio.run(
                            run_jobs(
                                store,
                                run_id,
                                jobs,
                                recorded,
                                resume=resume_id is not None,
                                run_config=run_config,
                                budget_usd=budget_usd,
                                estimate_cost=estimate,
                                max_concurrency=max_concurrency,
                                per_contestant=(
                                    1 if gateway_container is not None else per_contestant
                                ),
                            )
                        )
                        run_status = "succeeded"
                    finally:
                        recorder.emit("run_finished", status=run_status)
            finally:
                if gateway_container is not None:
                    gateway_container.stop()
                if recorder.pending:
                    recorder.write(db.parent)
        except (OSError, ValueError, GatewayRuntimeError) as error:
            typer.echo(f"error: agent run failed before completion: {error}", err=True)
            raise typer.Exit(code=2) from error
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
