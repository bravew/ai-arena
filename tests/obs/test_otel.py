"""Bounded OTLP export, metrics, structured logs and the /arena status endpoints (#21).

The collector is an in-process HTTP server on an ephemeral loopback port: no test reaches a real
network. Everything runs under the `tests/testenv` temporary HOME.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.testclient import TestClient

from arena.core.models import Call, Tokens
from arena.gateway.lanes import KeyLane
from arena.gateway.server import GatewayMiddleware
from arena.gateway.status import LaneSnapshot, snapshot_lane, status_routes
from arena.obs.logging import JsonLogFormatter, RateLimitFilter, bind_log_context
from arena.obs.metrics import Metrics
from arena.obs.otel import OtlpConfig, OtlpExporter, call_to_span

SECRET = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"


class FakeCollector:
    """An OTLP/HTTP collector that records posts, optionally slowly or failing."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.release = threading.Event()
        self.release.set()
        self.status = 200
        self._lock = threading.Lock()
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                body = json.loads(self.rfile.read(length))
                collector.release.wait(timeout=10)
                with collector._lock:
                    collector.requests.append(
                        (self.path, {k.lower(): v for k, v in self.headers.items()}, body)
                    )
                self.send_response(collector.status)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1/traces"

    def spans(self) -> list[dict[str, Any]]:
        with self._lock:
            posts = list(self.requests)
        return [
            span
            for _, _, body in posts
            for resource in body["resourceSpans"]
            for scope in resource["scopeSpans"]
            for span in scope["spans"]
        ]

    def close(self) -> None:
        self.release.set()
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def collector() -> Iterator[FakeCollector]:
    fake = FakeCollector()
    try:
        yield fake
    finally:
        fake.close()


def _call(index: int = 1, **update: Any) -> Call:
    call = Call(
        id=f"call-{index}",
        seq=index,
        run_id="run-1",
        trial_id="trial-1",
        protocol_in="anthropic",
        protocol_out="anthropic",
        provider="anthropic",
        account_id="acct-hash",
        model_asked="anthropic/claude-opus-5-5",
        model_served="claude-opus-5-5-20260101",
        status=200,
        tokens=Tokens.model_validate({"in": 120, "out": 30, "cache_read": 7}),
        cost_usd=0.0123,
        total_ms=1500,
    )
    return call.model_copy(update=update)


def _attrs(span: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in span["attributes"]:
        ((kind, value),) = item["value"].items()
        out[item["key"]] = int(value) if kind == "intValue" else value
    return out


def _exporter(collector: FakeCollector, **options: Any) -> OtlpExporter:
    config = OtlpConfig(endpoint=collector.endpoint, **options)
    return OtlpExporter(config, metrics=options.pop("metrics", None)).start()


# --- exporter -----------------------------------------------------------------------------


def test_collector_receives_genai_spans(collector: FakeCollector) -> None:
    exporter = OtlpExporter(
        OtlpConfig(endpoint=collector.endpoint, headers={"authorization": "Basic Zm9v"})
    ).start()
    assert exporter.submit_call(_call(), contestant="opus-plain")
    assert exporter.flush(timeout=5)
    exporter.close()

    path, headers, body = collector.requests[0]
    assert path == "/v1/traces"
    assert headers["authorization"] == "Basic Zm9v"
    assert headers["content-type"] == "application/json"
    resource = {i["key"]: i["value"] for i in body["resourceSpans"][0]["resource"]["attributes"]}
    assert resource["service.name"] == {"stringValue": "arena-gateway"}
    (span,) = collector.spans()
    attrs = _attrs(span)
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.provider.name"] == "anthropic"
    assert attrs["gen_ai.request.model"] == "anthropic/claude-opus-5-5"
    assert attrs["gen_ai.response.model"] == "claude-opus-5-5-20260101"
    assert attrs["gen_ai.usage.input_tokens"] == 120
    assert attrs["gen_ai.usage.output_tokens"] == 30
    assert attrs["arena.run_id"] == "run-1"
    assert attrs["arena.trial_id"] == "trial-1"
    assert attrs["arena.call_id"] == "call-1"
    assert attrs["arena.contestant"] == "opus-plain"
    assert len(span["traceId"]) == 32 and len(span["spanId"]) == 16
    assert int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]) == 1_500_000_000
    assert span["status"]["code"] == 1


