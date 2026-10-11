"""Read the sign-in a vendor CLI keeps on disk, only when asked to."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from arena.agents.local.claude import CREDENTIALS_FILE
from arena.agents.local.codex import AUTH_FILE
from arena.agents.local.types import SignIn


class SignInError(Exception):
    """No usable subscription sign-in. The message never holds a token."""


def _home(environ: Mapping[str, str]) -> Path:
    return Path(environ.get("HOME", "")) if environ.get("HOME") else Path.home()


def codex_sign_in(environ: Mapping[str, str]) -> SignIn:
    """The ChatGPT sign-in in `$CODEX_HOME/auth.json`, refused if it is an API key."""
    codex_home = environ.get("CODEX_HOME")
    path = (Path(codex_home) if codex_home else _home(environ) / ".codex") / "auth.json"
    try:
        content = path.read_bytes()
        data: Any = json.loads(content)
    except (OSError, json.JSONDecodeError) as error:
        raise SignInError("no Codex sign-in file; run `codex login`") from error
    mode = cast(dict[str, Any], data).get("auth_mode") if isinstance(data, dict) else None
    if mode != "chatgpt":
        raise SignInError("the Codex sign-in is not a ChatGPT subscription sign-in")
    return SignIn({AUTH_FILE: content})


def claude_sign_in(environ: Mapping[str, str]) -> SignIn:
    """The Claude sign-in file. macOS keeps it in the Keychain, which only an explicit import
    reads (`arena providers import claude`), so on macOS this file is usually absent."""
    config_dir = environ.get("CLAUDE_CONFIG_DIR")
    path = (
        Path(config_dir) / ".credentials.json" if config_dir else _home(environ) / CREDENTIALS_FILE
    )
    try:
        return SignIn({CREDENTIALS_FILE: path.read_bytes()})
    except OSError as error:
        raise SignInError(
            "no Claude sign-in file; on macOS the sign-in is in the Keychain and must be "
            "imported explicitly"
        ) from error
