"""Deterministic trial expansion, persistence, resumable execution and run caps."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from arena.core.ids import content_id
from arena.core.models import Contestant, Task
from arena.core.store import Store
from arena.runners.completion import CompletionResult


@dataclass(frozen=True)
class TrialJob:
    contestant: Contestant
    task: Task
    repeat: int

    @property
    def id(self) -> str:
        return content_id(
            {
                "contestant": self.contestant.id,
                "task": self.task.id,
                "task_version": self.task.version,
                "repeat": self.repeat,
            }
        )


@dataclass(frozen=True)
class RunSummary:
    planned: int
    created: int
    completed: int
    cache_hits: int
    skipped: int = 0


class SubscriptionRest(RuntimeError):
    def __init__(self, account_id: str, reset_at: float) -> None:
        super().__init__(f"subscription account {account_id} resting until {reset_at}")
        self.account_id = account_id
        self.reset_at = reset_at


Countdown = Callable[[str, int], None]
Executor = Callable[[TrialJob], Awaitable[CompletionResult]]


def expand_jobs(
    suite: Sequence[Task], contestants: Sequence[Contestant], repeats: int
) -> list[TrialJob]:
    """Create one job per task, contestant and one-based repeat."""
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    return [
        TrialJob(contestant, task, repeat)
        for task in suite
        for contestant in contestants
        for repeat in range(1, repeats + 1)
    ]


def _trial_id(run_id: str, job: TrialJob) -> str:
    return content_id({"run_id": run_id, "job_id": job.id})


def _ensure_run(store: Store, run_id: str, config: dict[str, Any]) -> None:
    config_json = json.dumps(config, sort_keys=True)
    store.execute(
        "INSERT OR IGNORE INTO runs(id, config_json, status) VALUES (?, ?, 'running')",
        (run_id, config_json),
    )
    row = store.execute("SELECT config_json FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None or row["config_json"] != config_json:
        raise ValueError(f"run {run_id} does not match the requested trial plan")


def _prepare(
    store: Store, run_id: str, jobs: Sequence[TrialJob], resume: bool
) -> tuple[list[TrialJob], int, int]:
    selected: list[TrialJob] = []
    created = cache_hits = 0
    with store.transaction() as connection:
        for job in jobs:
            trial_id = _trial_id(run_id, job)
            row = connection.execute("SELECT status FROM trials WHERE id=?", (trial_id,)).fetchone()
            if row is not None and row["status"] == "succeeded":
                cache_hits += 1
                continue
            if row is not None and not resume:
                cache_hits += 1
                continue
            if row is None:
                connection.execute(
                    "INSERT INTO trials(id, run_id, contestant_id, task_id, attempt, status) "
                    "VALUES (?, ?, ?, ?, ?, 'queued')",
                    (trial_id, run_id, job.contestant.id, job.task.id, job.repeat),
                )
                created += 1
            else:
                connection.execute("UPDATE trials SET status='queued' WHERE id=?", (trial_id,))
            selected.append(job)
    return selected, created, cache_hits


async def run_jobs(
    store: Store | None,
    run_id: str,
    jobs: Sequence[TrialJob],
    dispatch: Executor,
    *,
    dry_run: bool = False,
    resume: bool = False,
    run_config: dict[str, Any] | None = None,
    max_concurrency: int = 8,
    per_contestant: int = 2,
    budget_usd: float | None = None,
    estimate_cost: Callable[[TrialJob], float | None] | None = None,
    on_countdown: Countdown | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RunSummary:
    if max_concurrency < 1 or per_contestant < 1:
        raise ValueError("concurrency caps must be positive")
    if budget_usd is not None and budget_usd < 0:
        raise ValueError("budget must be non-negative")
    if dry_run:
        return RunSummary(len(jobs), 0, 0, 0)
    if store is not None:
        config = run_config or {"trials": [job.id for job in jobs]}
        if resume:
            existing = store.execute(
                "SELECT config_json FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if existing is None:
                raise ValueError(f"cannot resume unknown run {run_id}")
            config = json.loads(existing["config_json"])
            requested = run_config or {"trials": [job.id for job in jobs]}
            if config != requested:
                raise ValueError(f"resume plan does not match stored run {run_id}")
        _ensure_run(store, run_id, config)
        pending, created, cache_hits = _prepare(store, run_id, jobs, resume)
    else:
        pending, created, cache_hits = list(jobs), 0, 0

    locks: dict[str, asyncio.Semaphore] = {}
    for job in pending:
        locks.setdefault(job.contestant.id, asyncio.Semaphore(per_contestant))
    gate = asyncio.Semaphore(max_concurrency)
    spend = 0.0
    reserved = 0.0
    spent_lock = asyncio.Lock()
    completed = skipped = 0

    async def execute(job: TrialJob) -> None:
        nonlocal spend, reserved, completed, skipped
        async with gate, locks[job.contestant.id]:
            estimate = estimate_cost(job) if estimate_cost is not None else None
            async with spent_lock:
                if (
                    estimate is not None
                    and budget_usd is not None
                    and spend + reserved + estimate > budget_usd
                ):
                    skipped += 1
                    if store is not None:
                        store.execute(
                            "UPDATE trials SET status='skipped' WHERE id=?",
                            (_trial_id(run_id, job),),
                        )
                    return
                if estimate is not None:
                    reserved += estimate
            if store is not None:
                store.execute(
                    "UPDATE trials SET status='running' WHERE id=?", (_trial_id(run_id, job),)
                )
            try:
                while True:
                    try:
                        result = await dispatch(job)
                        break
                    except SubscriptionRest as rest:
                        remaining = max(0, int(rest.reset_at - clock()))
                        if on_countdown is not None:
                            on_countdown(rest.account_id, remaining)
                        await sleep(float(max(remaining, 1)))
                async with spent_lock:
                    if estimate is not None:
                        reserved -= estimate
                    spend += result.cost_usd or 0.0
                    completed += 1
                if store is not None:
                    store.execute(
                        "UPDATE trials SET status='succeeded' WHERE id=?",
                        (_trial_id(run_id, job),),
                    )
            except Exception:
                if estimate is not None:
                    async with spent_lock:
                        reserved -= estimate
                if store is not None:
                    store.execute(
                        "UPDATE trials SET status='errored' WHERE id=?",
                        (_trial_id(run_id, job),),
                    )
                raise

    results = await asyncio.gather(*(execute(job) for job in pending), return_exceptions=True)
    cancellations = [result for result in results if isinstance(result, asyncio.CancelledError)]
    if cancellations:
        raise cancellations[0]
    failures = [result for result in results if isinstance(result, Exception)]
    if failures:
        if store is not None:
            store.execute("UPDATE runs SET status='errored' WHERE id=?", (run_id,))
        raise failures[0]
    if store is not None:
        store.execute("UPDATE runs SET status='succeeded' WHERE id=?", (run_id,))
    return RunSummary(len(jobs), created, completed, cache_hits, skipped)
