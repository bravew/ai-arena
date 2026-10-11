"""What the arena knows about each vendor CLI, taken from the recorded contracts.

The commands and output shapes come from docs/subsystems/provider-runtimes.md and the
fixtures under fixtures/cli-contracts. A version older than `min_version` is unverified, not
known bad, so detection reports it as incompatible and says which version was recorded.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast


class ProtocolFailure(Exception):
    """A CLI answered, but not in the shape the recorded contract describes."""


# Parsed from a probe: (signed in?, detail). None means the CLI gave no usable answer.
AuthParser = Callable[[int, str, str], tuple[bool, str]]


@dataclass(frozen=True)
class RuntimeSpec:
    runtime: str
    vendor: str
    binary: str
    path_variable: str
    min_version: tuple[int, int, int]
    recorded_version: str
    version_args: tuple[str, ...]
    auth_args: tuple[str, ...]
    parse_auth: AuthParser
    install_hint: str
    login_hint: str
    common_dirs: tuple[str, ...] = (
        "~/.local/bin",
        "~/.claude/local",
        "~/.npm-global/bin",
        "~/.bun/bin",
        "/opt/homebrew/bin",
        "/usr/local/bin",
    )


def _parse_claude_auth(returncode: int, stdout: str, stderr: str) -> tuple[bool, str]:
    # `claude auth status` exits 0 whether or not it is signed in, so only the JSON counts.
    try:
        raw: Any = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise ProtocolFailure("claude auth status did not print JSON") from error
    if not isinstance(raw, dict):
        raise ProtocolFailure("claude auth status did not print a JSON object")
    data = cast(dict[str, Any], raw)
    signed_in = data.get("loggedIn")
    if not isinstance(signed_in, bool):
        raise ProtocolFailure("claude auth status has no boolean loggedIn")
    return signed_in, f"authMethod={data.get('authMethod')}"


def _parse_codex_auth(returncode: int, stdout: str, stderr: str) -> tuple[bool, str]:
    # `codex login status` exits non-zero when signed out, so the text is the answer and the
    # exit code only a hint. It prints to stdout or stderr depending on the version.
    text = (stdout + "\n" + stderr).strip()
    if re.search(r"not logged in", text, re.IGNORECASE):
        return False, "Not logged in"
    match = re.search(r"logged in using ([^\n]+)", text, re.IGNORECASE)
    if match:
        return True, f"Logged in using {match.group(1).strip()}"
    raise ProtocolFailure("codex login status gave an answer that is neither signed in nor out")


CLAUDE = RuntimeSpec(
    runtime="claude",
    vendor="claude",
    binary="claude",
    path_variable="CLAUDE_CODE_PATH",
    min_version=(2, 1, 248),
    recorded_version="2.1.296",
    version_args=("--version",),
    auth_args=("auth", "status"),
    parse_auth=_parse_claude_auth,
    install_hint="npm install -g @anthropic-ai/claude-code",
    login_hint="claude auth login",
)

CODEX = RuntimeSpec(
    runtime="codex",
    vendor="chatgpt",
    binary="codex",
    path_variable="CODEX_CLI_PATH",
    min_version=(0, 162, 0),
    recorded_version="0.162.1",
    version_args=("--version",),
    auth_args=("login", "status"),
    parse_auth=_parse_codex_auth,
    install_hint="npm install -g @openai/codex",
    login_hint="codex login",
)

SPECS: tuple[RuntimeSpec, ...] = (CLAUDE, CODEX)


def spec_for_vendor(vendor: str) -> RuntimeSpec | None:
    """The CLI spec for a subscription vendor, or None when it has no CLI contract."""
    return next((spec for spec in SPECS if spec.vendor == vendor), None)
