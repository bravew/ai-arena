from __future__ import annotations

import json

from arena.agents.sessions import assemble_session
from arena.core.models import Call, FileTouched


def _call(call_id: str, *, trial_id: str = "trial-1") -> Call:
    return Call(
        id=call_id,
        seq=1,
        run_id="run-1",
        trial_id=trial_id,
        protocol_in="anthropic",
        protocol_out="anthropic",
        provider="test",
        account_id="account-hash",
        model_asked="test/model",
    )


def test_assembles_turns_calls_telemetry_and_files_from_scripted_agent() -> None:
    transcript = "\n".join(
        json.dumps(row)
        for row in [
            {"type": "session", "session_id": "native-1"},
            {"type": "turn", "turn_id": "one"},
            {"type": "tool_call", "name": "Read", "args": {"file": "a.py"}},
            {"type": "mcp_call", "server": "docs", "tool": "search", "duration_ms": 12},
            {"type": "skill_event", "kind": "listed", "skill": "review"},
            {"type": "skill_event", "kind": "loaded", "skill": "review"},
            {"type": "skill_event", "kind": "invoked", "skill": "review"},
            {"type": "turn", "turn_id": "two"},
            {"type": "tool_call", "name": "Edit", "args": {"file": "a.py"}},
        ]
    )
    result = assemble_session(
        transcript,
        trial_id="trial-1",
        agent="scripted",
        calls=[_call("c1", trial_id="trial-1"), _call("other", trial_id="trial-2"), _call("c2")],
        filesystem_diff=[FileTouched(path="a.py", added=3, removed=1)],
        kit_hash="sha256:kit",
    )

    session, events = result
    assert len(session.turns) == 2
    assert [turn.call_ids for turn in session.turns] == [["c1"], ["c2"]]
    assert [turn.unmetered for turn in session.turns] == [False, False]
    assert [call.name for call in session.turns[0].tool_calls] == ["Read"]
    assert session.turns[0].mcp_calls[0].server == "docs"
    assert [event.kind for event in session.turns[0].skill_events] == [
        "listed",
        "loaded",
        "invoked",
    ]
    assert session.turns[0].skill_events[0].kit_hash == "sha256:kit"
    assert session.turns[1].files == [FileTouched(path="a.py", added=3, removed=1)]
    assert [event.kind for event in events] == [
        "session_turn",
        "skill_event",
        "skill_event",
        "skill_event",
        "session_turn",
    ]


def test_native_turn_without_gateway_call_is_unmetered() -> None:
    result = assemble_session(
        json.dumps({"type": "turn", "turn_id": "one"}),
        trial_id="trial-1",
        agent="scripted",
        calls=[],
    )

    session, events = result
    assert len(session.turns) == 1
    assert session.turns[0].unmetered is True
    assert session.status == "complete"
    assert events[0].kind == "session_turn"


def test_unparseable_transcript_returns_partial_with_gateway_data() -> None:
    result = assemble_session(
        '{"type":"turn"}\nnot-json',
        trial_id="trial-1",
        agent="scripted",
        calls=[_call("c1")],
    )

    session, events = result
    assert session.status == "partial"
    assert len(session.turns) == 1
    assert session.turns[0].call_ids == ["c1"]
    assert events[0].kind == "session_turn"
