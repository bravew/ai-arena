"""Structured JSON logs with context propagation and per-source error suppression."""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from arena.gateway.redact import scrub

_CONTEXT: contextvars.ContextVar[dict[str, str | None] | None] = contextvars.ContextVar(
    "arena_log_context", default=None
)
_CONTEXT_FIELDS = ("run_id", "trial_id", "call_id")
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
    "exc_text",
    "stack_info",
}


@contextmanager
def bind_log_context(
    *, run_id: str | None = None, trial_id: str | None = None, call_id: str | None = None
) -> Generator[None, None, None]:
    """Bind identifiers for logs emitted in this context, restoring prior values on exit."""
    current = dict(_CONTEXT.get() or {})
    current.update({"run_id": run_id, "trial_id": trial_id, "call_id": call_id})
    token = _CONTEXT.set(current)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


class JsonLogFormatter(logging.Formatter):
    """Emit a single JSON object per line, scrubbing every string field."""

    def format(self, record: logging.LogRecord) -> str:
        result: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": scrub(record.name),
            "message": scrub(record.getMessage()),
        }
        context = _CONTEXT.get() or {}
        for key in _CONTEXT_FIELDS:
            value = context.get(key)
            result[key] = scrub(value) if value is not None else None
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_") or key in result or key in _CONTEXT_FIELDS:
                continue
            if (
                "key" in key.lower()
                or "token" in key.lower()
                or "secret" in key.lower()
                or "password" in key.lower()
            ):
                result[key] = "[REDACTED]"
            elif isinstance(value, str):
                result[key] = scrub(value)
            elif value is None or isinstance(value, (bool, int, float)):
                result[key] = value
        if record.exc_info:
            result["exception"] = scrub(self.formatException(record.exc_info))
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


class RateLimitFilter(logging.Filter):
    """Allow up to `per_minute` ERROR records per logger and message signature per minute."""

    def __init__(self, *, per_minute: int = 60, clock: Any = time.monotonic) -> None:
        super().__init__()
        if per_minute < 1:
            raise ValueError("per_minute must be positive")
        self._limit = per_minute
        self._clock = clock
        self._lock = threading.Lock()
        self._window: dict[str, tuple[float, int, int]] = defaultdict(lambda: (0.0, 0, 0))

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR:
            return True
        source = f"{record.name}:{record.funcName}:{record.msg}"
        now = self._clock()
        with self._lock:
            started, sent, suppressed = self._window[source]
            if now - started >= 60 or now < started:
                record.suppressed = suppressed
                started, sent, suppressed = now, 0, 0
            if sent < self._limit:
                self._window[source] = (started, sent + 1, suppressed)
                return True
            self._window[source] = (started, sent, suppressed + 1)
            return False
