from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arena.core.models import RunEvent, RunEventKind
from arena.obs.events import EventLog


def _event(run_id: str, kind: RunEventKind = "run_started") -> RunEvent:
    return RunEvent(seq=0, ts=datetime.now(UTC), run_id=run_id, kind=kind)


def test_event_log_assigns_monotonic_seq_appends_jsonl_and_long_polls(tmp_path: Path) -> None:
    async def run() -> None:
        log = EventLog(tmp_path)
        first = await log.append(_event("r1"))
        assert first.seq == 1
        lines = (tmp_path / "r1" / "events.jsonl").read_text().splitlines()
        assert json.loads(lines[0])["seq"] == 1

        waiting = asyncio.create_task(log.read_after("r1", after=1, wait=1))
        await asyncio.sleep(0)
        second = await log.append(_event("r1", "trial_started"))
        assert second.seq == 2
        assert await waiting == [second]
        assert await log.read_after("r1", after=0, wait=0) == [first, second]

    asyncio.run(run())


def test_event_log_rejects_run_ids_that_escape_the_root(tmp_path: Path) -> None:
    async def run() -> None:
        log = EventLog(tmp_path)
        for run_id in ("../outside", "/tmp/outside", ".."):
            with pytest.raises(ValueError, match="invalid run id"):
                await log.append(_event(run_id))
            with pytest.raises(ValueError, match="invalid run id"):
                await log.read_after(run_id, after=0)
        assert not (tmp_path.parent / "outside" / "events.jsonl").exists()

    asyncio.run(run())


def test_event_log_recovers_sequence_from_existing_append_only_file(tmp_path: Path) -> None:
    async def run() -> None:
        run_dir = tmp_path / "r1"
        run_dir.mkdir()
        event = _event("r1").model_copy(update={"seq": 1})
        payload = event.model_dump(mode="json")
        payload["event_version"] = 1
        (run_dir / "events.jsonl").write_text(json.dumps(payload) + "\n")
        log = EventLog(tmp_path)
        next_event = await log.append(_event("r1", "trial_started"))
        assert next_event.seq == 2
        assert len((run_dir / "events.jsonl").read_text().splitlines()) == 2

    asyncio.run(run())
