"""Measure in-process gateway request overhead against the deterministic mock."""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Any

from arena.providers.mock import MockProvider


@dataclass(frozen=True, slots=True)
class BenchResult:
    iterations: int
    p50_ms: float
    p95_ms: float
    added_p50_ms: float


def benchmark_mock(iterations: int = 1000) -> BenchResult:
    """Measure a mock completion and its serialization-free baseline per iteration."""
    if iterations < 1:
        raise ValueError("iterations must be positive")
    provider = MockProvider()
    request: dict[str, Any] = {
        "model": "mock/example",
        "messages": [{"role": "user", "content": "ping"}],
    }
    baseline: list[float] = []
    gateway: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        _mock_baseline(request)
        baseline.append((time.perf_counter_ns() - started) / 1_000_000)
        started = time.perf_counter_ns()
        provider.complete("chat", request)
        gateway.append((time.perf_counter_ns() - started) / 1_000_000)
    added = [max(0.0, full - base) for full, base in zip(gateway, baseline, strict=True)]
    return BenchResult(
        iterations=iterations,
        p50_ms=statistics.median(gateway),
        p95_ms=_percentile(gateway, 0.95),
        added_p50_ms=statistics.median(added),
    )


def _mock_baseline(request: dict[str, Any]) -> str:
    """Representative fake-upstream work used as the latency baseline."""
    return str(request.get("model", "mock"))


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * percentile))]
