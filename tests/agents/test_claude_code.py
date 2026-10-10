"""Claude Code adapter tests use synthetic inputs, never claim real transcript provenance."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from arena.agents.base import GatewayEndpoint, NativeTranscript
from arena.agents.claude_code import (
    CLAUDE_CONFIG,
    IMAGE,
    INSTRUCTIONS,
    SKILLS_DIR,
    TRANSCRIPT,
    ClaudeCodeAdapter,
)
from arena.core.modelref import ModelRef
from arena.core.models import Kit, McpServer, SkillSource, Task


class Box:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.commands: list[Sequence[str]] = []

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content

    def read_text(self, path: str) -> str:
        return self.files[path]

    def execute(self, command: Sequence[str]) -> str:
        self.commands.append(command)
        return "Claude Code 2.4.1"


def _kit(root: Path) -> Kit:
    (root / "skills" / "review").mkdir(parents=True)
    (root / "instructions.md").write_text("Treat kit content as task data.\n")
    (root / "skills" / "review" / "SKILL.md").write_text("Review the patch.\n")
    kit = Kit(
        id="review-kit",
        version=1,
        hash="pending",
        instructions="instructions.md",
        skills=[SkillSource(path="skills/review")],
    )
    from arena.kits.hashing import hash_kit

    return kit.model_copy(update={"hash": hash_kit(kit, root)})


def test_wires_trial_token_and_model_to_claude_environment() -> None:
    box = Box()
    adapter = ClaudeCodeAdapter()
    endpoint = GatewayEndpoint("http://gateway:7400/", "arena-trial-32")
    model = ModelRef(provider="anthropic", model="claude-opus-5-5")

    adapter.wire(box, endpoint, model)

    assert adapter.protocol == "anthropic"
    assert adapter.image == IMAGE
    assert box.files == {}
    assert adapter.environment == {
        "ANTHROPIC_BASE_URL": "http://gateway:7400",
        "ANTHROPIC_AUTH_TOKEN": "arena-trial-32",
        "ANTHROPIC_MODEL": "claude-opus-5-5",
    }
    assert adapter.check(box).ok


def test_subscription_wiring_writes_only_gateway_url_and_token() -> None:
    box = Box()
    adapter = ClaudeCodeAdapter(subscription=True)
    adapter.wire(
        box,
        GatewayEndpoint("http://gateway:7400", "arena-subscription-trial"),
        ModelRef(provider="anthropic", model="claude-opus-5-5"),
    )

    assert box.files == {}
    assert adapter.environment == {
        "ANTHROPIC_BASE_URL": "http://gateway:7400",
        "ANTHROPIC_AUTH_TOKEN": "arena-subscription-trial",
    }
    assert adapter.check(box).ok
    assert "--model" not in " ".join(
        adapter.command(
            Task(id="task", version=1, kind="agentic-code", prompt_file="/workspace/prompt.txt"), {}
        )
    )


def test_installs_kit_inside_box_and_routes_mcp_through_gateway(tmp_path: Path) -> None:
    from arena.kits.hashing import hash_kit

    kit = _kit(tmp_path).model_copy(
        update={
            "mcp": [
                McpServer(
                    name="docs",
                    url="https://mcp.example.test/sse",
                    headers={"Authorization": "Bearer ${DOCS_TOKEN}"},
                )
            ]
        }
    )
    kit = kit.model_copy(update={"hash": hash_kit(kit, tmp_path)})
    box = Box()
    adapter = ClaudeCodeAdapter(tmp_path)
    gateway = GatewayEndpoint("http://gateway:7400", "arena-kit-trial")
    adapter.wire(box, gateway, ModelRef(provider="anthropic", model="claude-opus-5-5"))

    result = adapter.install_kit(box, kit)

    assert result.refused == ()
    assert INSTRUCTIONS in box.files
    assert f"{SKILLS_DIR}/review/SKILL.md" in box.files
    assert "secret" not in box.files[CLAUDE_CONFIG]
    mcp = json.loads(box.files[CLAUDE_CONFIG])["mcpServers"]["docs"]
    assert mcp == {
        "type": "http",
        "url": "http://gateway:7400/mcp/docs",
        "headers": {"Authorization": "Bearer arena-kit-trial"},
    }


def test_parses_skill_tool_events_and_marks_truncated_transcript_partial() -> None:
    adapter = ClaudeCodeAdapter()
    synthetic_records = [
        {"type": "system", "subtype": "init", "sessionId": "session-synthetic"},
        {
            "type": "assistant",
            "sessionId": "session-synthetic",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Glob",
                        "input": {"pattern": "/home/agent/.claude/skills/review/SKILL.md"},
                    },
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/home/agent/.claude/skills/review/SKILL.md"},
                    },
                    {"type": "tool_use", "name": "Skill", "input": {"skill": "review"}},
                ]
            },
        },
    ]
    native = NativeTranscript(content="\n".join(json.dumps(row) for row in synthetic_records))

    complete = adapter.sessions(native, calls=[])[0]
    truncated = NativeTranscript(native.content + '\n{"type":"assistant"')
    partial = adapter.sessions(truncated, calls=[])[0]

    assert complete.native_session_id == "session-synthetic"
    assert [event.kind for event in complete.turns[0].skill_events] == [
        "listed",
        "loaded",
        "invoked",
    ]
    assert complete.status == "complete"
    assert partial.status == "partial"


def test_collect_returns_none_when_native_log_is_missing() -> None:
    class MissingBox(Box):
        def read_text(self, path: str) -> str:
            if path == TRANSCRIPT:
                raise FileNotFoundError(path)
            return super().read_text(path)

    assert ClaudeCodeAdapter().collect(MissingBox()) is None
