from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from arena.agents.aider import (
    CONVENTIONS,
    ENV_FILE,
    IMAGE,
    TRANSCRIPT,
    VERSION,
    AiderAdapter,
)
from arena.agents.base import GatewayEndpoint, NativeTranscript
from arena.core.modelref import ModelRef
from arena.core.models import Task
from arena.kits.loading import load_kit

TRANSCRIPTS = Path(__file__).parent / "transcripts" / "aider"
GATEWAY = GatewayEndpoint(url="http://gateway:7400", token="arena-trial-34")


class FakeBox:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.commands: list[tuple[str, ...]] = []

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content

    def read_text(self, path: str) -> str:
        return self.files[path]

    def execute(self, command: Sequence[str]) -> str:
        self.commands.append(tuple(command))
        if command == ("aider", "--version"):
            return f"aider {VERSION}\n"
        if command[:2] == ("sh", "-lc"):
            return self.files.get(TRANSCRIPT, "")
        raise ValueError(f"unexpected command {command}")


def test_aider_is_pinned_and_uses_chat_protocol() -> None:
    adapter = AiderAdapter()
    box = FakeBox()

    assert adapter.version(box) == f"aider {VERSION}"
    assert adapter.id == "aider"
    assert adapter.protocol == "chat"
    assert adapter.image == IMAGE


def test_wire_uses_trial_gateway_credentials_and_check_validates_them() -> None:
    adapter = AiderAdapter()
    box = FakeBox()
    adapter.wire(box, GATEWAY, ModelRef.parse("openai/gpt-x"))

    assert json.loads(box.files[ENV_FILE]) == {
        "OPENAI_API_BASE": "http://gateway:7400/v1",
        "OPENAI_API_KEY": "arena-trial-34",
    }
    assert adapter.check(box).ok

    box.files[ENV_FILE] = json.dumps(
        {"OPENAI_API_BASE": "https://api.openai.com/v1", "OPENAI_API_KEY": "key"}
    )
    assert not adapter.check(box).ok


def test_command_uses_openai_model_message_file_and_read_conventions(tmp_path: Path) -> None:
    adapter = AiderAdapter()
    box = FakeBox()
    adapter.wire(box, GATEWAY, ModelRef.parse("openai/gpt-x"))
    task = Task(id="task", version=1, kind="agentic-code", prompt_file=str(tmp_path / "prompt"))

    adapter.read_instructions = True
    command = adapter.command(task, {})

    assert command[:2] == ["sh", "-lc"]
    assert ENV_FILE in command[-1]
    assert "--model openai/gpt-x" in command[-1]
    assert "--message-file" in command[-1]
    assert "--chat-history-file" in command[-1]
    assert f"--read {CONVENTIONS}" in command[-1]
    assert "--no-auto-commits" in command[-1]


def test_collect_returns_native_history_and_missing_history_is_absent() -> None:
    adapter = AiderAdapter()
    box = FakeBox()
    content = (TRANSCRIPTS / "chat-history.md").read_text(encoding="utf-8")
    box.files[TRANSCRIPT] = content

    assert adapter.collect(box) == NativeTranscript(content=content)


def test_recorded_history_yields_turns_and_truncation_is_partial() -> None:
    adapter = AiderAdapter()
    recorded = (TRANSCRIPTS / "chat-history.md").read_text(encoding="utf-8")

    session = adapter.sessions(NativeTranscript(content=recorded), calls=[])[0]
    partial = adapter.sessions(
        NativeTranscript(content=(TRANSCRIPTS / "chat-history-truncated.md").read_text()),
        calls=[],
    )[0]

    assert session.status == "complete"
    assert len(session.turns) == 2
    assert session.agent == "aider"
    assert partial.status == "partial"


def test_sessions_accepts_an_empty_gateway_ledger() -> None:
    session = AiderAdapter().sessions(NativeTranscript(content="#### request\n\nresponse\n"), [])[0]

    assert session.status == "complete"
    assert session.turns


def test_kit_installer_refuses_aider_skills_and_mcp_but_reads_instructions() -> None:
    adapter = AiderAdapter(kit_root=Path(__file__).resolve().parents[2] / "kits" / "fixture-basic")
    box = FakeBox()
    adapter.wire(box, GATEWAY, ModelRef.parse("openai/gpt-x"))
    root = adapter.kit_root
    assert root is not None
    kit = load_kit(root / "kit.yaml")

    result = adapter.install_kit(box, kit)

    assert result.written == (CONVENTIONS,)
    assert box.files[CONVENTIONS] == (root / "instructions.md").read_text()
    assert len(result.refused) == 2
    assert any("does not take skills" in refusal for refusal in result.refused)
    assert any("does not take MCP servers" in refusal for refusal in result.refused)
    assert adapter.kit_unapplied is False


def test_entirely_refused_aider_kit_is_marked_unapplied(tmp_path: Path) -> None:
    root = tmp_path / "kit"
    root.mkdir()
    (root / "skill" / "s").mkdir(parents=True)
    (root / "skill" / "s" / "SKILL.md").write_text("skill")
    (root / "kit.yaml").write_text("id: k\nversion: 1\nskills: [{path: skill/s}]\n")
    kit = load_kit(root / "kit.yaml")
    adapter = AiderAdapter(kit_root=root)
    adapter.wire(FakeBox(), GATEWAY, ModelRef.parse("openai/gpt-x"))

    result = adapter.install_kit(FakeBox(), kit)

    assert result.written == () and result.refused
    assert adapter.kit_unapplied is True


def test_version_mismatch_is_rejected() -> None:
    class WrongVersionBox(FakeBox):
        def execute(self, command: Sequence[str]) -> str:
            return "aider 0.1.0"

    with pytest.raises(ValueError, match="expected Aider"):
        AiderAdapter().version(WrongVersionBox())