def test_error_call_maps_to_error_span() -> None:
    span = call_to_span(_call(status=429, error_class="rate_limit"))
    assert span["status"]["code"] == 2
    assert _attrs(span)["error.type"] == "rate_limit"
    assert _attrs(span)["http.response.status_code"] == 429


def test_batches_share_one_post(collector: FakeCollector) -> None:
    exporter = OtlpExporter(
        OtlpConfig(endpoint=collector.endpoint, batch_size=2, flush_interval=0.05)
    ).start()
    for index in range(5):
        exporter.submit_call(_call(index))
    assert exporter.flush(timeout=5)
    exporter.close()
    assert len(collector.spans()) == 5
    assert all(
        sum(len(s["spans"]) for r in body["resourceSpans"] for s in r["scopeSpans"]) <= 2
        for _, _, body in collector.requests
    )


def test_full_queue_drops_and_counts_without_slowing_the_caller(
    collector: FakeCollector,
) -> None:
    collector.release.clear()  # the collector accepts the connection and never answers
    metrics = Metrics()
    exporter = OtlpExporter(
        OtlpConfig(endpoint=collector.endpoint, max_items=128, batch_size=16, export_timeout=30),
        metrics=metrics,
    ).start()
    started = time.perf_counter()
    for index in range(1000):
        exporter.submit_call(_call(index))
    elapsed = time.perf_counter() - started

    stats = exporter.stats()
    assert elapsed < 1.0, f"submit blocked the caller for {elapsed:.2f}s"
    assert stats.queued <= 128
    assert stats.dropped >= 1000 - 128 - 16
    assert stats.dropped + stats.queued + stats.exported + 16 >= 1000
    assert metrics.snapshot()["counters"]["otel_dropped"] == stats.dropped
    exporter.close(drain_timeout=0.2)


def test_queue_is_bounded_by_bytes(collector: FakeCollector) -> None:
    collector.release.clear()
    exporter = OtlpExporter(
        OtlpConfig(endpoint=collector.endpoint, max_items=10_000, max_bytes=6_000, batch_size=1)
    ).start()
    for index in range(200):
        exporter.submit_call(_call(index))
    stats = exporter.stats()
    assert stats.queued_bytes <= 6_000
    assert stats.dropped > 100
    exporter.close(drain_timeout=0.2)


def test_close_drains_for_a_bounded_time(collector: FakeCollector) -> None:
    collector.release.clear()
    exporter = OtlpExporter(OtlpConfig(endpoint=collector.endpoint, export_timeout=30)).start()
    for index in range(20):
        exporter.submit_call(_call(index))
    started = time.perf_counter()
    exporter.close(drain_timeout=0.3)
    assert time.perf_counter() - started < 2.0
    stats = exporter.stats()
    assert stats.queued == 0
    assert stats.dropped + stats.exported >= 1  # what could not drain is counted, not lost silently


def test_close_flushes_queued_spans_to_a_healthy_collector(collector: FakeCollector) -> None:
    exporter = OtlpExporter(
        OtlpConfig(endpoint=collector.endpoint, flush_interval=60, batch_size=100)
    ).start()
    for index in range(7):
        exporter.submit_call(_call(index))
    exporter.close(drain_timeout=3)
    assert len(collector.spans()) == 7
    assert exporter.stats().dropped == 0


def test_submit_after_close_is_dropped_and_counted(collector: FakeCollector) -> None:
    exporter = OtlpExporter(OtlpConfig(endpoint=collector.endpoint)).start()
    exporter.close()
    assert exporter.submit_call(_call()) is False
    assert exporter.stats().dropped == 1


def test_unreachable_or_failing_collector_never_raises(collector: FakeCollector) -> None:
    collector.status = 500
    exporter = OtlpExporter(OtlpConfig(endpoint=collector.endpoint)).start()
    exporter.submit_call(_call())
    assert exporter.flush(timeout=5)
    assert exporter.stats().failed == 1
    exporter.close()

    dead = OtlpExporter(OtlpConfig(endpoint="http://127.0.0.1:9/v1/traces")).start()
    dead.submit_call(_call())
    assert dead.flush(timeout=5)
    assert dead.stats().failed == 1
    dead.close()


