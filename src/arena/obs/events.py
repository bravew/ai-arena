"""Append-only per-run event log with in-memory long-poll readers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from arena.core.bundle_contract import validate_event
from arena.core.models import RunEvent


class EventLog:
    """Persist events as JSONL and serve events after a per-run sequence cursor."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._events: dict[str, list[RunEvent]] = {}
        self._conditions: dict[str, asyncio.Condition] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def append(self, event: RunEvent) -> RunEvent:
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
            run_dir = self.root / event.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            path = run_dir / "events.jsonl"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
                stream.flush()
            events.append(assigned)
            condition = self._conditions.setdefault(event.run_id, asyncio.Condition())
            async with condition:
                condition.notify_all()
            return assigned

    async def read_after(self, run_id: str, after: int, wait: float = 0) -> list[RunEvent]:
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

    def _load(self, run_id: str) -> list[RunEvent]:
        path = self.root / run_id / "events.jsonl"
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
