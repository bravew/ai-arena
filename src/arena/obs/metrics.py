"""In-process counters, gauges and latency percentiles for `/arena/stats`.

Everything here is cheap and bounded: a series count cap, a fixed-size sample window per
histogram, and one short lock. Recording never raises, because it runs on the request path.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from typing import Any

DEFAULT_MAX_SERIES = 1024
DEFAULT_WINDOW = 1024
SERIES_DROPPED = "metrics_series_dropped"


def series_key(name: str, labels: dict[str, str]) -> str:
    """`name` or `name{a=b,c=d}` with labels in sorted order."""
    if not labels:
        return name
    return name + "{" + ",".join(f"{key}={labels[key]}" for key in sorted(labels)) + "}"


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of a non-empty ascending list."""
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


class Metrics:
    """Thread-safe metric registry. A new series past `max_series` is dropped and counted."""

    def __init__(self, *, max_series: int = DEFAULT_MAX_SERIES, window: int = DEFAULT_WINDOW):
        if max_series < 1 or window < 1:
            raise ValueError("max_series and window must be positive")
        self._max_series = max_series
        self._window = window
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, deque[float]] = {}
        self._counts: dict[str, int] = {}

    def inc(self, name: str, amount: float = 1, **labels: str) -> None:
        key = series_key(name, labels)
        with self._lock:
            if key in self._counters or self._admit(key):
                self._counters[key] = self._counters.get(key, 0) + amount

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        key = series_key(name, labels)
        with self._lock:
            if key in self._gauges or self._admit(key):
                self._gauges[key] = value

    def observe(self, name: str, value: float, **labels: str) -> None:
        if not math.isfinite(value):
            return
        key = series_key(name, labels)
        with self._lock:
            samples = self._histograms.get(key)
            if samples is None:
                if not self._admit(key):
                    return
                samples = self._histograms[key] = deque(maxlen=self._window)
            samples.append(value)
            self._counts[key] = self._counts.get(key, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            histograms: dict[str, dict[str, float]] = {}
            for key, samples in self._histograms.items():
                ordered = sorted(samples)
                histograms[key] = {
                    "count": self._counts[key],
                    "p50": _percentile(ordered, 0.50),
                    "p95": _percentile(ordered, 0.95),
                    "max": ordered[-1],
                }
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": histograms,
            }

    def _admit(self, key: str) -> bool:
        """Whether a new series fits. Called with the lock held; counts what it refuses."""
        series = len(self._counters) + len(self._gauges) + len(self._histograms)
        if series < self._max_series:
            return True
        # The overflow counter is always allowed so a refusal is never invisible.
        self._counters[SERIES_DROPPED] = self._counters.get(SERIES_DROPPED, 0) + 1
        return False