def test_unmappable_call_does_not_break_the_request_path(collector: FakeCollector) -> None:
    exporter = OtlpExporter(OtlpConfig(endpoint=collector.endpoint)).start()
    assert exporter.submit_call(_call(), request_body=object()) is True  # body is not JSON
    assert exporter.submit_call("not a call") is False  # type: ignore[arg-type]
    exporter.close()
    assert exporter.stats().errors == 1


@pytest.mark.parametrize("endpoint", ["file:///etc/passwd", "ftp://host/x", "not a url", ""])
def test_config_rejects_non_http_endpoints(endpoint: str) -> None:
    with pytest.raises(ValueError):
        OtlpConfig(endpoint=endpoint)


def test_headers_are_not_in_the_config_repr() -> None:
    config = OtlpConfig(endpoint="http://127.0.0.1:1/v1/traces", headers={"authorization": SECRET})
    assert SECRET not in repr(config)


def test_bodies_are_off_by_default_and_redacted_when_on(collector: FakeCollector) -> None:
    request = {"model": "m", "api_key": SECRET, "messages": [{"role": "user", "content": SECRET}]}
    response = {"content": f"key is {SECRET}"}

    off = OtlpExporter(OtlpConfig(endpoint=collector.endpoint)).start()
    off.submit_call(_call(1), request_body=request, response_body=response)
    assert off.flush(timeout=5)
    off.close()
    assert "arena.request_body" not in _attrs(collector.spans()[0])

    on = OtlpExporter(OtlpConfig(endpoint=collector.endpoint, include_bodies=True)).start()
    on.submit_call(_call(2), request_body=request, response_body=response)
    assert on.flush(timeout=5)
    on.close()
    sent = json.dumps(collector.spans()[1])
    assert "arena.request_body" in sent
    assert SECRET not in sent


def test_attributes_never_carry_a_secret(collector: FakeCollector) -> None:
    span = call_to_span(_call(model_asked=f"anthropic/{SECRET}"), contestant=SECRET)
    assert SECRET not in json.dumps(span)


# --- metrics ------------------------------------------------------------------------------


def test_metrics_counters_gauges_and_percentiles() -> None:
    metrics = Metrics()
    metrics.inc("rests", 1, **{"class": "rate_limit"})
    metrics.inc("rests", 2, **{"class": "rate_limit"})
    metrics.set_gauge("in_flight", 3, lane="k1")
    for value in range(1, 101):
        metrics.observe("latency_ms", float(value), provider="anthropic")
    snap = metrics.snapshot()
    assert snap["counters"]["rests{class=rate_limit}"] == 3
    assert snap["gauges"]["in_flight{lane=k1}"] == 3
    hist = snap["histograms"]["latency_ms{provider=anthropic}"]
    assert hist["count"] == 100 and hist["p50"] == 50 and hist["p95"] == 95 and hist["max"] == 100


def test_metrics_series_are_bounded() -> None:
    metrics = Metrics(max_series=10)
    for index in range(50):
        metrics.inc("calls", model=f"m{index}")
    snap = metrics.snapshot()
    assert len(snap["counters"]) <= 11  # ten series plus the overflow counter
    assert snap["counters"]["metrics_series_dropped"] == 40


def test_metrics_are_thread_safe() -> None:
    metrics = Metrics()

    def work() -> None:
        for _ in range(1000):
            metrics.inc("n")

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert metrics.snapshot()["counters"]["n"] == 8000


# --- structured logs ----------------------------------------------------------------------


def _logger(stream: io.StringIO, *, clock: Any = time.monotonic) -> logging.Logger:
    logger = logging.Logger(f"arena.test.{id(stream)}")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(RateLimitFilter(per_minute=60, clock=clock))
    logger.addHandler(handler)
    return logger


