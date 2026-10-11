"""Read the Claude sign-in from the macOS Keychain, only on an explicit import.

The sign-in is a generic-password item with service `Claude Code-credentials`. macOS may ask the
user to allow the read. The secret goes to the caller as bytes and is never printed or logged.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping

from arena.agents.local.claude import CREDENTIALS_FILE
from arena.agents.local.types import SignIn
from arena.providers.runtimes import probe_environment

KEYCHAIN_SERVICE = "Claude Code-credentials"


class KeychainError(Exception):
    """The Keychain could not give up a Claude sign-in. The message never holds a secret."""


def read_claude_keychain(
    environ: Mapping[str, str],
    *,
    platform: str = sys.platform,
    security: str = "security",
    timeout: float = 60.0,
) -> SignIn:
    if platform != "darwin":
        raise KeychainError("the Keychain import is for macOS; on Linux use the sign-in file")
    try:
        completed = subprocess.run(
            [security, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            env=probe_environment(environ),
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise KeychainError("could not run the macOS `security` tool") from error
    if completed.returncode != 0:
        raise KeychainError(
            f"no Keychain item {KEYCHAIN_SERVICE!r}, or access was denied; "
            "sign in with `claude auth login` first"
        )
    content = completed.stdout.strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        raise KeychainError("the Keychain item is not the JSON sign-in the CLI writes") from error
    if not isinstance(data, dict):
        raise KeychainError("the Keychain item is not the JSON sign-in the CLI writes")
    return SignIn({CREDENTIALS_FILE: content})
