"""Deterministic run comparisons using the score intervals from aggregation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from arena.stats.aggregate import Aggregation, TrialScore
from arena.stats.bootstrap import ConfidenceInterval, cluster_bootstrap_ci


@dataclass(frozen=True)
class TaskChange:
    task_id: str
    before: float | None
    after: float | None
    difference: float | None
    status: Literal["improved", "regressed", "unchanged", "added", "removed", "uncertain"]


@dataclass(frozen=True)
class RunDiff:
    contestant_id: str
    tasks: tuple[TaskChange, ...]
    cost_delta_usd: float | None
    changed_contestants: tuple[str, ...]


def diff_runs(
    before: Aggregation,
    after: Aggregation,
    *,
    before_cost_usd: dict[str, float] | None = None,
    after_cost_usd: dict[str, float] | None = None,
    changed_contestants: tuple[str, ...] = (),
    before_records: Iterable[TrialScore] = (),
    after_records: Iterable[TrialScore] = (),
) -> tuple[RunDiff, ...]:
    """Compare task estimates; call a change directional only when paired CI excludes zero.

    Missing contestant/task data is represented as added/removed/None. Cost is None
    unless both runs supply costs for that contestant.
    """
    before_cost_usd = before_cost_usd or {}
    after_cost_usd = after_cost_usd or {}
    before_by_task: dict[tuple[str, str], dict[int, float]] = {}
    after_by_task: dict[tuple[str, str], dict[int, float]] = {}
    for record in before_records:
        if record.swapped or record.unmetered or record.kit_unapplied:
            continue
        before_by_task.setdefault((record.contestant_id, record.task_id), {})[record.attempt] = (
            record.score
        )
    for record in after_records:
        if record.swapped or record.unmetered or record.kit_unapplied:
            continue
        after_by_task.setdefault((record.contestant_id, record.task_id), {})[record.attempt] = (
            record.score
        )
    output: list[RunDiff] = []
    for contestant_id in sorted(before.contestants.keys() | after.contestants.keys()):
        left = before.contestants.get(contestant_id)
        right = after.contestants.get(contestant_id)
        task_ids: set[str] = set(left.tasks if left else ()) | set(right.tasks if right else ())
        changes: list[TaskChange] = []
        for task_id in sorted(task_ids):
            left_task = left.tasks.get(task_id) if left else None
            right_task = right.tasks.get(task_id) if right else None
            if left_task is None or right_task is None:
                changes.append(
                    TaskChange(
                        task_id,
                        left_task.mean if left_task else None,
                        right_task.mean if right_task else None,
                        None,
                        "added" if left_task is None else "removed",
                    )
                )
                continue
            left_by_attempt = before_by_task.get((contestant_id, task_id), {})
            right_by_attempt = after_by_task.get((contestant_id, task_id), {})
            attempts = sorted(left_by_attempt.keys() & right_by_attempt.keys())
            paired_differences = [
                right_by_attempt[attempt] - left_by_attempt[attempt] for attempt in attempts
            ]
            difference = (
                sum(paired_differences) / len(paired_differences) if paired_differences else None
            )
            if difference == 0:
                status: Literal[
                    "improved", "regressed", "unchanged", "added", "removed", "uncertain"
                ] = "unchanged"
            else:
                interval: ConfidenceInterval | None = (
                    cluster_bootstrap_ci(
                        [paired_differences],
                        samples=after.bootstrap_samples,
                        seed=after.seed,
                    )
                    if len(paired_differences) > 1
                    else None
                )
                status = (
                    "improved"
                    if interval and interval.low > 0
                    else "regressed"
                    if interval and interval.high < 0
                    else "uncertain"
                )
            changes.append(TaskChange(task_id, left_task.mean, right_task.mean, difference, status))
        cost_delta = (
            after_cost_usd[contestant_id] - before_cost_usd[contestant_id]
            if contestant_id in after_cost_usd and contestant_id in before_cost_usd
            else None
        )
        output.append(
            RunDiff(contestant_id, tuple(changes), cost_delta, tuple(sorted(changed_contestants)))
        )
    return tuple(output)
