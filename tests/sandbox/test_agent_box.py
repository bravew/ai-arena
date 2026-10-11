from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from arena.sandbox import DockerAgentBox, DockerSandbox, SandboxContainer, SandboxSpec
from arena.sandbox.docker import DockerCommandError


class FakeDocker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def run(self, *args: str) -> str:
        self.calls.append(args)
        return f"{args[0]}-id" if args[0] in {"network", "run"} else ""


@pytest.fixture
def spec(tmp_path: Path) -> SandboxSpec:
    (tmp_path / "ws").mkdir()
    return SandboxSpec(
        trial_id="trial-1",
        image="arena-agent:latest",
        command=("agent", "run"),
        workspace=tmp_path / "ws",
        gateway_url="http://gateway:7400",
        gateway_token="trial-secret",
        gateway_container="arena-gateway",
    )


def test_start_runs_an_idle_container_so_the_agent_is_not_started_before_wiring(
    spec: SandboxSpec,
) -> None:
    async def scenario() -> list[tuple[str, ...]]:
        docker = FakeDocker()
        async with DockerSandbox(docker=docker).start(spec) as container:
            assert container.id == "run-id"
        return docker.calls

    run = next(call for call in asyncio.run(scenario()) if call[0] == "run")

    assert run[run.index("--entrypoint") : run.index("--entrypoint") + 2] == (
        "--entrypoint",
        "sleep",
    )
    assert run[-2:] == ("arena-agent:latest", "infinity")
    assert "agent" not in run[run.index("arena-agent:latest") :]
    assert any(arg.startswith("/home/agent:") for arg in run)  # writable home for adapter config
    assert "--read-only" in run and "ARENA_GATEWAY_TOKEN=trial-secret" in run


def test_run_still_starts_the_spec_command_without_the_idle_changes(spec: SandboxSpec) -> None:
    async def scenario() -> tuple[str, ...]:
        docker = FakeDocker()
        async with DockerSandbox(docker=docker).run(spec):
            pass
        return next(call for call in docker.calls if call[0] == "run")

    run = asyncio.run(scenario())

    assert run[-3:] == ("arena-agent:latest", "agent", "run")
    assert "--entrypoint" not in run


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], bytes | None]] = []

    def __call__(self, args: Sequence[str], stdin: bytes | None) -> bytes:
        self.calls.append((tuple(args), stdin))
        return b"out"


def test_box_writes_reads_and_executes_through_docker_exec() -> None:
    docker = Recorder()
    box = DockerAgentBox(SandboxContainer(id="c1", network="n"), docker)

    box.write_text("/home/agent/config", "secret-body")
    assert box.read_text("/home/agent/config") == "out"
    assert box.execute(["agent", "--version"]) == "out"

    (write, stdin), (read, _), (execute, _) = docker.calls
    assert write[:4] == ("exec", "-i", "c1", "sh") and write[-1] == "/home/agent/config"
    assert stdin == b"secret-body"
    assert "secret-body" not in " ".join(write)  # content goes over stdin, not argv
    assert read == ("exec", "c1", "cat", "/home/agent/config")
    assert execute == ("exec", "c1", "agent", "--version")


@pytest.mark.parametrize("path", ["relative/path", "", "/bad\x00path"])
def test_box_refuses_non_absolute_or_nul_paths(path: str) -> None:
    box = DockerAgentBox(SandboxContainer(id="c1", network="n"), Recorder())

    with pytest.raises(ValueError, match="absolute container path"):
        box.write_text(path, "x")
    with pytest.raises(ValueError, match="absolute container path"):
        box.read_text(path)


@pytest.mark.parametrize("command", [[], ["agent", ""]])
def test_box_refuses_empty_commands(command: list[str]) -> None:
    box = DockerAgentBox(SandboxContainer(id="c1", network="n"), Recorder())

    with pytest.raises(ValueError, match="non-empty"):
        box.execute(command)


def test_box_surfaces_docker_failures() -> None:
    def failing(args: Sequence[str], stdin: bytes | None) -> bytes:
        raise DockerCommandError(args, 1, "no such container")

    box = DockerAgentBox(SandboxContainer(id="gone", network="n"), failing)

    with pytest.raises(DockerCommandError, match="no such container"):
        box.execute(["agent"])


def test_workspace_mount_uses_only_fields_that_docker_accepts(spec: SandboxSpec) -> None:
    # `docker run --mount` rejects a bare `rw` field ("must be a key=value pair"); a fake client
    # cannot catch that, so pin the exact value here. Bind mounts are read-write by default.
    async def scenario() -> tuple[str, ...]:
        docker = FakeDocker()
        async with DockerSandbox(docker=docker).start(spec):
            pass
        return next(call for call in docker.calls if call[0] == "run")

    run = asyncio.run(scenario())

    mount = run[run.index("--mount") + 1]
    assert all("=" in field for field in mount.split(","))
    assert mount.endswith("dst=/workspace")
