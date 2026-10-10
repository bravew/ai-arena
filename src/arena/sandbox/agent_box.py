"""AgentBox backed by `docker exec` into a running trial container."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

from arena.sandbox.docker import DockerCommandError, SandboxContainer

# (docker args, stdin) -> stdout. Injected by tests; the default shells out to `docker`.
DockerExec = Callable[[Sequence[str], bytes | None], bytes]

_WRITE = 'umask 077; mkdir -p "$(dirname "$1")"; cat > "$1"'


def _docker(args: Sequence[str], stdin: bytes | None) -> bytes:
    result = subprocess.run(("docker", *args), input=stdin, capture_output=True, check=False)
    if result.returncode != 0:
        raise DockerCommandError(args, result.returncode, result.stderr.decode(errors="replace"))
    return result.stdout


class DockerAgentBox:
    """Synchronous box: the runner calls it from a worker thread (`asyncio.to_thread`)."""

    def __init__(self, container: SandboxContainer, docker: DockerExec = _docker) -> None:
        self._id = container.id
        self._docker = docker

    def write_text(self, path: str, content: str) -> None:
        _check_path(path)
        self._docker(
            ("exec", "-i", self._id, "sh", "-c", _WRITE, "arena-write", path), content.encode()
        )

    def read_text(self, path: str) -> str:
        _check_path(path)
        return self._docker(("exec", self._id, "cat", path), None).decode()

    def execute(self, command: Sequence[str]) -> str:
        if not command or any(not part for part in command):
            raise ValueError("command must contain non-empty arguments")
        return self._docker(("exec", self._id, *command), None).decode()


def _check_path(path: str) -> None:
    if not path.startswith("/") or "\x00" in path:
        raise ValueError("path must be an absolute container path")
