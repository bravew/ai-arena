"""Fair per-key concurrency and request-rate lanes for the gateway."""

from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic

KEEPALIVE_SECONDS = 15.0
RPM_WINDOW_SECONDS = 60.0


class LaneRejected(Exception):
    """A lane could not admit a request before its queue limit or deadline."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(f"gateway lane is full; retry after {self.retry_after} seconds")

    @property
    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)}


@dataclass(slots=True)
class AdaptiveConcurrency:
    """A concurrency limit that backs off on 429 and recovers after successes."""

    maximum: int
    enabled: bool = True
    successes_to_recover: int = 5
    _successes: int = 0

    def __post_init__(self) -> None:
        if self.maximum < 1:
            raise ValueError("concurrency must be positive")
        if self.successes_to_recover < 1:
            raise ValueError("successes_to_recover must be positive")
        self.current = self.maximum

    current: int = 0

    def on_rate_limit(self) -> int:
        if self.enabled:
            self.current = max(1, self.current // 2)
            self._successes = 0
        return self.current

    def on_success(self) -> int:
        if self.enabled:
            self._successes += 1
            if self._successes >= self.successes_to_recover:
                recovered = max(self.current + 1, math.ceil(self.current * 1.5))
                self.current = min(self.maximum, recovered)
                self._successes = 0
        return self.current


@dataclass(frozen=True, slots=True)
class LanePermit:
    """A granted lane slot and the time spent waiting for it."""

    queue_ms: int


class KeyLane:
    """A FIFO lane enforcing in-flight concurrency, a rolling RPM limit and bounded waiting."""

    def __init__(
        self,
        *,
        concurrency: int,
        rpm: int | None = None,
        queue: int = 64,
        queue_wait: float = 120.0,
        adaptive: bool = False,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        if rpm is not None and rpm < 1:
            raise ValueError("rpm must be positive")
        if queue < 0:
            raise ValueError("queue cannot be negative")
        if queue_wait < 0:
            raise ValueError("queue_wait cannot be negative")
        self._clock = clock
        self._concurrency = AdaptiveConcurrency(concurrency, enabled=adaptive)
        self._rpm = rpm
        self._queue_limit = queue
        self._queue_wait = queue_wait
        self._active = 0
        self._requests: deque[float] = deque()
        self._condition = asyncio.Condition()
        self._waiters: deque[object] = deque()

    @property
    def in_flight(self) -> int:
        return self._active

    @property
    def queued(self) -> int:
        return len(self._waiters)

    @property
    def concurrency(self) -> int:
        return self._concurrency.current

    async def acquire(
        self,
        *,
        streaming: bool = False,
        keepalive: Callable[[], Awaitable[None]] | None = None,
    ) -> LanePermit:
        """Wait fairly for a permit, sending SSE comments outside the condition lock."""
        started = self._clock()
        deadline = started + self._queue_wait
        waiter = object()
        async with self._condition:
            self._expire_requests(started)
            if not self._waiters and self._can_admit(started):
                self._active += 1
                self._requests.append(started)
                return LanePermit(queue_ms=0)
            if len(self._waiters) >= self._queue_limit:
                raise LaneRejected(self._retry_after(started))
            self._waiters.append(waiter)
            self._condition.notify_all()

        try:
            while True:
                send_keepalive = False
                async with self._condition:
                    now = self._clock()
                    self._expire_requests(now)
                    if self._waiters[0] is waiter and self._can_admit(now):
                        self._waiters.popleft()
                        self._active += 1
                        self._requests.append(now)
                        self._condition.notify_all()
                        return LanePermit(queue_ms=max(0, round((now - started) * 1000)))
                    remaining = deadline - now
                    if remaining <= 0:
                        raise LaneRejected(self._retry_after(now))
                    until_rate_slot = self._seconds_until_rate_slot(now)
                    wait_for = min(remaining, until_rate_slot)
                    if streaming and keepalive is not None:
                        wait_for = min(wait_for, KEEPALIVE_SECONDS)
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=max(wait_for, 0.001))
                    except TimeoutError:
                        send_keepalive = streaming and keepalive is not None
                if send_keepalive and keepalive is not None:
                    await keepalive()
        finally:
            async with self._condition:
                with suppress(ValueError):
                    self._waiters.remove(waiter)
                self._condition.notify_all()

    async def release(self) -> None:
        """Release one previously granted slot and wake queued callers."""
        async with self._condition:
            if self._active <= 0:
                raise RuntimeError("cannot release an unacquired lane slot")
            self._active -= 1
            self._condition.notify_all()

    async def report_success(self) -> None:
        """Record an upstream success for adaptive concurrency recovery."""
        async with self._condition:
            previous = self._concurrency.current
            current = self._concurrency.on_success()
            if current != previous:
                self._condition.notify_all()

    async def report_rate_limit(self) -> None:
        """Halve adaptive concurrency after an upstream 429 response."""
        async with self._condition:
            previous = self._concurrency.current
            current = self._concurrency.on_rate_limit()
            if current != previous:
                self._condition.notify_all()

    def _can_admit(self, now: float) -> bool:
        return self._active < self._concurrency.current and (
            self._rpm is None or len(self._requests) < self._rpm
        )

    def _expire_requests(self, now: float) -> None:
        if self._rpm is None:
            return
        while self._requests and now - self._requests[0] >= RPM_WINDOW_SECONDS:
            self._requests.popleft()

    def _seconds_until_rate_slot(self, now: float) -> float:
        if self._rpm is None or len(self._requests) < self._rpm:
            return self._queue_wait
        return max(0.001, RPM_WINDOW_SECONDS - (now - self._requests[0]))

    def _retry_after(self, now: float) -> float:
        if self._rpm is not None and len(self._requests) >= self._rpm:
            return max(1.0, RPM_WINDOW_SECONDS - (now - self._requests[0]))
        return max(1.0, self._queue_wait)


class LanePool:
    """Lazily create independent lanes keyed by provider key or subscription account."""

    def __init__(self, **lane_options: int | float | bool | None) -> None:
        self._lane_options = lane_options
        self._lanes: dict[str, KeyLane] = {}

    def for_key(self, key: str) -> KeyLane:
        if not key:
            raise ValueError("lane key cannot be empty")
        if key not in self._lanes:
            self._lanes[key] = KeyLane(**self._lane_options)  # type: ignore[arg-type]
        return self._lanes[key]