def test_log_lines_are_json_with_ids_and_no_secrets() -> None:
    stream = io.StringIO()
    logger = _logger(stream)
    logger.info("plain line before binding")
    with bind_log_context(run_id="run-1", trial_id="trial-1", call_id="call-1"):
        logger.warning("upstream said %s", f"bad key {SECRET}", extra={"api_key": SECRET, "n": 2})
    first, second = (json.loads(line) for line in stream.getvalue().splitlines())
    assert (first["run_id"], first["trial_id"], first["call_id"]) == (None, None, None)
    assert (second["run_id"], second["trial_id"], second["call_id"]) == (
        "run-1",
        "trial-1",
        "call-1",
    )
    assert second["level"] == "warning" and second["n"] == 2
    assert SECRET not in stream.getvalue()


def test_repeated_errors_are_rate_limited_per_source_with_a_drop_count() -> None:
    now = [0.0]
    stream = io.StringIO()
    logger = _logger(stream, clock=lambda: now[0])
    for _ in range(100):
        logger.error("boom")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 60
    other_source = len(stream.getvalue().splitlines())
    logger.error("different source")  # a different call site is not throttled by the first
    assert len(stream.getvalue().splitlines()) == other_source + 1
    now[0] = 61.0
    for _ in range(2):
        logger.error("boom")
    resumed = [json.loads(line) for line in stream.getvalue().splitlines()][-2:]
    assert resumed[0]["suppressed"] == 40
    assert "suppressed" not in resumed[1]


# --- status endpoints ---------------------------------------------------------------------


def _status_client(
    *, lanes: Any = None, exporter: OtlpExporter | None = None, metrics: Metrics | None = None
) -> TestClient:
    routes = status_routes(
        lanes=lanes or (lambda: {}),
        metrics=metrics or Metrics(),
        exporter=exporter,
    )
    app = Starlette(routes=routes, middleware=[Middleware(GatewayMiddleware)])
    return TestClient(app)


OPS = {"authorization": "Bearer arena-ops"}


def test_status_endpoints_respond_for_ops() -> None:
    metrics = Metrics()
    metrics.inc("calls")
    lane = KeyLane(concurrency=2)
    client = _status_client(lanes=lambda: {"anthropic/key1": snapshot_lane(lane)}, metrics=metrics)

    health = client.get("/arena/health", headers=OPS)
    assert health.status_code == 200 and health.json()["status"] == "ok"

    lanes = client.get("/arena/lanes", headers=OPS)
    assert lanes.status_code == 200
    assert lanes.json()["lanes"]["anthropic/key1"] == {
        "in_flight": 0,
        "queued": 0,
        "concurrency": 2,
        "resting_until": None,
        "rest_class": None,
    }

    stats = client.get("/arena/stats", headers=OPS)
    assert stats.status_code == 200
    assert stats.json()["metrics"]["counters"]["calls"] == 1
    assert stats.json()["otel"] == {"enabled": False}


def test_status_endpoints_require_an_ops_token() -> None:
    client = _status_client()
    for path in ("/arena/health", "/arena/lanes", "/arena/stats"):
        assert client.get(path).status_code == 401
        assert (
            client.get(path, headers={"authorization": "Bearer arena-trial-1"}).status_code == 403
        )


def test_status_without_the_gateway_middleware_fails_closed() -> None:
    app = Starlette(routes=status_routes(lanes=lambda: {}, metrics=Metrics()))
    assert TestClient(app).get("/arena/health").status_code == 401


def test_unreadable_lanes_are_an_error_not_an_empty_list() -> None:
    def broken() -> dict[str, LaneSnapshot]:
        raise RuntimeError("lane pool unavailable")

    response = _status_client(lanes=broken).get("/arena/lanes", headers=OPS)
    assert response.status_code == 500
    assert response.json()["error"]["type"] == "lanes_unavailable"


def test_stats_report_otel_counters(collector: FakeCollector) -> None:
    collector.release.clear()
    exporter = OtlpExporter(
        OtlpConfig(endpoint=collector.endpoint, max_items=4, batch_size=1, export_timeout=30)
    ).start()
    for index in range(40):
        exporter.submit_call(_call(index))
    otel = _status_client(exporter=exporter).get("/arena/stats", headers=OPS).json()["otel"]
    assert otel["enabled"] is True
    assert otel["otel_dropped"] >= 30
    assert otel["queued"] <= 4
    assert SECRET not in json.dumps(otel)
    exporter.close(drain_timeout=0.2)
