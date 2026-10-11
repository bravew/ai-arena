from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

_TRIAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
# Writable home for adapter config in an otherwise read-only container; gone with the container.
_AGENT_HOME = "/home/agent:rw,nosuid,size=64m"
_IDLE = ("sleep", "infinity")


class SandboxError(RuntimeError):
    """Base error for sandbox setup and execution."""


class DockerCommandError(SandboxError):
    def __init__(self, command: Sequence[str], returncode: int, stderr: str) -> None:
        self.command = tuple(command)
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"Docker command failed ({returncode}): {' '.join(command)}: {stderr}")


class DockerClient(Protocol):
    async def run(self, *args: str) -> str: ...


class _DockerCLI:
    async def run(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise
        if process.returncode != 0:
            raise DockerCommandError(args, process.returncode or 1, stderr.decode(errors="replace"))
        return stdout.decode().strip()


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: float = 600
    cpus: float = 2.0
    memory_bytes: int = 4 * 1024**3
    pids_limit: int = 256

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.cpus <= 0:
            raise ValueError("cpus must be positive")
        if self.memory_bytes <= 0:
            raise ValueError("memory_bytes must be positive")
        if self.pids_limit <= 0:
            raise ValueError("pids_limit must be positive")


@dataclass(frozen=True)
class SandboxSpec:
    trial_id: str
    image: str
    command: tuple[str, ...]
    workspace: Path
    gateway_url: str
    gateway_token: str
    gateway_container: str

    def __post_init__(self) -> None:
        if not _TRIAL_ID.fullmatch(self.trial_id):
            raise ValueError("trial_id must be a Docker-safe identifier")
        if not self.image or self.image.startswith("-"):
            raise ValueError("image must be a non-empty image reference")
        if not self.command:
            raise ValueError("command must contain at least one argument")
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        if not self.gateway_token:
            raise ValueError("gateway_token must not be empty")
        gateway = urlsplit(self.gateway_url)
        if gateway.scheme not in {"http", "https"} or not gateway.hostname:
            raise ValueError("gateway_url must be an http(s) URL with a hostname")
        if gateway.username or gateway.password:
            raise ValueError("gateway_url must not contain credentials")


@dataclass(frozen=True)
class SandboxContainer:
    id: str
    network: str


class DockerSandbox:
    """Run trial containers on a private internal network with bounded resources."""

    def __init__(self, docker: DockerClient | None = None) -> None:
        self._docker = docker or _DockerCLI()

    @asynccontextmanager
    async def run(
        self, spec: SandboxSpec, limits: SandboxLimits | None = None
    ) -> AsyncGenerator[SandboxContainer, None]:
        """Start the container with `spec.command` as its main process."""
        async with self._open(spec, limits or SandboxLimits(), spec.command, idle=False) as box:
            yield box

    @asynccontextmanager
    async def start(
        self, spec: SandboxSpec, limits: SandboxLimits | None = None
    ) -> AsyncGenerator[SandboxContainer, None]:
        """Start an idle container so an adapter can wire it and then `docker exec` into it.

        The agent command is not the container's main process here; the runner executes it after
        the adapter has written its config, so a wiring failure never starts the agent.
        """
        async with self._open(spec, limits or SandboxLimits(), _IDLE, idle=True) as box:
            yield box

    @asynccontextmanager
    async def _open(
        self,
        spec: SandboxSpec,
        limits: SandboxLimits,
        command: Sequence[str],
        *,
        idle: bool,
    ) -> AsyncGenerator[SandboxContainer, None]:
        name = f"arena-{spec.trial_id}"
        network = name
        network_id: str | None = None
        container_id: str | None = None
        gateway_connected = False
        try:
            network_id = await self._docker.run(
                "network", "create", "--internal", "--driver", "bridge", network
            )
            gateway_host = urlsplit(spec.gateway_url).hostname or "gateway"
            await self._docker.run(
                "network", "connect", "--alias", gateway_host, network, spec.gateway_container
            )
            gateway_connected = True
            args = [
                "run",
                "--detach",
                "--name",
                name,
                "--network",
                network,
                "--cpus",
                str(limits.cpus),
                "--memory",
                str(limits.memory_bytes),
                "--pids-limit",
                str(limits.pids_limit),
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                *(("--tmpfs", _AGENT_HOME) if idle else ()),
                "--mount",
                f"type=bind,src={spec.workspace.resolve()},dst=/workspace",
                "--workdir",
                "/workspace",
                "--env",
                f"ARENA_GATEWAY_URL={spec.gateway_url}",
                "--env",
                f"ARENA_GATEWAY_TOKEN={spec.gateway_token}",
                "--env",
                "NO_PROXY=*",
                *(("--entrypoint", command[0]) if idle else ()),
                spec.image,
                *(command[1:] if idle else command),
            ]
            container_id = await self._docker.run(*args)
            timeout_scope = asyncio.timeout(limits.timeout_seconds)
            try:
                async with timeout_scope:
                    yield SandboxContainer(id=container_id, network=network)
            except TimeoutError as error:
                if timeout_scope.expired():
                    raise TimeoutError(
                        f"sandbox timed out after {limits.timeout_seconds} seconds"
                    ) from error
                raise
        finally:
            cleanup = asyncio.create_task(
                self._cleanup(container_id, network_id, gateway_connected, spec.gateway_container)
            )
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

    async def _cleanup(
        self,
        container_id: str | None,
        network_id: str | None,
        gateway_connected: bool,
        gateway_container: str,
    ) -> None:
        cleanup_error: Exception | None = None
        if container_id is not None:
            try:
                await self._docker.run("rm", "-f", container_id)
            except DockerCommandError as error:
                cleanup_error = error
        if gateway_connected and network_id is not None:
            try:
                await self._docker.run("network", "disconnect", network_id, gateway_container)
            except DockerCommandError as error:
                cleanup_error = cleanup_error or error
        if network_id is not None:
            try:
                await self._docker.run("network", "rm", network_id)
            except DockerCommandError as error:
                cleanup_error = cleanup_error or error
        if cleanup_error is not None:
            raise cleanup_error
