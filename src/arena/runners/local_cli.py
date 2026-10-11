"""Run one trial through the vendor's own CLI on a subscription sign-in.

This is the named exception to "agent config is written only inside trial containers"
(CP8 decision D1). The CLI runs on the host, so the runner confines it:

- a scratch directory (0700) holds the CLI's home and the trial workspace, and is deleted on
  success, error, timeout and cancel. The user's real `~/.claude` and `~/.codex` are never written.
- the sign-in is copied into the scratch home (0600) from a `SignIn` the caller hands over,
  and the child environment is an allowlist, so no `*_API_KEY` or `ANTHROPIC_*` variable leaks in.
- a model other than the contestant's is a failure. There is no fallback.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath

from arena.agents.local import (
    ClaudeLocalAdapter,
    CodexLocalAdapter,
    LocalCliAdapter,
    SignIn,
    StreamSummary,
    served_model_from_rollout,
)
from arena.providers.runtimes import (
    RuntimeStatus,
    detect_version,
    probe_environment,
    spec_for_vendor,
)

TASK_INPUT = "task-input"
MAX_OUTPUT_FILE_BYTES = 10 * 1024 * 1024
_DATED = re.compile(r"-\d{8}$")
_KILL_GRACE_SECONDS = 2.0


class FailureKind(StrEnum):
    AUTH_REQUIRED = "auth_required"
    LIMIT_REACHED = "limit_reached"
    MODEL_MISMATCH = "model_mismatch"
    TIMEOUT = "timeout"
    EXIT_CODE = "exit_code"
    PROTOCOL = "protocol"
    NO_USAGE = "no_usage"
    SPAWN = "spawn"


@dataclass(frozen=True)
class LocalCliRequest:
    adapter: LocalCliAdapter
    binary: str
    model: str
    prompt: str
    sign_in: SignIn
    timeout: float
    environ: Mapping[str, str]
    scratch_base: Path | None = None
    task_files: Mapping[str, bytes] = field(default_factory=lambda: dict[str, bytes]())


@dataclass(frozen=True)
class LocalCliResult:
    failure: FailureKind | None
    detail: str
    exit_code: int | None
    summary: StreamSummary | None
    outputs: Mapping[str, bytes]
    duration_seconds: float
    model_served_verified: bool

    @property
    def ok(self) -> bool:
        return self.failure is None


def adapter_for_runtime(runtime: str) -> LocalCliAdapter:
    if runtime == "claude":
        return ClaudeLocalAdapter()
    if runtime == "codex":
        return CodexLocalAdapter()
    raise ValueError(f"no local CLI adapter for runtime {runtime!r}")


def served_matches(requested: str, served: str) -> bool:
    """Same model, allowing the dated alias a vendor may report (`name-20250929`)."""
    return served == requested or _DATED.sub("", served) == requested


async def run_local_cli(request: LocalCliRequest) -> LocalCliResult:
    started = time.monotonic()
    base = request.scratch_base
    if base is not None:
        base.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="arena-local-cli-", dir=base))
    try:
        scratch.chmod(0o700)
        home = scratch / "home"
        workspace = scratch / "workspace"
        home.mkdir(mode=0o700)
        workspace.mkdir(mode=0o700)
        env = probe_environment(
            request.environ, request.adapter.home_environment(home, request.model)
        )
        if request.adapter.runtime == "claude":
            spec = spec_for_vendor("claude")
            assert spec is not None
            report = await asyncio.to_thread(
                detect_version,
                spec,
                env,
                find=lambda _spec, _env: request.binary,
            )
            if report.status is not RuntimeStatus.OK:
                failure = (
                    FailureKind.SPAWN
                    if report.status
                    in {RuntimeStatus.NOT_INSTALLED, RuntimeStatus.PERMISSION_FAILURE}
                    else FailureKind.PROTOCOL
                )
                return _failed(
                    started,
                    failure,
                    "Claude Code restricted mode requires version 2.1.248 or later",
                )
        _place_files(home, request.sign_in.files)
        _place_files(workspace / TASK_INPUT, request.task_files)
        argv = request.adapter.command(request.binary, request.model, request.prompt, workspace)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=workspace,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as error:
            return _failed(started, FailureKind.SPAWN, f"{type(error).__name__}: {error}")
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), request.timeout)
        except TimeoutError:
            await _terminate(process)
            return _failed(
                started, FailureKind.TIMEOUT, f"no result within {request.timeout:g} seconds"
            )
        except asyncio.CancelledError:
            await _terminate(process)
            raise
        lines = stdout.decode("utf-8", errors="replace").splitlines()
        summary = request.adapter.parse([line for line in lines if line.strip()])
        served = summary.model_served or _read_codex_rollout(home)
        outputs = _collect_outputs(workspace)
        return _classify(started, request, process.returncode, summary, served, stderr, outputs)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _classify(
    started: float,
    request: LocalCliRequest,
    exit_code: int | None,
    summary: StreamSummary,
    served: str | None,
    stderr: bytes,
    outputs: Mapping[str, bytes],
) -> LocalCliResult:
    verified = served is not None
    tail = stderr.decode("utf-8", errors="replace").strip()[-500:]

    def result(failure: FailureKind | None, detail: str) -> LocalCliResult:
        return LocalCliResult(
            failure,
            detail,
            exit_code,
            summary,
            outputs,
            time.monotonic() - started,
            verified,
        )

    # What the vendor said about sign-in and limits outranks the exit code.
    if summary.auth_failed:
        return result(FailureKind.AUTH_REQUIRED, summary.error or "sign-in rejected")
    if summary.limit_hit:
        return result(FailureKind.LIMIT_REACHED, summary.error or "subscription limit reached")
    if exit_code != 0:
        return result(FailureKind.EXIT_CODE, f"exit {exit_code}: {summary.error or tail}")
    if summary.events == 0:
        return result(FailureKind.PROTOCOL, "the CLI printed no events")
    if served is not None and not served_matches(request.model, served):
        return result(
            FailureKind.MODEL_MISMATCH,
            f"asked for {request.model} and the CLI served {served}",
        )
    if summary.usage is None:
        return result(FailureKind.NO_USAGE, "the CLI reported no token usage")
    return result(None, "ok")


def _failed(started: float, failure: FailureKind, detail: str) -> LocalCliResult:
    return LocalCliResult(failure, detail, None, None, {}, time.monotonic() - started, False)


def _place_files(root: Path, files: Mapping[str, bytes]) -> None:
    for relative, content in files.items():
        if not _safe(relative):
            raise ValueError(f"unsafe path {relative!r}")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)


def _safe(relative: str) -> bool:
    path = PurePosixPath(relative)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _collect_outputs(workspace: Path) -> dict[str, bytes]:
    """Files the CLI wrote, except the task inputs, links, and anything very large."""
    found: dict[str, bytes] = {}
    for path in sorted(workspace.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(workspace).as_posix()
        if relative.startswith(f"{TASK_INPUT}/") or not _safe(relative):
            continue
        if path.stat().st_size > MAX_OUTPUT_FILE_BYTES:
            continue
        found[relative] = path.read_bytes()
    return found


def _read_codex_rollout(home: Path) -> str | None:
    sessions = home / ".codex" / "sessions"
    if not sessions.is_dir():
        return None
    for rollout in sorted(sessions.rglob("rollout-*.jsonl")):
        model = served_model_from_rollout(rollout.read_text(encoding="utf-8", errors="replace"))
        if model:
            return model
    return None


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """Stop the CLI and everything it started: it leads its own process group."""
    if process.returncode is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), _KILL_GRACE_SECONDS)
            return
        except TimeoutError:
            continue
