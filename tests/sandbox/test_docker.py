from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from arena.sandbox import DockerSandbox, SandboxLimits, SandboxSpec
from arena.sandbox.docker import DockerCommandError


class FakeDocker:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_on = fail_on

    async def run(self, *args: str) -> str:
        self.calls.append(args)
        if args[0] == self.fail_on:
            raise DockerCommandError(args, 1, "docker command failed")
        if args[0] in {"network", "run"}:
            return f"{args[0]}-id"
        return ""


@pytest.fixture
def spec(tmp_path: Path) -> SandboxSpec:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SandboxSpec(
        trial_id="trial-1",
        image="arena-agent:latest",
        command=("agent", "run"),
        workspace=workspace,
        gateway_url="http://gateway:7400",
        gateway_token="trial-secret",
        gateway_container="arena-gateway",
    )


def test_run_uses_internal_network_limits_and_trial_token_only(spec: SandboxSpec) -> None:
    async def scenario() -> None:
        docker = FakeDocker()
        sandbox = DockerSandbox(docker=docker)

        async with sandbox.run(spec, limits=SandboxLimits(timeout_seconds=30)) as container:
            assert (
                "network",
                "connect",
                "--alias",
                "gateway",
                "arena-trial-1",
                "arena-gateway",
            ) in docker.calls
            args = next(call for call in docker.calls if call[0] == "run")
            assert "--network" in args
            assert "--memory" in args and "--cpus" in args and "--pids-limit" in args
            assert "--read-only" in args
            mount = args[args.index("--mount") + 1]
            assert all("=" in field for field in mount.split(","))
            assert mount.endswith("dst=/workspace")
            assert "ARENA_GATEWAY_TOKEN=trial-secret" in args
            assert not any("ANTHROPIC_API_KEY" in arg or "OPENAI_API_KEY" in arg for arg in args)
            assert container.id == "run-id"

        assert ("rm", "-f", "run-id") in docker.calls
        assert any(call[0:2] == ("network", "disconnect") for call in docker.calls)
        assert docker.calls[-1] == ("network", "rm", "network-id")

    asyncio.run(scenario())


def test_network_is_internal_and_removed_if_container_create_fails(spec: SandboxSpec) -> None:
    async def scenario() -> None:
        docker = FakeDocker(fail_on="run")
        sandbox = DockerSandbox(docker=docker)

        with pytest.raises(DockerCommandError):
            async with sandbox.run(spec):
                pytest.fail("container should not start")

        assert (
            "network",
            "create",
            "--internal",
            "--driver",
            "bridge",
            "arena-trial-1",
        ) in docker.calls
        assert docker.calls[-1] == ("network", "rm", "network-id")

    asyncio.run(scenario())


def test_cancellation_removes_container_and_network(spec: SandboxSpec) -> None:
    async def scenario() -> None:
        docker = FakeDocker()
        sandbox = DockerSandbox(docker=docker)
        entered = asyncio.Event()

        async def use_sandbox() -> None:
            async with sandbox.run(spec) as _container:
                entered.set()
                await asyncio.Future()

        task = asyncio.create_task(use_sandbox())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert ("rm", "-f", "run-id") in docker.calls
        assert docker.calls[-1] == ("network", "rm", "network-id")

    asyncio.run(scenario())


def test_timeout_removes_container_and_network(spec: SandboxSpec) -> None:
    async def scenario() -> None:
        docker = FakeDocker()
        sandbox = DockerSandbox(docker=docker)

        with pytest.raises(TimeoutError, match="timed out"):
            async with sandbox.run(spec, limits=SandboxLimits(timeout_seconds=0.01)):
                await asyncio.sleep(1)

        assert ("rm", "-f", "run-id") in docker.calls
        assert docker.calls[-1] == ("network", "rm", "network-id")

    asyncio.run(scenario())


def test_invalid_configuration_is_rejected(spec: SandboxSpec) -> None:
    with pytest.raises(ValueError):
        SandboxSpec(
            trial_id="../host",
            image=spec.image,
            command=spec.command,
            workspace=spec.workspace,
            gateway_url=spec.gateway_url,
            gateway_token=spec.gateway_token,
            gateway_container=spec.gateway_container,
        )
    with pytest.raises(ValueError):
        SandboxLimits(memory_bytes=0)
