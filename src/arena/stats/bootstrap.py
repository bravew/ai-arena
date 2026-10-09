"""Cluster bootstrap intervals for task-and-repeat evaluation scores (DEV_PLAN §8 Aggregation)."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_SAMPLES = 2_000
DEFAULT_CONFIDENCE = 0.95
DEFAULT_SEED = 0  # a report rerun on the same trials must print the same interval


@dataclass(frozen=True)
class ConfidenceInterval:
    """Percentile confidence interval and its point estimate."""

    estimate: float
    low: float
    high: float
    confidence: float

    @property
    def contains_zero(self) -> bool:
        return self.low <= 0.0 <= self.high


def cluster_bootstrap_ci(
    clusters: Sequence[Sequence[float]],
    *,
    samples: int = DEFAULT_SAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_SEED,
) -> ConfidenceInterval:
    """Confidence interval for the mean of per-cluster means, tasks weighted equally.

    Each cluster holds one task's repeats. Every bootstrap sample draws tasks with
    replacement, then draws repeats with replacement inside each drawn task, so both the
    spread between tasks and the spread between repeats show up in the interval. The
    estimate is the plain mean of the task means, not the bootstrap mean.
    """
    if not clusters:
        raise ValueError("at least one task cluster is required")
    if any(not cluster for cluster in clusters):
        raise ValueError("task clusters must each contain at least one repeat")
    if any(not math.isfinite(value) for cluster in clusters for value in cluster):
        raise ValueError("bootstrap values must be finite")
    if samples < 1:
        raise ValueError("samples must be positive")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")

    rng = random.Random(seed)
    task_count = len(clusters)
    distribution = sorted(
        math.fsum(_resampled_mean(clusters[rng.randrange(task_count)], rng) for _ in clusters)
        / task_count
        for _ in range(samples)
    )
    tail = (1 - confidence) / 2
    return ConfidenceInterval(
        estimate=math.fsum(_mean(cluster) for cluster in clusters) / task_count,
        low=_quantile(distribution, tail),
        high=_quantile(distribution, 1 - tail),
        confidence=confidence,
    )


def _resampled_mean(cluster: Sequence[float], rng: random.Random) -> float:
    size = len(cluster)
    return math.fsum(cluster[rng.randrange(size)] for _ in range(size)) / size


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    """Linear interpolation between the two nearest order statistics."""
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction
