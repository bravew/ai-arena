"""Append-only per-run event log with in-memory long-poll readers."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from arena.core.bundle_contract import validate_event
from arena.core.models import RunEvent

_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class EventLog:
    """Persist events as JSONL and serve events after a per-run sequence cursor."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._events: dict[str, list[RunEvent]] = {}
        self._conditions: dict[str, asyncio.Condition] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._writer_lock_path = Path(tempfile.gettempdir()) / (
            f"arena-events-{hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()}.lock"
        )
        self._writer_lock_fd: int | None = None
        self._acquire_writer_lock()

    async def append(self, event: RunEvent) -> RunEvent:
        self._validate_run_id(event.run_id)
        self._acquire_writer_lock()
        lock = self._locks.setdefault(event.run_id, asyncio.Lock())
        async with lock:
            events = self._events.get(event.run_id)
            if events is None:
                events = self._load(event.run_id)
                self._events[event.run_id] = events
            assigned = event.model_copy(update={"seq": events[-1].seq + 1 if events else 1})
            payload = assigned.model_dump(mode="json")
            payload["event_version"] = 1
            validate_event(payload)
            path = self._events_path(event.run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
                stream.flush()
            events.append(assigned)
            condition = self._conditions.setdefault(event.run_id, asyncio.Condition())
            async with condition:
                condition.notify_all()
            return assigned

    async def read_after(self, run_id: str, after: int, wait: float = 0) -> list[RunEvent]:
        self._validate_run_id(run_id)
        if after < 0:
            raise ValueError("after must be non-negative")
        if not 0 <= wait <= 25:
            raise ValueError("wait must be between 0 and 25 seconds")
        events = self._events.get(run_id)
        if events is None:
            events = self._load(run_id)
            self._events[run_id] = events
        found = [event for event in events if event.seq > after]
        if found or wait == 0:
            return found
        condition = self._conditions.setdefault(run_id, asyncio.Condition())
        try:
            async with condition:
                await asyncio.wait_for(
                    condition.wait_for(
                        lambda: any(event.seq > after for event in self._events[run_id])
                    ),
                    timeout=wait,
                )
        except TimeoutError:
            return []
        return [event for event in self._events[run_id] if event.seq > after]

    def _acquire_writer_lock(self) -> None:
        if self._writer_lock_fd is not None:
            return
        lock_fd = os.open(self._writer_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(lock_fd)
            raise RuntimeError(f"event log root already has a writer: {self.root}") from exc
        self._writer_lock_fd = lock_fd

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not _RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
            raise ValueError(f"invalid run id: {run_id!r}")

    def _events_path(self, run_id: str) -> Path:
        self._validate_run_id(run_id)
        root = self.root.resolve()
        path = root / run_id / "events.jsonl"
        if root not in path.resolve().parents:
            raise ValueError(f"event path escapes log root for run id: {run_id!r}")
        return path

    def _load(self, run_id: str) -> list[RunEvent]:
        path = self._events_path(run_id)
        if not path.exists():
            return []
        events: list[RunEvent] = []
        previous = 0
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                payload = json.loads(line)
                validate_event(payload)
                payload.pop("event_version", None)
                event = RunEvent.model_validate(payload)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid event in {path} line {line_number}: {exc}") from exc
            if event.run_id != run_id or event.seq != previous + 1:
                raise ValueError(f"non-monotonic or mismatched event in {path} line {line_number}")
            previous = event.seq
            events.append(event)
        return events
