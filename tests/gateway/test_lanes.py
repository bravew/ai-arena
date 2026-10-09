from __future__ import annotations

import asyncio

import pytest

import arena.gateway.lanes as lanes
from arena.gateway.lanes import AdaptiveConcurrency, KeyLane, LanePool, LaneRejected


def test_concurrency_limits_are_independent_per_key() -> None:
    async def scenario() -> None:
        lanes = LanePool(concurrency=1, queue=2)
        first = lanes.for_key("provider:key-a")
        second = lanes.for_key("provider:key-b")
        permit = await first.acquire()
        other_permit = await second.acquire()
        queued = asyncio.create_task(first.acquire())
        await asyncio.sleep(0)
        assert first.queued == 1
        assert second.in_flight == 1
        await first.release()
        waiting_permit = await queued
        assert waiting_permit.queue_ms >= 0
        await first.release()
        await second.release()
        with pytest.raises(RuntimeError, match="unacquired"):
            await first.release()
        assert permit.queue_ms == other_permit.queue_ms == 0

    asyncio.run(scenario())


def test_queue_overflow_has_retry_after_and_waiters_are_fifo() -> None:
    async def scenario() -> None:
        lane = KeyLane(concurrency=1, queue=2, queue_wait=1)
        await lane.acquire()
        order: list[int] = []

        async def wait(index: int) -> None:
            await lane.acquire()
            order.append(index)
            await lane.release()

        first = asyncio.create_task(wait(1))
        await asyncio.sleep(0)
        second = asyncio.create_task(wait(2))
        await asyncio.sleep(0)
        assert lane.queued == 2
        with pytest.raises(LaneRejected) as rejected:
            await lane.acquire()
        assert rejected.value.headers == {"Retry-After": "1"}
        await lane.release()
        await asyncio.gather(first, second)
        assert order == [1, 2]

    asyncio.run(scenario())


def test_queue_wait_deadline_rejects_with_retry_after() -> None:
    async def scenario() -> None:
        lane = KeyLane(concurrency=1, queue=1, queue_wait=0.02)
        await lane.acquire()
        with pytest.raises(LaneRejected) as rejected:
            await lane.acquire()
        assert rejected.value.retry_after >= 1
        assert lane.queued == 0
        await lane.release()

    asyncio.run(scenario())


def test_rpm_window_limits_requests_even_when_concurrency_is_available() -> None:
    async def scenario() -> None:
        lane = KeyLane(concurrency=4, rpm=1, queue=1, queue_wait=0.02)
        await lane.acquire()
        await lane.release()
        with pytest.raises(LaneRejected) as rejected:
            await lane.acquire()
        assert rejected.value.retry_after >= 59
        assert lane.queued == 0

    asyncio.run(scenario())


def test_streaming_waiter_sends_sse_keepalive_without_holding_lane_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lanes, "KEEPALIVE_SECONDS", 0.01)

    async def scenario() -> None:
        lane = KeyLane(concurrency=1, queue=1, queue_wait=1)
        await lane.acquire()
        channel: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        channel.put_nowait("occupied")
        sending = asyncio.Event()

        async def send_comment() -> None:
            sending.set()
            await channel.put(": keep-alive\\n\\n")

        waiter = asyncio.create_task(lane.acquire(streaming=True, keepalive=send_comment))
        await sending.wait()
        # The sender is blocked by the full channel; lane state must still remain unlocked.
        await asyncio.wait_for(lane.release(), timeout=0.1)
        assert await channel.get() == "occupied"
        await waiter
        assert await channel.get() == ": keep-alive\\n\\n"
        await lane.release()

    asyncio.run(scenario())


def test_keepalive_callback_cannot_extend_queue_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lanes, "KEEPALIVE_SECONDS", 0.001)

    async def scenario() -> None:
        lane = KeyLane(concurrency=1, queue=1, queue_wait=0.02)
        await lane.acquire()
        started = asyncio.Event()

        async def blocked_keepalive() -> None:
            started.set()
            await asyncio.Event().wait()

        waiting = asyncio.create_task(lane.acquire(streaming=True, keepalive=blocked_keepalive))
        await started.wait()
        with pytest.raises(LaneRejected):
            await asyncio.wait_for(waiting, timeout=0.1)
        assert lane.queued == 0
        await lane.release()

    asyncio.run(scenario())


def test_keepalive_uses_fixed_deadline_despite_frequent_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lanes, "KEEPALIVE_SECONDS", 0.04)

    async def scenario() -> None:
        lane = KeyLane(concurrency=1, queue=1, queue_wait=0.2)
        await lane.acquire()
        sent = asyncio.Event()

        async def send_comment() -> None:
            sent.set()

        waiting = asyncio.create_task(lane.acquire(streaming=True, keepalive=send_comment))
        for _ in range(30):
            async with lane._condition:
                lane._condition.notify_all()
            await asyncio.sleep(0.003)
        assert sent.is_set()
        await lane.release()
        await waiting
        await lane.release()

    asyncio.run(scenario())


def test_lane_pool_bounds_distinct_keys_without_evicting_live_lanes() -> None:
    pool = LanePool(concurrency=1, max_keys=2)
    first = pool.for_key("provider:key-a")
    second = pool.for_key("provider:key-b")
    assert pool.for_key("provider:key-a") is first
    with pytest.raises(lanes.LanePoolFull):
        pool.for_key("provider:key-c")
    assert pool.for_key("provider:key-b") is second


def test_lane_pool_requires_positive_key_limit() -> None:
    with pytest.raises(ValueError, match="max_keys"):
        LanePool(max_keys=0)


def test_adaptive_concurrency_halves_on_429_and_recovers_after_five_successes() -> None:
    limit = AdaptiveConcurrency(maximum=16)
    assert limit.current == 16
    assert limit.on_rate_limit() == 8
    assert limit.on_rate_limit() == 4
    for _ in range(4):
        assert limit.on_success() == 4
    assert limit.on_success() == 6
    for _ in range(5):
        limit.on_success()
    assert limit.current == 9
    for _ in range(50):
        limit.on_success()
    assert limit.current == 16


def test_adaptive_mode_can_be_disabled() -> None:
    limit = AdaptiveConcurrency(maximum=8, enabled=False)
    for _ in range(10):
        limit.on_rate_limit()
        limit.on_success()
    assert limit.current == 8
