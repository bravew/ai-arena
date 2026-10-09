"""Integration of gateway decisions through the ASGI request path."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient
from starlette.types import Message, Scope

from arena.catalog.config import load_catalog
from arena.core.models import Call, Tokens
from arena.gateway.budget import Budget
from arena.gateway.lanes import LanePool, LanePoolFull, LaneRejected
from arena.gateway.server import (
    CallContext,
    DispatchContext,
    LedgerWriter,
    UpstreamResponse,
    create_app,
)
from arena.obs.events import EventLog
from arena.obs.metrics import Metrics
from arena.obs.otel import OtlpConfig, OtlpExporter
from arena.providers.config import load_providers

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = load_providers(ROOT / "providers.yaml")
CATALOG = load_catalog(ROOT / "catalog" / "models.yaml")


def _translator(source: str, target: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"model": payload["model"], "input": payload.get("messages", [])}


async def _response_body() -> AsyncIterator[bytes]:
    yield b'{"id":"fake","usage":{"input_tokens":2,"output_tokens":3}}'


def test_request_runs_through_dispatch_and_releases_lane(tmp_path: Path) -> None:
    events: list[str] = []
    lane_pool = LanePool(concurrency=1, adaptive=True)
    metrics = Metrics()

    class MemoryLedger(LedgerWriter):
        def __init__(self) -> None:
            self.calls: list[Call] = []

        def record(self, call: Call) -> Call:
            self.calls.append(call)
            return call

        def list_for_run(self, run_id: str) -> list[Call]:
            return [call for call in self.calls if call.run_id == run_id]

    ledger = MemoryLedger()
    event_log = EventLog(tmp_path / "events")

    async def dispatcher(context: DispatchContext) -> UpstreamResponse:
        events.append("dispatch")
        assert context.target.catalog_ref == "anthropic/claude-opus-5-5"
        assert context.prepared.body == b'{"model":"anthropic/claude-opus-5-5","messages":[]}'
        assert context.source_protocol == "anthropic"
        assert context.streaming is False
        assert not hasattr(context, "headers")
        return UpstreamResponse(
            200,
            {"content-type": "application/json"},
            _response_body(),
            Tokens.model_validate({"in": 2, "out": 3}),
        )

    app = create_app(
        PROVIDERS,
        CATALOG,
        dispatcher=dispatcher,
        context_provider=lambda target, caller: CallContext(
            "trial-1", "acct-hash", "call-1", 1, 0.0, "catalog-v1"
        ),
        translator=_translator,
        lanes=lane_pool,
        ledger=ledger,
        events=event_log,
        metrics=metrics,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer arena-trial-1"},
            json={"model": "anthropic/claude-opus-5-5", "messages": []},
        )
        assert response.status_code == 200
        assert response.json()["id"] == "fake"
        assert lane_pool.for_key("anthropic/main").in_flight == 0
    assert events == ["dispatch"]
    calls = ledger.list_for_run("trial-1")
    assert len(calls) == 1
    assert calls[0].queue_ms == 0
    assert calls[0].translated is False
    assert calls[0].effort_applied is None
    assert calls[0].tokens == Tokens.model_validate({"in": 2, "out": 3})
    assert metrics.snapshot()["counters"]["gateway_requests{status=200}"] == 1


def test_status_routes_require_ops_token(tmp_path: Path) -> None:
    app = create_app(PROVIDERS, CATALOG, lanes=LanePool(concurrency=1), metrics=Metrics())
    with TestClient(app) as client:
        assert (
            client.get(
                "/arena/health", headers={"Authorization": "Bearer arena-trial-1"}
            ).status_code
            == 403
        )
        assert (
            client.get("/arena/health", headers={"Authorization": "Bearer arena-ops"}).status_code
            == 200
        )
        assert client.get("/arena/lanes", headers={"Authorization": "Bearer arena-ops"}).json() == {
            "lanes": {}
        }


def test_exporter_starts_and_drains_with_app_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = OtlpExporter(OtlpConfig("http://127.0.0.1:4318/v1/traces"))
    started = False
    closed = False

    def start() -> OtlpExporter:
        nonlocal started
        started = True
        return exporter

    def close(*, drain_timeout: float = 3.0) -> None:
        nonlocal closed
        closed = True

    monkeypatch.setattr(exporter, "start", start)
    monkeypatch.setattr(exporter, "close", close)
    app = create_app(PROVIDERS, CATALOG, exporter=exporter)
    with TestClient(app):
        assert started
        assert not closed
    assert closed


def test_lane_rejections_map_to_retry_and_capacity_responses() -> None:
    class RejectingPool:
        def __init__(self, error: Exception) -> None:
            self.error = error

        def for_key(self, key: str):
            raise self.error

    dispatcher_called = False

    async def dispatcher(context: DispatchContext) -> UpstreamResponse:
        nonlocal dispatcher_called
        dispatcher_called = True
        return UpstreamResponse(200, {}, _response_body())

    request_body = {"model": "anthropic/claude-opus-5-5", "messages": []}
    headers = {"Authorization": "Bearer arena-trial-1"}
    lane_app = create_app(
        PROVIDERS,
        CATALOG,
        dispatcher=dispatcher,
        translator=_translator,
        lanes=RejectingPool(LaneRejected(7)),
    )
    with TestClient(lane_app) as client:
        rejected = client.post("/v1/messages", headers=headers, json=request_body)
    assert rejected.status_code == 429
    assert rejected.headers["retry-after"] == "7"

    full_app = create_app(
        PROVIDERS,
        CATALOG,
        dispatcher=dispatcher,
        translator=_translator,
        lanes=RejectingPool(LanePoolFull("full")),
    )
    with TestClient(full_app) as client:
        full = client.post("/v1/messages", headers=headers, json=request_body)
    assert full.status_code == 503
    assert "lane pool is full" in full.json()["error"]["message"]
    assert dispatcher_called is False


def test_dispatch_failure_releases_lane_and_redacts_failure(caplog: Any) -> None:
    lane_pool = LanePool(concurrency=1)
    secret = "sk-ant-api03-12345678901234567890"

    async def dispatcher(context: DispatchContext) -> UpstreamResponse:
        raise RuntimeError(f"upstream rejected secret {secret}")

    app = create_app(
        PROVIDERS, CATALOG, dispatcher=dispatcher, translator=_translator, lanes=lane_pool
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer arena-trial-1"},
            json={"model": "anthropic/claude-opus-5-5", "messages": []},
        )
    assert response.status_code == 502
    assert secret not in response.text
    assert secret not in caplog.text
    assert lane_pool.for_key("anthropic/main").in_flight == 0


def test_upstream_rate_limit_reports_lane_and_records_metadata() -> None:
    lane_pool = LanePool(concurrency=8, adaptive=True)
    recorded: list[Call] = []

    class RecordingLedger(LedgerWriter):
        def record(self, call: Call) -> Call:
            recorded.append(call)
            return call

    async def dispatcher(context: DispatchContext) -> UpstreamResponse:
        return UpstreamResponse(429, {"retry-after": "12"}, _response_body())

    app = create_app(
        PROVIDERS,
        CATALOG,
        dispatcher=dispatcher,
        context_provider=lambda target, caller: CallContext(
            "trial-1", "acct-hash", "call-429", 4, 0.0, "catalog-v1"
        ),
        translator=_translator,
        lanes=lane_pool,
        ledger=RecordingLedger(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer arena-trial-1"},
            json={"model": "anthropic/claude-opus-5-5", "messages": []},
        )
    assert response.status_code == 429
    assert lane_pool.for_key("anthropic/main").concurrency == 4
    assert len(recorded) == 1
    assert recorded[0].status == 429


def test_no_dispatcher_preserves_501() -> None:
    app = create_app(PROVIDERS, CATALOG)
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer arena-trial-1"},
            json={"model": "anthropic/claude-opus-5-5", "messages": []},
        )
    assert response.status_code == 501


def test_budget_and_ledger_require_validated_context_configuration() -> None:
    from arena.gateway.budget import Budget

    with pytest.raises(ValueError, match="validated call context provider"):
        create_app(PROVIDERS, CATALOG, ledger=RecordingLedgerForConfig())
    with pytest.raises(ValueError, match="requires an event log"):
        create_app(
            PROVIDERS,
            CATALOG,
            budget=Budget(),
            context_provider=lambda target, caller: None,
        )


class RecordingLedgerForConfig(LedgerWriter):
    def record(self, call: Call) -> Call:
        return call


def test_streaming_holds_lane_until_body_finishes() -> None:
    async def exercise() -> None:
        lane_pool = LanePool(concurrency=1)
        lane = lane_pool.for_key("anthropic/main")
        first_chunk_sent = asyncio.Event()
        allow_first_stream_to_finish = asyncio.Event()
        dispatch_count = 0

        async def body() -> AsyncIterator[bytes]:
            yield b"first"
            yield b"second"

        async def dispatcher(context: DispatchContext) -> UpstreamResponse:
            nonlocal dispatch_count
            dispatch_count += 1
            return UpstreamResponse(200, {"content-type": "text/event-stream"}, body())

        app = create_app(
            PROVIDERS, CATALOG, dispatcher=dispatcher, translator=_translator, lanes=lane_pool
        )

        async def request_receive() -> Message:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": request_bytes, "more_body": False}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def request_send(message: Message) -> None:
            if message["type"] == "http.response.body" and message.get("body") == b"first":
                first_chunk_sent.set()
                await allow_first_stream_to_finish.wait()

        async def make_request() -> None:
            nonlocal request_sent
            request_sent = False
            scope: Scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/v1/messages",
                "raw_path": b"/v1/messages",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"authorization", b"Bearer arena-trial-1"),
                    (b"content-type", b"application/json"),
                ],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
            }
            await app(scope, request_receive, request_send)

        request_bytes = b'{"model":"anthropic/claude-opus-5-5","stream":true,"messages":[]}'
        request_sent = False
        first_request = asyncio.create_task(make_request())
        await asyncio.wait_for(first_chunk_sent.wait(), timeout=1)
        assert lane.in_flight == 1

        second_request = asyncio.create_task(make_request())
        await asyncio.sleep(0)
        assert lane.queued == 1
        assert dispatch_count == 1
        assert lane.in_flight == 1

        allow_first_stream_to_finish.set()
        await asyncio.gather(first_request, second_request)
        assert dispatch_count == 2
        assert lane.in_flight == 0

    asyncio.run(exercise())


def test_streaming_body_failure_releases_lane_once() -> None:
    async def exercise() -> None:
        lane_pool = LanePool(concurrency=1)
        lane = lane_pool.for_key("anthropic/main")
        release_count = 0
        original_release = lane.release

        async def counting_release() -> None:
            nonlocal release_count
            release_count += 1
            await original_release()

        lane.release = counting_release  # type: ignore[method-assign]

        async def failing_body() -> AsyncIterator[bytes]:
            yield b"first"
            raise ConnectionError("upstream stream failed")

        async def dispatcher(context: DispatchContext) -> UpstreamResponse:
            return UpstreamResponse(200, {"content-type": "text/event-stream"}, failing_body())

        app = create_app(
            PROVIDERS, CATALOG, dispatcher=dispatcher, translator=_translator, lanes=lane_pool
        )
        request_bytes = b'{"model":"anthropic/claude-opus-5-5","stream":true,"messages":[]}'
        request_sent = False

        async def receive() -> Message:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": request_bytes, "more_body": False}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(message: Message) -> None:
            pass

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/messages",
            "raw_path": b"/v1/messages",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"authorization", b"Bearer arena-trial-1"),
                (b"content-type", b"application/json"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        with pytest.raises((ConnectionError, ExceptionGroup)):
            await app(scope, receive, send)
        assert lane.in_flight == 0
        assert release_count == 1

    asyncio.run(exercise())


def test_stream_cancellation_releases_lane_and_budget_once(tmp_path: Path) -> None:
    async def exercise() -> None:
        lane_pool = LanePool(concurrency=1)
        lane = lane_pool.for_key("anthropic/main")
        release_count = 0
        original_release = lane.release
        disconnect = asyncio.Event()

        async def counting_release() -> None:
            nonlocal release_count
            release_count += 1
            await original_release()

        lane.release = counting_release  # type: ignore[method-assign]

        async def body() -> AsyncIterator[bytes]:
            yield b"first"
            await asyncio.Event().wait()

        async def dispatcher(context: DispatchContext) -> UpstreamResponse:
            return UpstreamResponse(200, {"content-type": "text/event-stream"}, body())

        budget = Budget()
        events = EventLog(tmp_path / "events")
        call_context = CallContext("trial-1", "acct-hash", "call-stream", 1, 0.01, "v1")
        app = create_app(
            PROVIDERS,
            CATALOG,
            dispatcher=dispatcher,
            context_provider=lambda target, caller: call_context,
            translator=_translator,
            lanes=lane_pool,
            budget=budget,
            events=events,
        )
        request_bytes = b'{"model":"anthropic/claude-opus-5-5","stream":true,"messages":[]}'
        request_sent = False
        stream_started = asyncio.Event()

        async def receive() -> Message:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": request_bytes, "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            if message["type"] == "http.response.body" and message.get("body") == b"first":
                stream_started.set()
                assert budget.report().remaining_usd == pytest.approx(19.99)
                disconnect.set()

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/messages",
            "raw_path": b"/v1/messages",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"authorization", b"Bearer arena-trial-1"),
                (b"content-type", b"application/json"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        await asyncio.wait_for(app(scope, receive, send), timeout=1)
        assert stream_started.is_set()
        assert lane.in_flight == 0
        assert release_count == 1
        assert budget.report().remaining_usd == pytest.approx(20.0)

    asyncio.run(exercise())


def test_lane_permit_is_released_when_stream_disconnects(tmp_path: Path) -> None:
    lane_pool = LanePool(concurrency=1)

    async def disconnecting_body() -> AsyncIterator[bytes]:
        yield b"first"
        raise ConnectionError("client disconnected")

    async def dispatcher(context: DispatchContext) -> UpstreamResponse:
        return UpstreamResponse(200, {"content-type": "text/event-stream"}, disconnecting_body())

    app = create_app(
        PROVIDERS, CATALOG, dispatcher=dispatcher, translator=_translator, lanes=lane_pool
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer arena-trial-1"},
            json={"model": "anthropic/claude-opus-5-5", "stream": True, "messages": []},
        )
        assert response.status_code == 200
    assert lane_pool.for_key("anthropic/main").in_flight == 0
