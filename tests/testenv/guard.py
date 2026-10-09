"""Fail any test that writes under the real HOME or reads a real agent config.

A Python audit hook sees every open, mkdir, rename, remove, and listing in this process. While a
guard is active, an access the guard forbids raises `RealHomeAccess` and is also recorded, so a
test that swallows the exception still fails at teardown. Subprocesses are not audited; they
inherit the temporary HOME instead.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

# Captured at import, before any test replaces HOME.
REAL_HOME = Path(os.path.expanduser("~")).resolve()
REPO_ROOT = Path(__file__).resolve().parents[2]

# Agent and arena state that tests may not even read.
AGENT_STATE = (
    ".claude",
    ".claude.json",
    ".codex",
    ".config",
    ".gemini",
    ".pi",
    ".local/share/opencode",
    ".aider.conf.yml",
    ".arena",
)

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

# event -> (indexes of path arguments, whether the event writes)
_PATH_EVENTS: dict[str, tuple[tuple[int, ...], bool]] = {
    "os.mkdir": ((0,), True),
    "os.rename": ((0, 1), True),
    "os.replace": ((0, 1), True),
    "os.remove": ((0,), True),
    "os.rmdir": ((0,), True),
    "os.symlink": ((1,), True),
    "os.link": ((1,), True),
    "os.truncate": ((0,), True),
    "os.chmod": ((0,), True),
    "os.chown": ((0,), True),
    "os.utime": ((0,), True),
    "shutil.copyfile": ((1,), True),
    "shutil.copytree": ((1,), True),
    "shutil.move": ((0, 1), True),
    "shutil.rmtree": ((0,), True),
    "os.listdir": ((0,), False),
    "os.scandir": ((0,), False),
}


class RealHomeAccess(BaseException):
    """A test touched the real HOME. BaseException so `except Exception` can't hide it."""


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


class RealHomeGuard:
    def __init__(self, fake_home: Path, allowed: Iterable[Path] = ()) -> None:
        self.fake_home = fake_home.resolve()
        tmp = Path(tempfile.gettempdir()).resolve()
        roots = [self.fake_home, REPO_ROOT, Path(sys.prefix).resolve(), *allowed]
        if not _within(REAL_HOME, tmp):
            roots.append(tmp)
        self.allowed = [Path(r).resolve() for r in roots]
        self.agent_state = [REAL_HOME / p for p in AGENT_STATE]
        self.violations: list[str] = []

    def check(self, raw: object, writes: bool) -> str | None:
        if isinstance(raw, int) or raw is None:
            return None  # a file descriptor: its path was checked when it was opened
        try:
            path = Path(os.path.abspath(os.fsdecode(raw)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if not _within(path, REAL_HOME) or any(_within(path, a) for a in self.allowed):
            return None
        if writes:
            return f"write under the real HOME: {path}"
        if any(_within(path, s) for s in self.agent_state):
            return f"read of real agent or arena state: {path}"
        return None


_active: RealHomeGuard | None = None
_busy = False


def _is_write_open(args: tuple[object, ...]) -> bool:
    mode, flags = args[1], args[2]
    if isinstance(mode, str):
        return any(c in mode for c in "wax+")
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _hook(event: str, args: tuple[object, ...]) -> None:
    global _busy
    guard = _active
    if guard is None or _busy:
        return
    if event == "open":
        checks = [(args[0], _is_write_open(args))]
    elif event in _PATH_EVENTS:
        idx, writes = _PATH_EVENTS[event]
        checks = [(args[i], writes) for i in idx if i < len(args)]
    else:
        return
    _busy = True
    try:
        for raw, writes in checks:
            problem = guard.check(raw, writes)
            if problem:
                guard.violations.append(f"{event}: {problem}")
                raise RealHomeAccess(problem)
    finally:
        _busy = False


sys.addaudithook(_hook)


def activate(guard: RealHomeGuard) -> None:
    global _active
    _active = guard


def deactivate() -> None:
    global _active
    _active = None
