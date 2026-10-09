"""Per-trial, per-task, and suite score rollups (DEV_PLAN §8 Aggregation)."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TypedDict, cast

from arena.stats.bootstrap import (
    DEFAULT_SAMPLES,
    DEFAULT_SEED,
    ConfidenceInterval,
    cluster_bootstrap_ci,
)


class TrialScoreData(TypedDict, total=False):
    """Mapping input accepted at the aggregation boundary."""

    contestant_id: str
    task_id: str
    attempt: int
    score: float
    passed: bool | None
    swapped: bool
    unmetered: bool
    kit_unapplied: bool


@dataclass(frozen=True)
class TrialScore:
    """One already-scored trial, including the flags that affect headline eligibility."""

    contestant_id: str
    task_id: str
    attempt: int
    score: float
    passed: bool | None = None
    swapped: bool = False
    unmetered: bool = False
    kit_unapplied: bool = False


@dataclass(frozen=True)
class ExcludedTrials:
    """Counts of excluded trials, both by reason and once in total."""

    swapped: int = 0
    unmetered: int = 0
    kit_unapplied: int = 0
    total: int = 0


@dataclass(frozen=True)
class TaskAggregate:
    task_id: str
    mean: float
    repeats: int
    passed: int
    pass_at_k: float | None
    pass_power_k: float | None


@dataclass(frozen=True)
class ContestantAggregate:
    tasks: Mapping[str, TaskAggregate]
    suite: ConfidenceInterval | None
    excluded: ExcludedTrials


@dataclass(frozen=True)
class HeadToHead:
    difference: ConfidenceInterval
    wins: int
    ties: int
    losses: int

    @property
    def no_detectable_difference(self) -> bool:
        """Whether the paired confidence interval includes zero."""
        return self.difference.contains_zero


@dataclass(frozen=True)
class Aggregation:
    contestants: Mapping[str, ContestantAggregate]
    _records: Mapping[str, Mapping[str, tuple[TrialScore, ...]]]
    bootstrap_samples: int
    seed: int

    def compare(self, left: str, right: str) -> HeadToHead:
        """Compare contestants using paired task means and a cluster bootstrap."""
        left_tasks = self._records[left]
        right_tasks = self._records[right]
        shared_tasks = sorted(left_tasks.keys() & right_tasks.keys())
        if not shared_tasks:
            raise ValueError("contestants have no shared eligible tasks")

        paired_clusters: list[list[float]] = []
        task_differences: list[float] = []
        for task_id in shared_tasks:
            left_by_attempt = {record.attempt: record.score for record in left_tasks[task_id]}
            right_by_attempt = {record.attempt: record.score for record in right_tasks[task_id]}
            attempts = sorted(left_by_attempt.keys() & right_by_attempt.keys())
            if not attempts:
                continue
            differences = [left_by_attempt[a] - right_by_attempt[a] for a in attempts]
            paired_clusters.append(differences)
            task_differences.append(_mean(differences))
        if not paired_clusters:
            raise ValueError("contestants have no paired eligible repeats")

        interval = cluster_bootstrap_ci(
            paired_clusters,
            samples=self.bootstrap_samples,
            seed=self.seed,
        )
        wins = sum(difference > 0 for difference in task_differences)
        losses = sum(difference < 0 for difference in task_differences)
        return HeadToHead(interval, wins, len(task_differences) - wins - losses, losses)


def aggregate_scores(
    records: Iterable[TrialScore | Mapping[str, object]],
    *,
    bootstrap_samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
    pass_k: int | None = None,
) -> Aggregation:
    """Aggregate scores equally by task and exclude unreliable trials from headlines.

    Pass@k estimates the chance of at least one success when drawing k repeats from
    the available n repeats, using the unbiased estimator 1 - C(n-c,k)/C(n,k).
    pass^k is the empirical chance all k independent repetitions succeed, (c/n)**k.
    By default k is n; pass_k can choose a common k bounded by n per task. Missing
    pass labels make both estimates unavailable for that task.
    """
    if bootstrap_samples < 1:
        raise ValueError("bootstrap_samples must be positive")
    if pass_k is not None and pass_k < 1:
        raise ValueError("pass_k must be positive")
    contestants: dict[str, list[TrialScore]] = defaultdict(list)
    excluded_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"swapped": 0, "unmetered": 0, "kit_unapplied": 0, "total": 0}
    )
    seen_attempts: set[tuple[str, str, int]] = set()
    for item in records:
        record = item if isinstance(item, TrialScore) else _parse_trial_score(item)
        _validate_trial(record)
        attempt_key = (record.contestant_id, record.task_id, record.attempt)
        if attempt_key in seen_attempts:
            raise ValueError(
                "duplicate attempt for contestant/task: "
                f"{record.contestant_id}/{record.task_id} attempt {record.attempt}"
            )
        seen_attempts.add(attempt_key)
        reasons = [
            flag for flag in ("swapped", "unmetered", "kit_unapplied") if getattr(record, flag)
        ]
        if reasons:
            counts = excluded_counts[record.contestant_id]
            for reason in reasons:
                counts[reason] += 1
            counts["total"] += 1
            continue
        contestants[record.contestant_id].append(record)

    for contestant_id in excluded_counts:
        contestants.setdefault(contestant_id, [])

    aggregates: dict[str, ContestantAggregate] = {}
    task_records_by_contestant: dict[str, Mapping[str, tuple[TrialScore, ...]]] = {}
    for contestant_id, eligible_records in contestants.items():
        tasks: dict[str, list[TrialScore]] = defaultdict(list)
        for record in eligible_records:
            tasks[record.task_id].append(record)
        task_records = {
            task_id: tuple(sorted(task_records, key=lambda record: record.attempt))
            for task_id, task_records in tasks.items()
        }
        task_aggregates: dict[str, TaskAggregate] = {}
        for task_id, task_records_for_task in task_records.items():
            passes = [record.passed for record in task_records_for_task]
            complete_passes = all(passed is not None for passed in passes)
            successes = sum(passed is True for passed in passes)
            repeat_count = len(passes)
            k = pass_k if pass_k is not None else repeat_count
            if k > repeat_count:
                raise ValueError(
                    f"pass_k ({k}) exceeds eligible repeats ({repeat_count}) for task {task_id}"
                )
            pass_at_k = _pass_at_k(repeat_count, successes, k) if complete_passes else None
            pass_power_k = (successes / repeat_count) ** k if complete_passes else None
            task_aggregates[task_id] = TaskAggregate(
                task_id=task_id,
                mean=_mean(record.score for record in task_records_for_task),
                repeats=repeat_count,
                passed=successes,
                pass_at_k=pass_at_k,
                pass_power_k=pass_power_k,
            )
        suite_interval = (
            cluster_bootstrap_ci(
                [
                    [record.score for record in task_records[task_id]]
                    for task_id in sorted(task_records)
                ],
                samples=bootstrap_samples,
                seed=seed,
            )
            if task_records
            else None
        )
        counts = excluded_counts[contestant_id]
        aggregates[contestant_id] = ContestantAggregate(
            tasks=task_aggregates,
            suite=suite_interval,
            excluded=ExcludedTrials(
                swapped=counts["swapped"],
                unmetered=counts["unmetered"],
                kit_unapplied=counts["kit_unapplied"],
                total=counts["total"],
            ),
        )
        task_records_by_contestant[contestant_id] = task_records

    return Aggregation(aggregates, task_records_by_contestant, bootstrap_samples, seed)


def _parse_trial_score(item: Mapping[str, object]) -> TrialScore:
    """Validate mapping keys and field types before constructing a typed trial."""
    allowed = {
        "contestant_id",
        "task_id",
        "attempt",
        "score",
        "passed",
        "swapped",
        "unmetered",
        "kit_unapplied",
    }
    extra = item.keys() - allowed
    if extra:
        raise ValueError(f"unknown trial score fields: {', '.join(sorted(extra))}")
    required = ("contestant_id", "task_id", "attempt", "score")
    missing = [key for key in required if key not in item]
    if missing:
        raise ValueError(f"missing trial score fields: {', '.join(missing)}")

    contestant_id = item["contestant_id"]
    task_id = item["task_id"]
    attempt = item["attempt"]
    score = item["score"]
    passed = item.get("passed")
    flags = {key: item.get(key, False) for key in ("swapped", "unmetered", "kit_unapplied")}
    if not isinstance(contestant_id, str) or not contestant_id:
        raise ValueError("contestant_id must be a non-empty string")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id must be a non-empty string")
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise ValueError("attempt must be an integer")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("score must be numeric")
    if passed is not None and not isinstance(passed, bool):
        raise ValueError("passed must be a boolean or None")
    if any(not isinstance(flag, bool) for flag in flags.values()):
        raise ValueError("trial exclusion flags must be booleans")
    return TrialScore(
        contestant_id=contestant_id,
        task_id=task_id,
        attempt=attempt,
        score=float(score),
        passed=passed,
        **cast(dict[str, bool], flags),
    )


def _validate_trial(record: TrialScore) -> None:
    if not record.contestant_id or not record.task_id:
        raise ValueError("contestant_id and task_id must be non-empty")
    if record.attempt < 1:
        raise ValueError("attempt must be a positive integer")
    if not math.isfinite(record.score) or not 0 <= record.score <= 1:
        raise ValueError("trial score must be finite and between 0 and 1")


def _pass_at_k(n: int, successes: int, k: int) -> float:
    """Unbiased probability of ≥1 success among k draws without replacement."""
    if k > n or n - successes < k:
        return 1.0
    if successes == 0:
        return 0.0
    # Avoid large integer-to-float conversion by using a product ratio.
    failure_probability = math.prod((n - successes - i) / (n - i) for i in range(k))
    return 1.0 - failure_probability


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    if not materialized:
        raise ValueError("cannot calculate the mean of no values")
    return math.fsum(materialized) / len(materialized)
