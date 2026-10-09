from __future__ import annotations

import asyncio
from pathlib import Path

from typer.testing import CliRunner

from arena.cli import app
from arena.core.modelref import ModelRef
from arena.core.models import Contestant, Task
from arena.core.store import Store
from arena.runners.completion import CompletionResult
from arena.runners.scheduler import SubscriptionRest, TrialJob, run_jobs


def contestant(name: str = "echo") -> Contestant:
    return Contestant(model=ModelRef.parse(f"mock/{name}"))


def task(name: str = "task") -> Task:
    return Task(id=name, version=1, kind="content", prompt_file="prompt.md")


def test_expands_exact_jobs_and_persists_cache_resume(tmp_path: Path) -> None:
    jobs = [
        TrialJob(contestant=contestant(model), task=task(task_id), repeat=repeat)
        for model in ("echo", "canned")
        for task_id in ("one", "two", "three")
        for repeat in (1, 2)
    ]
    calls: list[str] = []

    async def execute(job: TrialJob) -> CompletionResult:
        calls.append(f"{job.contestant.model}:{job.task.id}")
        return CompletionResult(text="done")

    with Store(tmp_path / "arena.db") as store:
        first = asyncio.run(run_jobs(store, "run-1", jobs, execute))
        assert first.created == 12
        assert first.completed == 12
        assert first.cache_hits == 0
        assert len(calls) == 12
        second = asyncio.run(run_jobs(store, "run-1", jobs, execute))
        assert second.created == 0
        assert second.cache_hits == 12
        assert len(calls) == 12
        store.execute("UPDATE trials SET status='errored' WHERE id=(SELECT id FROM trials LIMIT 1)")
        resumed = asyncio.run(run_jobs(store, "run-1", jobs, execute, resume=True))
        assert resumed.completed == 1
        assert len(calls) == 13


def test_scheduler_enforces_both_concurrency_caps() -> None:
    running = 0
    peak = 0

    async def execute(job: TrialJob) -> CompletionResult:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0)
        running -= 1
        return CompletionResult(text=job.task.id)

    jobs = [TrialJob(contestant=contestant(), task=task(str(i)), repeat=1) for i in range(8)]
    asyncio.run(run_jobs(None, "run", jobs, execute, max_concurrency=3, per_contestant=2))
    assert peak == 2


def test_dry_run_does_not_write_trials(tmp_path: Path) -> None:
    calls = 0

    async def dispatch(job: TrialJob) -> CompletionResult:
        nonlocal calls
        calls += 1
        return CompletionResult(text="done")

    with Store(tmp_path / "arena.db") as store:
        result = asyncio.run(
            run_jobs(
                store,
                "dry",
                [TrialJob(contestant=contestant(), task=task(), repeat=1)],
                dispatch,
                dry_run=True,
            )
        )
        assert result.created == 0
        assert result.planned == 1
        assert calls == 0
        assert store.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_estimated_budget_skips_trials_before_dispatch() -> None:
    calls = 0

    async def execute(job: TrialJob) -> CompletionResult:
        nonlocal calls
        calls += 1
        return CompletionResult(text="done", cost_usd=0.1)

    jobs = [
        TrialJob(contestant=contestant(), task=task(str(index)), repeat=1) for index in range(3)
    ]
    summary = asyncio.run(
        run_jobs(None, "budget", jobs, execute, budget_usd=0.2, estimate_cost=lambda job: 0.1)
    )
    assert summary.completed == 2
    assert summary.skipped == 1
    assert calls == 2


def test_cli_plan_and_run_dry_run(tmp_path: Path) -> None:
    cli = CliRunner()
    plan_result = cli.invoke(
        app,
        [
            "plan",
            "suites/smoke",
            "-c",
            "mock-a",
            "-c",
            "mock-b",
            "--repeats",
            "2",
        ],
    )
    assert plan_result.exit_code == 0, plan_result.output
    assert "Trials: 16" in plan_result.output
    run_result = cli.invoke(
        app,
        [
            "run",
            "suites/smoke",
            "-c",
            "mock-a",
            "-c",
            "mock-b",
            "--repeats",
            "2",
            "--dry-run",
            "--db",
            str(tmp_path / "untouched.db"),
        ],
    )
    assert run_result.exit_code == 0, run_result.output
    assert "planned=16 created=0" in run_result.output
    assert not (tmp_path / "untouched.db").exists()


def test_subscription_rest_holds_jobs_and_emits_countdown() -> None:
    events: list[tuple[str, int]] = []
    now = 100.0

    async def execute(job: TrialJob) -> CompletionResult:
        nonlocal now
        if now < 103:
            raise SubscriptionRest("account-a", reset_at=103)
        return CompletionResult(text="done")

    async def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    jobs = [TrialJob(contestant=contestant(), task=task(), repeat=1)]
    result = asyncio.run(
        run_jobs(
            None,
            "rest",
            jobs,
            execute,
            on_countdown=lambda account, seconds: events.append((account, seconds)),
            clock=lambda: now,
            sleep=sleep,
        )
    )
    assert result.completed == 1
    assert events == [("account-a", 3)]
