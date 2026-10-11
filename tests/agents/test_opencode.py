"""Pure adapter tests and an opt-in pinned-container contract test."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest

from arena.agents.base import GatewayEndpoint, NativeTranscript
from arena.agents.opencode import CONFIG, SYSTEM_PROMPT, OpenCodeAdapter
from arena.core.modelref import ModelRef
from arena.core.models import Kit, McpServer, SkillSource, Task
from arena.kits.hashing import hash_kit
from arena.sandbox import DockerAgentBox, DockerSandbox, SandboxSpec

FIXTURE = Path(__file__).parent / "transcripts/opencode"
TOKEN = "arena-contract-trial"
CONTRACT_IMAGE = "arena-opencode-contract:1.2.18"


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
        return "1.2.18"


def _docker(*args: str) -> str:
    return subprocess.run(
        ("docker", *args), capture_output=True, text=True, check=True, timeout=300
    ).stdout.strip()


def test_wiring_config_uses_gateway_and_same_system_prompt_for_model_families() -> None:
    adapter = OpenCodeAdapter()
    box = FakeBox()
    endpoint = GatewayEndpoint("http://gateway:7400", TOKEN)
    adapter.wire(box, endpoint, ModelRef.parse("anthropic/family-a"))
    config_a = json.loads(box.files[CONFIG])
    adapter.wire(box, endpoint, ModelRef.parse("openai/family-b"))
    config_b = json.loads(box.files[CONFIG])
    assert (
        config_a["agent"]["arena"]["prompt"].encode()
        == config_b["agent"]["arena"]["prompt"].encode()
    )
    assert config_a["agent"]["arena"]["prompt"] == SYSTEM_PROMPT
    assert config_a["provider"]["arena"]["options"] == {
        "baseURL": "http://gateway:7400/v1",
        "apiKey": TOKEN,
    }
    assert adapter.check(box).ok


def test_kit_skills_are_staged_once_and_mcp_is_written_to_config(tmp_path: Path) -> None:
    root = tmp_path / "kit"
    skill_dir = root / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Review carefully.", encoding="utf-8")
    kit = Kit(
        id="review-kit",
        version=1,
        hash="",
        instructions=None,
        skills=[SkillSource(path="skills/review"), SkillSource(path="skills/review")],
        mcp=[McpServer(name="docs", url="http://provider.invalid", headers={})],
    )
    kit = kit.model_copy(update={"hash": hash_kit(kit, root)})
    box = FakeBox()
    endpoint = GatewayEndpoint("http://gateway:7400", TOKEN)
    adapter = OpenCodeAdapter(kit_root=root)
    adapter.wire(box, endpoint, ModelRef.parse("mock/model"))
    result = adapter.install_kit(box, kit)
    assert result.written.count("/home/agent/.claude/skills/review/SKILL.md") == 1
    assert "/home/agent/.config/opencode/opencode.json" in result.written
    config = json.loads(box.files[CONFIG])
    assert config["mcp"]["docs"] == {
        "type": "remote",
        "url": "http://gateway:7400/mcp/docs",
        "enabled": True,
        "headers": {"Authorization": f"Bearer {TOKEN}"},
    }


def test_kit_install_requires_explicit_resolved_kit_root(tmp_path: Path) -> None:
    adapter = OpenCodeAdapter()
    box = FakeBox()
    adapter.wire(box, GatewayEndpoint("http://gateway:7400", TOKEN), ModelRef.parse("mock/model"))
    with pytest.raises(ValueError, match="explicit resolved kit root"):
        adapter.install_kit(box, Kit(id="empty", version=1, hash=""))


def test_native_skill_events_and_truncated_jsonl() -> None:
    adapter = OpenCodeAdapter()
    native = NativeTranscript((FIXTURE / "complete.mock.jsonl").read_text(encoding="utf-8"))
    session = adapter.sessions(native, [])[0]
    assert session.status == "complete"
    assert [event.kind for turn in session.turns for event in turn.skill_events] == [
        "listed",
        "loaded",
        "invoked",
    ]
    partial = adapter.sessions(
        NativeTranscript((FIXTURE / "truncated.mock.jsonl").read_text(encoding="utf-8")), []
    )[0]
    assert partial.status == "partial"
    assert len(partial.turns) == 1


def test_command_sets_container_home_and_preserves_task_prompt_argument(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Do the thing", encoding="utf-8")
    command = OpenCodeAdapter().command(
        Task(id="t", version=1, kind="agentic-code", prompt_file="prompt.md"),
        {"model": "arena/model"},
    )
    shell = command[-1]
    assert "export HOME=/home/agent" in shell
    assert "opencode run --format json --agent arena" in shell
    assert "prompt=$(cat prompt.md)" in shell
    assert "/tmp/arena-opencode-transcript.jsonl" in shell


@pytest.mark.skipif(
    os.environ.get("ARENA_OPENCODE_DOCKER_CONTRACT") != "1",
    reason="Set ARENA_OPENCODE_DOCKER_CONTRACT=1 after reviewing the container contract fixture",
)
def test_pinned_container_contract_records_only_request_metadata_and_prompt_digest(
    tmp_path: Path,
) -> None:
    """Manual contract test; never stores request or task prompt contents on the host."""
    image = _docker("build", "--quiet", "--tag", CONTRACT_IMAGE, str(FIXTURE))
    assert image
    results = tmp_path / "results"
    results.mkdir()
    results.chmod(0o777)
    gateway_name = f"arena-opencode-gw-{uuid.uuid4().hex[:8]}"
    _docker(
        "run",
        "--detach",
        "--name",
        gateway_name,
        "--mount",
        f"type=bind,src={results},dst=/results",
        "--entrypoint",
        "node",
        CONTRACT_IMAGE,
        "/opt/arena-mock/mock_gateway.js",
    )
    try:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        endpoint = GatewayEndpoint("http://gateway:7400", TOKEN)

        async def run_family(family: str) -> None:
            spec = SandboxSpec(
                trial_id=f"opencode-{uuid.uuid4().hex[:8]}",
                image=CONTRACT_IMAGE,
                command=("sleep", "infinity"),
                workspace=workspace,
                gateway_url=endpoint.url,
                gateway_token=endpoint.token,
                gateway_container=gateway_name,
            )

            async def scenario() -> None:
                adapter = OpenCodeAdapter()
                async with DockerSandbox().start(spec) as container:
                    box = DockerAgentBox(container)
                    await asyncio.to_thread(
                        adapter.wire,
                        box,
                        endpoint,
                        ModelRef.parse(f"{family}/pinned-model"),
                    )
                    await asyncio.to_thread(
                        box.execute,
                        (
                            "sh",
                            "-lc",
                            "export HOME=/home/agent; "
                            "opencode run --format json --agent arena "
                            "--model arena/pinned-model 'Reply with the word done.' "
                            "> /tmp/opencode-contract.jsonl",
                        ),
                    )

            await scenario()

        asyncio.run(run_family("family-a"))
        asyncio.run(run_family("family-b"))
        records = [
            json.loads(line)
            for line in (results / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        expected = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
        assert len(records) == 2
        assert all(row["token"] == TOKEN for row in records)
        assert all(row["path"] == "/v1/chat/completions" for row in records)
        expected_system = [{"bytes": len(SYSTEM_PROMPT.encode()), "sha256": expected}]
        assert all(row["system"] == expected_system for row in records)
        assert records[0]["prompt_parts"] == records[1]["prompt_parts"]
    finally:
        subprocess.run(("docker", "rm", "-f", gateway_name), capture_output=True, check=False)
