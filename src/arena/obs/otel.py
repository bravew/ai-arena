"""Opt-in, bounded, asynchronous OTLP/HTTP span export for gateway Calls.

The request thread only serializes a small span and tries a non-blocking queue insert. Network
I/O, retries and batching belong to the worker. A full item or byte budget drops and counts spans.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from arena.core.models import Call
from arena.gateway.redact import scrub, scrub_json
from arena.obs.metrics import Metrics

logger = logging.getLogger("arena.obs.otel")


def _safe(value: str | None) -> str | None:
    return scrub(value) if value is not None else None


def _string(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _int(key: str, value: int) -> dict[str, Any]:
    return {"key": key, "value": {"intValue": str(value)}}


def _attribute(key: str, value: Any) -> dict[str, Any] | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return _int(key, value)
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return _string(key, _safe(str(value)) or "")


def _body(value: Any) -> str | None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        clean = scrub_json(encoded).decode("utf-8")
        return clean[:16_384]
    except (TypeError, ValueError, UnicodeError):
        return None


def call_to_span(
    call: Call,
    *,
    contestant: str | None = None,
    request_body: Any = None,
    response_body: Any = None,
    include_bodies: bool = False,
) -> dict[str, Any]:
    """Map a Calls ledger row to OTLP JSON using stable GenAI and arena attributes."""
    seed = f"{call.run_id}:{call.id}".encode()
    trace_id = hashlib.sha256(f"trace:{call.run_id}".encode()).hexdigest()[:32]
    span_id = hashlib.sha256(seed).hexdigest()[:16]
    start_ns = time.time_ns() - max(0, call.total_ms) * 1_000_000
    attrs: list[dict[str, Any]] = [
        _string("gen_ai.operation.name", "chat"),
        _string("gen_ai.provider.name", _safe(call.provider) or "unknown"),
        _string("gen_ai.request.model", _safe(call.model_asked) or "unknown"),
        _int("gen_ai.usage.input_tokens", call.tokens.in_),
        _int("gen_ai.usage.output_tokens", call.tokens.out),
        _string("arena.run_id", _safe(call.run_id) or ""),
        _string("arena.call_id", _safe(call.id) or ""),
        _int("http.response.status_code", call.status or 0),
    ]
    for key, value in (
        ("gen_ai.response.model", call.model_served),
        ("gen_ai.usage.cache_read_tokens", call.tokens.cache_read),
        ("gen_ai.usage.cache_write_tokens", call.tokens.cache_write),
        ("gen_ai.usage.reasoning_tokens", call.tokens.reasoning),
        ("arena.trial_id", call.trial_id),
        ("arena.contestant", contestant),
        ("arena.purpose", call.purpose),
        ("arena.error_class", call.error_class),
        ("arena.cost_usd", call.cost_usd),
        ("error.type", call.error_class if call.status is None or call.status >= 400 else None),
    ):
        item = _attribute(key, value)
        if item is not None:
            attrs.append(item)
    if include_bodies:
        for name, body in (
            ("arena.request_body", request_body),
            ("arena.response_body", response_body),
        ):
            encoded = _body(body)
            if encoded is not None:
                attrs.append(_string(name, encoded))
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "gen_ai.chat",
        "kind": 3,
        "startTimeUnixNano": str(max(0, start_ns)),
        "endTimeUnixNano": str(max(0, start_ns + max(0, call.total_ms) * 1_000_000)),
        "attributes": attrs,
        "status": {"code": 2 if call.error_class is not None or (call.status or 0) >= 400 else 1},
    }


@dataclass(frozen=True, slots=True)
class OtlpConfig:
    """OTLP/HTTP exporter settings. An empty endpoint disables nothing; instantiate only opt-in."""

    endpoint: str
    headers: dict[str, str] = field(default_factory=dict[str, str], repr=False)
    service_name: str = "arena-gateway"
    max_items: int = 128
    max_bytes: int = 1_048_576
    batch_size: int = 32
    flush_interval: float = 0.25
    export_timeout: float = 2.0
    include_bodies: bool = False

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OTLP endpoint must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("OTLP endpoint must not contain credentials, query, or fragment")
        if not self.service_name or any(ord(ch) < 32 for ch in self.service_name):
            raise ValueError("service_name must be non-empty and contain no controls")
        if self.max_items < 1 or self.max_bytes < 1 or self.batch_size < 1:
            raise ValueError("queue and batch limits must be positive")
        if not math.isfinite(self.flush_interval) or self.flush_interval <= 0:
            raise ValueError("flush_interval must be positive and finite")
        if not math.isfinite(self.export_timeout) or self.export_timeout <= 0:
            raise ValueError("export_timeout must be positive and finite")
        for key, value in self.headers.items():
            if (
                not key
                or any(not (ch.isalnum() or ch in "!#$%&'*+-.^_`|~") for ch in key)
                or any(ch in "\r\n" or ord(ch) < 32 or ord(ch) > 126 for ch in value)
            ):
                raise ValueError("OTLP headers must be printable and non-empty")


@dataclass(frozen=True, slots=True)
class ExporterStats:
    queued: int
    queued_bytes: int
    dropped: int
    exported: int
    failed: int
    errors: int


class OtlpExporter:
    """A bounded queue serviced by one worker thread. `submit_call` never performs I/O."""

    def __init__(self, config: OtlpConfig, *, metrics: Metrics | None = None) -> None:
        self.config = config
        self.metrics = metrics or Metrics()
        self._condition = threading.Condition()
        self._queue: deque[tuple[dict[str, Any], int]] = deque()
        self._queued_bytes = 0
        self._dropped = self._exported = self._failed = self._errors = 0
        self._closed = False
        self._inflight = 0
        self._inflight_bytes = 0
        self._abandoned = 0
        self._drain_deadline: float | None = None
        self._worker: threading.Thread | None = None

    def start(self) -> OtlpExporter:
        with self._condition:
            if self._worker is None and not self._closed:
                self._worker = threading.Thread(target=self._run, name="arena-otlp", daemon=True)
                self._worker.start()
        return self

    def submit_call(
        self,
        call: Call,
        *,
        contestant: str | None = None,
        request_body: Any = None,
        response_body: Any = None,
    ) -> bool:
        """Queue a Call in bounded time; malformed input and overflow fail safe."""
        try:
            span = call_to_span(
                call,
                contestant=contestant,
                request_body=request_body,
                response_body=response_body,
                include_bodies=self.config.include_bodies,
            )
            item_bytes = len(json.dumps(span, separators=(",", ":")).encode())
        except Exception:
            self._error()
            return False
        with self._condition:
            if self._closed or item_bytes > self.config.max_bytes:
                self._drop_locked()
                return False
            if (
                len(self._queue) + self._inflight >= self.config.max_items
                or self._queued_bytes + self._inflight_bytes + item_bytes > self.config.max_bytes
            ):
                self._drop_locked()
                return False
            while (
                len(self._queue) >= self.config.max_items
                or self._queued_bytes + item_bytes > self.config.max_bytes
            ):
                if self._queue:
                    _, discarded_size = self._queue.popleft()
                    self._queued_bytes -= discarded_size
                    self._drop_locked()
                else:
                    self._drop_locked()
                    return False
            self._queue.append((span, item_bytes))
            self._queued_bytes += item_bytes
            self._condition.notify()
            return True

    def stats(self) -> ExporterStats:
        with self._condition:
            return ExporterStats(
                len(self._queue) + max(0, self._inflight - self._abandoned),
                self._queued_bytes,
                self._dropped + self._abandoned,
                self._exported,
                self._failed,
                self._errors,
            )

    def flush(self, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            self._condition.notify_all()
            while self._queue and time.monotonic() < deadline:
                self._condition.wait(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
            return not self._queue

    def close(self, *, drain_timeout: float = 3.0) -> None:
        with self._condition:
            if not self._closed:
                self._closed = True
                self._drain_deadline = time.monotonic() + max(0.0, drain_timeout)
                self._condition.notify_all()
            worker = self._worker
        if worker is not None:
            worker.join(timeout=max(0.0, drain_timeout) + 0.1)
            if worker.is_alive():
                with self._condition:
                    self._abandoned += self._inflight
        with self._condition:
            # Queue contents have now exceeded the explicit drain deadline.
            while self._queue:
                _, size = self._queue.popleft()
                self._queued_bytes -= size
                self._drop_locked()
            self._condition.notify_all()

    def _drop_locked(self) -> None:
        self._dropped += 1
        self.metrics.inc("otel_dropped")

    def _error(self) -> None:
        with self._condition:
            self._errors += 1
            self._drop_locked()

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._closed and self._drain_deadline is not None:
                    remaining = self._drain_deadline - time.monotonic()
                    if remaining <= 0:
                        while self._queue:
                            _, size = self._queue.popleft()
                            self._queued_bytes -= size
                            self._drop_locked()
                        self._condition.notify_all()
                        return
                if not self._queue and self._closed:
                    return
                if not self._queue:
                    self._condition.wait(timeout=self.config.flush_interval)
                    if not self._queue:
                        continue
                batch: list[dict[str, Any]] = []
                for _ in range(min(self.config.batch_size, len(self._queue))):
                    span, size = self._queue.popleft()
                    self._queued_bytes -= size
                    batch.append(span)
                self._inflight += len(batch)
            try:
                self._send(batch)
            except Exception as exc:
                with self._condition:
                    self._inflight -= len(batch)
                    self._inflight_bytes -= sum(
                        len(json.dumps(span, separators=(",", ":")).encode()) for span in batch
                    )
                    if (
                        self._closed
                        and self._drain_deadline is not None
                        and time.monotonic() >= self._drain_deadline
                    ):
                        self._dropped += len(batch)
                        self.metrics.inc("otel_dropped", len(batch))
                    else:
                        self._failed += len(batch)
                self.metrics.inc("otel_export_failed", len(batch))
                logger.warning("OTLP batch export failed: %s", exc)
            else:
                with self._condition:
                    self._inflight -= len(batch)
                    self._inflight_bytes -= sum(
                        len(json.dumps(span, separators=(",", ":")).encode()) for span in batch
                    )
                    self._exported += len(batch)
                self.metrics.inc("otel_exported", len(batch))
            finally:
                with self._condition:
                    self._condition.notify_all()

    def _send(self, spans: list[dict[str, Any]]) -> None:
        payload = {
            "resourceSpans": [
                {
                    "resource": {"attributes": [_string("service.name", self.config.service_name)]},
                    "scopeSpans": [{"scope": {"name": "arena"}, "spans": spans}],
                }
            ]
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ai-arena/0.1",
            **self.config.headers,
        }
        request = urllib.request.Request(
            self.config.endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.export_timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"collector returned HTTP {response.status}")
                response.read(4096)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"collector returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"collector connection failed: {exc.reason}") from None
