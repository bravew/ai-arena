"""pytest plugin: a temporary HOME, agent config dirs, and no provider secrets for every test."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.testenv import guard

# Each of these points at a directory under the temporary HOME.
HOME_VARS = {
    "HOME": ".",
    "USERPROFILE": ".",
    "XDG_CONFIG_HOME": ".config",
    "XDG_DATA_HOME": ".local/share",
    "XDG_CACHE_HOME": ".cache",
    "XDG_STATE_HOME": ".local/state",
    "CLAUDE_CONFIG_DIR": ".claude",
    "CODEX_HOME": ".codex",
    "PI_CODING_AGENT_DIR": ".pi/agent",
    "ARENA_HOME": ".arena",
}

# Credentials a test must never see, so nothing can spend on a real provider by accident.
SECRET_SUFFIXES = ("_API_KEY", "_AUTH_TOKEN", "_ACCESS_TOKEN", "_OAUTH_TOKEN")
SECRET_NAMES = ("GITHUB_TOKEN", "GH_TOKEN", "COPILOT_TOKEN", "ARENA_RUN_KEY")


class TempEnv:
    def __init__(self, home: Path, guard_: guard.RealHomeGuard) -> None:
        self.home = home
        self.guard = guard_


@pytest.fixture(autouse=True)
def testenv(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TempEnv]:
    home = tmp_path_factory.mktemp("home")
    for var, sub in HOME_VARS.items():
        monkeypatch.setenv(var, str(home / sub))
    for name in list(os.environ):
        if name.endswith(SECRET_SUFFIXES) or name in SECRET_NAMES:
            monkeypatch.delenv(name)
    g = guard.RealHomeGuard(home, allowed=[tmp_path_factory.getbasetemp()])
    guard.activate(g)
    try:
        yield TempEnv(home, g)
    finally:
        guard.deactivate()
    if g.violations:
        pytest.fail("test touched the real HOME:\n  " + "\n  ".join(g.violations), pytrace=False)
