"""Lifecycle events for an `arena run`, merged into the run's `events.jsonl`."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arena.core.bundle_contract import validate_event
from arena.core.models import Contestant, RunEvent, RunEventKind


def contestant_snapshot(contestant: Contestant) -> dict[str, Any]:
    """The contestant config a bundle reader needs to rebuild it with the same identity."""
    return {"id": contestant.id, "label": contestant.label, **contestant.resolved_config()}


def events_path(home: Path, run_id: str) -> Path:
    return home / "runs" / run_id / "events.jsonl"


def _timestamp(raw: Any) -> datetime:
    return datetime.fromisoformat(str(raw))


class RunRecorder:
    """Collect run and trial events in memory, then write them once the run has ended.

    The gateway container appends `call_*` events to the same file while it runs, with its
    own sequence numbers. The arena process never writes beside it. `write` merges both
    sets by timestamp after the gateway has stopped and renumbers `seq`.
    """

    def __init__(
        self, run_id: str, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.run_id = run_id
        self._clock = clock
        self._events: list[RunEvent] = []

    @property
    def pending(self) -> bool:
        return bool(self._events)

    def emit(self, kind: RunEventKind, ref: str | None = None, **data: Any) -> None:
        self._events.append(
            RunEvent(seq=1, ts=self._clock(), run_id=self.run_id, kind=kind, ref=ref, data=data)
        )

    def write(self, home: Path) -> Path:
        path = events_path(home, self.run_id)
        existing: list[dict[str, Any]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    existing.append(json.loads(line))
        added = [{**event.model_dump(mode="json"), "event_version": 1} for event in self._events]
        merged = sorted(existing + added, key=lambda event: _timestamp(event["ts"]))
        lines: list[str] = []
        for seq, event in enumerate(merged, start=1):
            event["seq"] = seq
            validate_event(event)
            lines.append(json.dumps(event, separators=(",", ":")))
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".events-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write("\n".join(lines) + "\n")
            os.replace(temporary, path)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
        self._events.clear()
        return path
