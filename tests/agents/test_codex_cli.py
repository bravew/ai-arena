from __future__ import annotations

import json
import tomllib
from collections.abc import Sequence
from pathlib import Path

from arena.agents.base import GatewayEndpoint, NativeTranscript
from arena.agents.codex_cli import (
    CODEX_CONFIG,
    CODEX_TOKEN_ENV,
    CODEX_VERSION,
    CodexCliAdapter,
    ResolvedCodexKit,
)
from arena.core.modelref import ModelRef
from arena.core.models import Call, Kit, SkillSource, Task
from arena.kits.hashing import hash_kit


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
        if command == ("codex", "--version"):
            return f"codex-cli {CODEX_VERSION}\n"
        if command[0] == "sh":
            return ""
        return ""


def _call() -> Call:
    return Call(
        id="call-1",
        seq=1,
        run_id="run-1",
        trial_id="trial-1",
        protocol_in="responses",
        protocol_out="responses",
        provider="mock",
        account_id="mock",
        model_asked="openai/gpt-test",
    )


def test_codex_wiring_uses_responses_gateway_and_trial_token_env() -> None:
    adapter = CodexCliAdapter()
    box = FakeBox()

    adapter.wire(
        box,
        GatewayEndpoint("http://gateway:7400/v1", "arena-trial-1"),
        ModelRef.parse("openai/gpt-test"),
    )

    config = tomllib.loads(box.files[CODEX_CONFIG])
    assert config["model_provider"] == "arena"
    assert config["model"] == "gpt-test"
    assert config["model_providers"]["arena"] == {
        "name": "AI Arena gateway",
        "base_url": "http://gateway:7400/v1",
        "wire_api": "responses",
        "env_key": CODEX_TOKEN_ENV,
        "requires_openai_auth": False,
    }
    assert adapter.check(box).ok
    assert "arena-trial-1" not in box.files[CODEX_CONFIG]


def test_subscription_wiring_writes_only_gateway_url_and_token_configuration() -> None:
    adapter = CodexCliAdapter()
    box = FakeBox()

    adapter.wire(
        box,
        GatewayEndpoint("http://gateway:7400/v1", "arena-trial-1"),
        ModelRef.parse("openai/gpt-test"),
    )

    config = box.files[CODEX_CONFIG]
    assert "api_key" not in config
    assert "access_token" not in config
    assert "oauth" not in config
    assert f'env_key = "{CODEX_TOKEN_ENV}"' in config
    assert adapter.check(box) == adapter.check(box)


def test_codex_kit_installs_instructions_skills_and_mcp_without_credentials(tmp_path: Path) -> None:
    adapter = CodexCliAdapter()
    box = FakeBox()
    (tmp_path / "instructions.md").write_text("Use careful reasoning.", encoding="utf-8")
    skill = tmp_path / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Review the diff.", encoding="utf-8")
    (tmp_path / "skills" / "review" / "run.py").write_text("print('ok')", encoding="utf-8")
    manifest = Kit(
        id="test-kit",
        version=1,
        hash="pending",
        instructions="instructions.md",
        skills=[SkillSource(path="skills/review")],
    )
    kit = manifest.model_copy(update={"hash": hash_kit(manifest, tmp_path)})
    result = adapter.install_kit(
        box,
        ResolvedCodexKit(kit, tmp_path, GatewayEndpoint("http://gateway:7400", "arena-trial-1")),
    )

    assert box.files["/home/agent/.codex/AGENTS.md"] == "Use careful reasoning."
    assert box.files["/home/agent/.codex/skills/review/SKILL.md"] == "Review the diff."
    assert box.files["/home/agent/.codex/skills/review/run.py"] == "print('ok')"
    assert result.refused == ()


def test_codex_mcp_config_uses_gateway_path_and_token_env(tmp_path: Path) -> None:
    adapter = CodexCliAdapter()
    box = FakeBox()
    manifest_path = tmp_path / "kit.yaml"
    manifest_path.write_text(
        "id: mcp-kit\nversion: 1\ninstructions: null\nskills: []\n"
        "mcp:\n  - name: docs\n    url: https://example.invalid/mcp\n    headers:\n"
        "      Authorization: 'Bearer ${DOCS_TOKEN}'\n",
        encoding="utf-8",
    )
    from arena.kits.loading import load_kit

    kit = ResolvedCodexKit(
        load_kit(manifest_path), tmp_path, GatewayEndpoint("http://gateway:7400", "arena-trial-1")
    )
    result = adapter.install_kit(box, kit)

    assert CODEX_CONFIG in result.written
    assert "/mcp/docs" in box.files[CODEX_CONFIG]
    assert f'bearer_token_env_var = "{CODEX_TOKEN_ENV}"' in box.files[CODEX_CONFIG]
    assert "DOCS_TOKEN" not in box.files[CODEX_CONFIG]


def test_command_is_headless_and_uses_task_prompt_file(tmp_path: Path) -> None:
    task = Task(
        id="task-1", version=1, kind="agentic-code", prompt_file=str(tmp_path / "prompt.txt")
    )

    (tmp_path / "prompt.txt").write_text("Fix the bug", encoding="utf-8")
    command = CodexCliAdapter().command(task, {"approval_policy": "never"})

    assert command[:4] == ["codex", "exec", "--json", "--full-auto"]
    assert command[-1] == "Fix the bug"
    assert 'approval_policy="never"' in command


def test_real_cli_rollout_records_map_session_tool_and_skill_telemetry() -> None:
    # Codex's native rollout JSONL shape is represented from upstream CLI source fixtures.
    rows = [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": "native-1", "selected_capability_roots": ["review"]},
        },
        {
            "timestamp": "2026-01-01T00:00:01Z",
            "type": "turn_context",
            "payload": {"turn_id": "turn-1"},
        },
        {
            "timestamp": "2026-01-01T00:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "read_file",
                "arguments": '{"file_path":"/home/agent/.codex/skills/review/SKILL.md"}',
            },
        },
        {
            "timestamp": "2026-01-01T00:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "run_python",
                "arguments": '{"file_path":"/home/agent/.codex/skills/review/run.py"}',
            },
        },
    ]
    adapter = CodexCliAdapter()

    sessions = adapter.sessions(
        NativeTranscript("\n".join(json.dumps(row) for row in rows)), [_call()]
    )

    assert len(sessions) == 1
    session = sessions[0]
    assert session.native_session_id == "native-1"
    assert session.turns[0].call_ids == ["call-1"]
    assert [event.kind for event in session.turns[0].skill_events] == [
        "listed",
        "loaded",
        "invoked",
    ]


def test_truncated_native_rollout_is_partial() -> None:
    adapter = CodexCliAdapter()

    sessions = adapter.sessions(
        NativeTranscript(
            '{"type":"session_meta","payload":{"id":"native-1"}}\n{"type":"response_item"'
        ),
        [_call()],
    )

    assert sessions[0].status == "partial"
    assert sessions[0].turns[0].call_ids == ["call-1"]


def test_collect_returns_none_for_empty_session_directory() -> None:
    assert CodexCliAdapter().collect(FakeBox()) is None
