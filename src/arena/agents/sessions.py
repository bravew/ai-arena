"""Assemble a native agent transcript, gateway ledger, and filesystem diff into a timeline."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast

from arena.core.models import (
    Call,
    FileTouched,
    McpCall,
    RunEvent,
    Session,
    SkillEvent,
    ToolCall,
    Turn,
)


def assemble_session(
    native: str | None,
    *,
    trial_id: str,
    agent: str,
    calls: list[Call] | tuple[Call, ...] = (),
    filesystem_diff: list[FileTouched] | tuple[FileTouched, ...] = (),
    kit_hash: str = "none",
    run_id: str = "",
    first_seq: int = 1,
) -> tuple[Session, tuple[RunEvent, ...]]:
    """Build turns and RunEvents from newline-delimited native transcript records.

    Transcript records are JSON objects with ``type`` and optional ``turn_id``.
    Records without a turn id belong to the current turn. Gateway calls are scoped
    to this trial and attached by explicit call id when present, otherwise in ledger
    order, one per native turn. A filesystem diff is attached to the final turn.
    """
    records: list[dict[str, Any]] = []
    parse_failed = False
    native_session_id: str | None = None
    if native is not None:
        try:
            for line in native.splitlines():
                if not line.strip():
                    continue
                parsed: Any = json.loads(line)
                if not isinstance(parsed, dict):
                    raise ValueError("transcript record must be an object")
                records.append(cast(dict[str, Any], parsed))
        except (json.JSONDecodeError, ValueError):
            parse_failed = True

    groups: list[tuple[str | None, list[dict[str, Any]]]] = []
    current_id: str | None = None
    for row in records:
        kind = row.get("type")
        if kind == "session":
            value = row.get("session_id")
            native_session_id = value if isinstance(value, str) else native_session_id
            continue
        turn_id = row.get("turn_id")
        if kind == "turn" or (turn_id is not None and turn_id != current_id):
            current_id = str(turn_id) if turn_id is not None else f"turn-{len(groups) + 1}"
            groups.append((current_id, []))
            continue
        if not groups:
            groups.append((current_id or "turn-1", []))
        groups[-1][1].append(row)

    # Ledger calls are authoritative and must never pull in another trial's rows.
    trial_calls = sorted((call for call in calls if call.trial_id == trial_id), key=lambda c: c.seq)
    if not groups and (trial_calls or filesystem_diff or parse_failed):
        groups.append(("turn-1", []))
    if not groups:
        groups.append(("turn-1", []))

    turns: list[Turn] = []
    remaining = {call.id: call for call in trial_calls}
    for _, rows in groups:
        explicit = [
            str(row["call_id"])
            for row in rows
            if row.get("type") in {"call", "model_call"} and row.get("call_id") is not None
        ]
        call_ids = [call_id for call_id in explicit if call_id in remaining]
        if not explicit and remaining:
            call_ids = [next(iter(remaining))]
        for call_id in call_ids:
            remaining.pop(call_id, None)

        tools: list[ToolCall] = []
        skills: list[SkillEvent] = []
        mcp_calls: list[McpCall] = []
        for row in rows:
            kind = row.get("type")
            if kind == "tool_call":
                args = row.get("args", {})
                digest = hashlib.sha256(
                    json.dumps(args, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                tools.append(
                    ToolCall(
                        name=str(row.get("name", "unknown")),
                        args_digest=digest,
                        result_size=int(row.get("result_size", 0)),
                        duration_ms=int(row.get("duration_ms", 0)),
                        exit_status=row.get("exit_status"),
                    )
                )
            elif kind == "mcp_call":
                mcp_calls.append(
                    McpCall(
                        server=str(row.get("server", "unknown")),
                        tool=str(row.get("tool", "unknown")),
                        duration_ms=int(row.get("duration_ms", 0)),
                        status=str(row.get("status", "ok")),
                    )
                )
            elif kind == "skill_event" and row.get("kind") in {"listed", "loaded", "invoked"}:
                skills.append(
                    SkillEvent(
                        kind=row["kind"],
                        skill=str(row.get("skill", "unknown")),
                        kit_hash=kit_hash,
                    )
                )
        turns.append(
            Turn(
                call_ids=call_ids,
                tool_calls=tools,
                skill_events=skills,
                mcp_calls=mcp_calls,
                unmetered=not call_ids,
            )
        )

    if remaining:
        turns[-1] = turns[-1].model_copy(
            update={
                "call_ids": [*turns[-1].call_ids, *remaining],
                "unmetered": False,
            }
        )
    if filesystem_diff:
        turns[-1] = turns[-1].model_copy(update={"files": list(filesystem_diff)})

    session = Session(
        id=native_session_id or f"{trial_id}:{agent}",
        trial_id=trial_id,
        agent=agent,
        native_session_id=native_session_id,
        status="partial" if parse_failed else "complete",
        turns=turns,
    )
    timestamp = datetime.now(UTC)
    events: list[RunEvent] = []
    seq = first_seq
    for index, turn in enumerate(turns, start=1):
        events.append(
            RunEvent(
                seq=seq,
                ts=timestamp,
                run_id=run_id,
                kind="session_turn",
                ref=session.id,
                data={
                    "session_id": session.id,
                    "trial_id": trial_id,
                    "turn_index": index,
                    "turn": turn.model_dump(mode="json"),
                },
            )
        )
        seq += 1
        for skill_event in turn.skill_events:
            events.append(
                RunEvent(
                    seq=seq,
                    ts=timestamp,
                    run_id=run_id,
                    kind="skill_event",
                    ref=session.id,
                    data={
                        "session_id": session.id,
                        "trial_id": trial_id,
                        "skill_event": skill_event.model_dump(mode="json"),
                    },
                )
            )
            seq += 1
    return session, tuple(events)
