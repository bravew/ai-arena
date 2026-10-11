"""A Docker-backed `Sandbox` for execution scorers.

Each sandbox is one throwaway container with no network, no capabilities, a read-only root and
small tmpfs mounts, running as an unprivileged user. Files go in as a tar stream on stdin and
the command runs through `docker exec`, so a scored solution never touches the host.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import uuid
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager

from arena.scorers.execution import CommandResult, Sandbox, SandboxError, SandboxFactory

WORKDIR = "/work"
# The task commands the suites use, and the image whose toolchain runs each. Tags are pinned to
# a release line, not a digest. `arena-scorer-python` is built from docker/scorer-python.Dockerfile.
COMMAND_IMAGES: dict[str, str] = {
    "python": "arena-scorer-python:1",
    "node": "node:22-slim",
    "cargo": "rust:1-slim",
    "go": "golang:1.23",
}
_ENV = {
    "HOME": "/tmp",
    "PYTHONDONTWRITEBYTECODE": "1",
    "CARGO_HOME": "/tmp/.cargo",
    "CARGO_NET_OFFLINE": "true",
    "GOCACHE": "/tmp/.gocache",
    "GOPATH": "/tmp/.gopath",
    "GOFLAGS": "-mod=mod",
    "GOPROXY": "off",
}
_KILL_GRACE_S = 15.0


def image_for_command(command: Sequence[str]) -> str:
    """The scorer image for a task command, or an error naming the unsupported executable."""
    if not command or command[0] not in COMMAND_IMAGES:
        raise SandboxError(
            f"no scorer image for command {command[:1]!r}; known: {sorted(COMMAND_IMAGES)}"
        )
    return COMMAND_IMAGES[command[0]]


Docker = Callable[..., "subprocess.CompletedProcess[bytes]"]


def _docker(
    *args: str, input: bytes | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["docker", *args], input=input, capture_output=True, timeout=timeout, check=False
    )


class DockerScorerSandbox:
    def __init__(self, name: str, docker: Docker) -> None:
        self._name = name
        self._docker = docker

    def put_files(self, files: Mapping[str, bytes]) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for path, data in sorted(files.items()):
                info = tarfile.TarInfo(path)
                info.size = len(data)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
        result = self._docker(
            "exec", "-i", "-w", WORKDIR, self._name, "tar", "-x", "-f", "-", input=buffer.getvalue()
        )
        if result.returncode != 0:
            raise SandboxError(f"cannot copy files in: {result.stderr.decode(errors='replace')}")

    def run(self, command: Sequence[str], *, timeout_s: float) -> CommandResult:
        try:
            result = self._docker("exec", "-w", WORKDIR, self._name, *command, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            self._docker("kill", self._name, timeout=_KILL_GRACE_S)
            return CommandResult(
                -1,
                (exc.stdout or b"").decode(errors="replace"),
                (exc.stderr or b"").decode(errors="replace"),
                timed_out=True,
            )
        return CommandResult(
            result.returncode,
            result.stdout.decode(errors="replace"),
            result.stderr.decode(errors="replace"),
        )


def docker_sandbox_factory(
    image: str,
    *,
    cpus: float = 2.0,
    memory: str = "2g",
    pids_limit: int = 512,
    docker: Docker = _docker,
) -> SandboxFactory:
    """A factory of sandboxes in `image`; each `with` starts and removes one container."""

    @contextmanager
    def open_sandbox() -> Generator[Sandbox]:
        name = f"arena-score-{uuid.uuid4().hex[:12]}"
        env = [arg for key, value in _ENV.items() for arg in ("--env", f"{key}={value}")]
        started = docker(
            "run", "-d", "--name", name, "--network", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--read-only", "--user", "65534:65534",
            "--cpus", str(cpus), "--memory", memory, "--pids-limit", str(pids_limit),
            "--tmpfs", "/tmp:rw,exec,nosuid,size=1g",
            "--tmpfs", f"{WORKDIR}:rw,exec,nosuid,size=512m,uid=65534,gid=65534",
            "--workdir", WORKDIR, *env, image, "sleep", "infinity",
        )  # fmt: skip
        if started.returncode != 0:
            raise SandboxError(f"cannot start {image}: {started.stderr.decode(errors='replace')}")
        try:
            yield DockerScorerSandbox(name, docker)
        finally:
            docker("rm", "-f", name)

    return open_sandbox
