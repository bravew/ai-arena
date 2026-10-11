"""Pi adapter wiring and mock transcript tests; these are not vendor-recorded bytes."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from arena.agents.base import GatewayEndpoint, NativeTranscript
from arena.agents.pi import (
    AGENT_DIR,
    MCP_CONFIG,
    MODELS_CONFIG,
    TRANSCRIPT,
    PiAdapter,
)
from arena.core.modelref import ModelRef
from arena.core.models import Kit, McpServer, SkillSource, Task
from arena.kits.hashing import hash_kit

FIXTURE = Path(__file__).parent / "transcripts/pi"
TOKEN = "arena-pi-test-trial"


class FakeBox:
    def __init__(self, version: str = "0.99.2") -> None:
        self.files: dict[str, str] = {}
        self.commands: list[tuple[str, ...]] = []
        self.installed_version = version

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content

    def read_text(self, path: str) -> str:
        return self.files[path]

    def execute(self, command: Sequence[str]) -> str:
        self.commands.append(tuple(command))
        return self.installed_version


def test_wiring_sets_pi_agent_dir_gateway_provider_and_trial_token() -> None:
    adapter = PiAdapter()
    box = FakeBox()
    endpoint = GatewayEndpoint("http://gateway:7400", TOKEN)
    adapter.wire(box, endpoint, ModelRef.parse("anthropic/model-a"))
    config = json.loads(box.files[MODELS_CONFIG])
    provider = config["providers"]["arena"]
    assert AGENT_DIR == "/home/agent/.pi/agent"
    assert provider["baseUrl"] == "http://gateway:7400/v1"
    assert provider["apiKey"] == TOKEN
    assert provider["models"][0]["id"] == "model-a"
    assert adapter.check(box).ok


def test_pi_version_before_native_mcp_is_refused_with_reason() -> None:
    adapter = PiAdapter()
    with pytest.raises(ValueError, match=r"Pi 0\.98\.9 is unsupported.*0\.99\.0.*native MCP"):
        adapter.version(FakeBox("0.98.9"))


def test_kit_writes_instructions_skills_and_gateway_routed_mcp(tmp_path: Path) -> None:
    root = tmp_path / "kit"
    (root / "skills" / "review").mkdir(parents=True)
    (root / "skills" / "review" / "SKILL.md").write_text("Review carefully.\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("Use the team conventions.\n", encoding="utf-8")
    kit = Kit(
        id="review-kit",
        version=1,
        hash="",
        instructions="AGENTS.md",
        skills=[SkillSource(path="skills/review")],
        mcp=[McpServer(name="docs", url="http://provider.invalid", headers={})],
    )
    kit = kit.model_copy(update={"hash": hash_kit(kit, root)})
    box = FakeBox()
    adapter = PiAdapter(kit_root=root)
    adapter.wire(box, GatewayEndpoint("http://gateway:7400", TOKEN), ModelRef.parse("mock/model"))
    result = adapter.install_kit(box, kit)
    assert result.refused == ()
    assert "/home/agent/AGENTS.md" in result.written
    assert "/home/agent/.pi/agent/skills/review/SKILL.md" in result.written
    mcp = json.loads(box.files[MCP_CONFIG])
    assert mcp["mcpServers"]["docs"] == {
        "url": "http://gateway:7400/mcp/docs",
        "headers": {"Authorization": f"Bearer {TOKEN}"},
    }


def test_kit_install_requires_explicit_resolved_root() -> None:
    adapter = PiAdapter()
    adapter.wire(
        FakeBox(),
        GatewayEndpoint("http://gateway:7400", TOKEN),
        ModelRef.parse("mock/model"),
    )
    with pytest.raises(ValueError, match="explicit resolved kit root"):
        adapter.install_kit(FakeBox(), Kit(id="empty", version=1, hash=""))


def test_command_uses_pinned_prompt_when_requested(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Do the task", encoding="utf-8")
    adapter = PiAdapter()
    adapter.model_id = "model-a"
    command = adapter.command(
        Task(id="t", version=1, kind="agentic-code", prompt_file="prompt.md"),
        {"scaffold_prompt": "pinned"},
    )
    shell = command[-1]
    assert "PI_CODING_AGENT_DIR=/home/agent/.pi/agent" in shell
    assert "--mode json" in shell
    assert "--system-prompt" in shell
    assert "You are a coding agent." in shell
    assert "/tmp/arena-pi-transcript.jsonl" in shell
    assert TRANSCRIPT in shell


def test_native_transcript_skill_events_and_truncation() -> None:
    adapter = PiAdapter()
    complete = adapter.sessions(
        NativeTranscript((FIXTURE / "complete.mock.jsonl").read_text(encoding="utf-8")), []
    )[0]
    assert complete.status == "complete"
    assert [event.kind for turn in complete.turns for event in turn.skill_events] == [
        "listed",
        "loaded",
        "invoked",
    ]
    partial = adapter.sessions(
        NativeTranscript((FIXTURE / "truncated.mock.jsonl").read_text(encoding="utf-8")), []
    )[0]
    assert partial.status == "partial"
    assert len(partial.turns) == 1
