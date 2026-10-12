"""Read the Claude sign-in from the macOS Keychain, only when a run or test needs it.

The sign-in is a generic-password item with service `Claude Code-credentials`. macOS may ask the
user to allow the read. The secret goes to the caller as bytes and is never printed or logged.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from arena.agents.local.claude import CREDENTIALS_FILE
from arena.agents.local.types import SignIn
from arena.providers.runtimes import probe_environment

KEYCHAIN_SERVICE = "Claude Code-credentials"


class KeychainError(Exception):
    """The Keychain could not give up a Claude sign-in. The message never holds a secret."""


def claude_keychain_present(
    environ: Mapping[str, str],
    *,
    platform: str = sys.platform,
    security: str = "security",
    timeout: float = 10.0,
) -> bool:
    """Whether the Keychain holds a Claude sign-in. Metadata only: no `-w`, so the secret is not
    read and macOS shows no access prompt. Needs an explicit HOME so a bare test environment never
    falls back to the real login keychain."""
    if platform != "darwin" or not environ.get("HOME"):
        return False
    try:
        completed = subprocess.run(
            [security, "find-generic-password", "-s", KEYCHAIN_SERVICE],
            env=probe_environment(environ),
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


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


def scoped_claude_service(config_dir: Path) -> str:
    """Claude Code 2.1.296 hashes the explicit absolute config namespace."""
    if not config_dir.is_absolute():
        raise KeychainError("the private config namespace must be absolute")
    return KEYCHAIN_SERVICE + "-" + hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]


def _scoped_command(
    operation: str, config_dir: Path, environ: Mapping[str, str], security: str, timeout: float
) -> subprocess.CompletedProcess[bytes]:
    username = environ.get("USER") or environ.get("LOGNAME")
    if not username or len(username) > 256 or any(ord(char) < 32 for char in username):
        raise KeychainError("a username is required for the private Keychain item")
    argv = [security, operation, "-s", scoped_claude_service(config_dir), "-a", username]
    if operation == "find-generic-password":
        argv.append("-w")
    try:
        return subprocess.run(
            argv,
            env=probe_environment(environ),
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise KeychainError("could not access the private Keychain item") from error


def read_scoped_claude_keychain(
    config_dir: Path,
    environ: Mapping[str, str],
    *,
    security: str = "security",
    timeout: float = 5.0,
) -> SignIn:
    """Internal login reader. It never falls back to the user's default service."""
    result = _scoped_command("find-generic-password", config_dir, environ, security, timeout)
    if result.returncode != 0:
        raise KeychainError("no private Keychain item, or access was denied")
    content = result.stdout.strip()
    if not content or len(content) > 1024 * 1024:
        raise KeychainError("the private Keychain item is not a usable sign-in")
    return SignIn({CREDENTIALS_FILE: content})


def delete_scoped_claude_keychain(
    config_dir: Path,
    environ: Mapping[str, str],
    *,
    security: str = "security",
    timeout: float = 5.0,
) -> None:
    """Internal login cleanup, restricted to this login's service and username."""
    result = _scoped_command("delete-generic-password", config_dir, environ, security, timeout)
    # security returns 44 when the item does not exist; deletion is idempotent.
    if result.returncode not in {0, 44}:
        raise KeychainError("could not remove the private Keychain item")
