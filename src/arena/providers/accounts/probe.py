"""Probe a selected subscription account in an isolated home, without a model call."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from arena.agents.local.types import SignIn
from arena.providers.runtimes import (
    AuthState,
    RuntimeReport,
    RuntimeStatus,
    detect_runtime,
    probe_environment,
)
from arena.providers.runtimes.detect import find_binary, run_probe
from arena.providers.runtimes.specs import RuntimeSpec
from arena.runners.local_cli import adapter_for_runtime

MAX_CREDENTIAL_BYTES = 1024 * 1024


def valid_sign_in(vendor: str, sign_in: SignIn) -> bool:
    """Accept only the vendor's subscription credential file, never API-key auth."""
    relative = ".claude/.credentials.json" if vendor == "claude" else ".codex/auth.json"
    if vendor not in {"claude", "chatgpt"} or set(sign_in.files) != {relative}:
        return False
    content = sign_in.files[relative]
    if not content or len(content) > MAX_CREDENTIAL_BYTES:
        return False
    try:
        data: Any = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    data = cast(dict[str, Any], data)
    if vendor == "claude":
        oauth: Any = data.get("claudeAiOauth")
        return isinstance(oauth, dict) and _token(cast(dict[str, Any], oauth).get("accessToken"))
    tokens: Any = data.get("tokens")
    return (
        data.get("auth_mode") == "chatgpt"
        and not data.get("OPENAI_API_KEY")
        and isinstance(tokens, dict)
        and all(
            _token(cast(dict[str, Any], tokens).get(key))
            for key in ("access_token", "refresh_token", "id_token")
        )
    )


# Claude reports `claude.ai` for a stored subscription login and `oauth_token` when a token
# arrives by environment. API-key, helper and signed-out states are never subscriptions.
_CLAUDE_SUBSCRIPTION_METHODS = frozenset({"authMethod=claude.ai", "authMethod=oauth_token"})


def subscription_auth(vendor: str, detail: str | None) -> bool:
    """Whether a CLI's auth detail line describes a subscription sign-in."""
    if vendor == "claude":
        return detail in _CLAUDE_SUBSCRIPTION_METHODS
    return detail == "Logged in using ChatGPT"


def _token(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 65536
        and not any(char.isspace() or ord(char) < 32 for char in value)
    )


def private_environment(
    spec: RuntimeSpec, home: Path, environ: Mapping[str, str]
) -> dict[str, str]:
    """Pin both config namespaces. USER keeps its username semantics for Keychain items."""
    extra = adapter_for_runtime(spec.runtime).home_environment(home, "")
    # Helper model overrides are not needed for auth and must never be empty.
    extra = {key: value for key, value in extra.items() if value}
    extra.update({"XDG_CONFIG_HOME": str(home / ".config"), "TMPDIR": str(home / "tmp")})
    if spec.vendor == "claude":
        extra["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = extra["CLAUDE_CONFIG_DIR"]
    return probe_environment(environ, extra)


def prepare_home(spec: RuntimeSpec, home: Path) -> None:
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    home.chmod(0o700)
    (home / "tmp").mkdir(mode=0o700, exist_ok=True)
    config = home / (".claude" if spec.vendor == "claude" else ".codex")
    config.mkdir(mode=0o700, exist_ok=True)
    if spec.vendor == "chatgpt":
        path = config / "config.toml"
        path.write_text('cli_auth_credentials_store = "file"\n', encoding="utf-8")
        path.chmod(0o600)


@contextmanager
def staged_account(
    spec: RuntimeSpec, sign_in: SignIn, environ: Mapping[str, str]
) -> Generator[dict[str, str], None, None]:
    """Copy only known credential paths; delete all staged bytes on every exit."""
    with tempfile.TemporaryDirectory(prefix="arena-account-probe-") as directory:
        home = Path(directory).resolve()
        prepare_home(spec, home)
        allowed = ".claude/.credentials.json" if spec.vendor == "claude" else ".codex/auth.json"
        if set(sign_in.files) != {allowed}:
            raise ValueError("account has no vendor credential file")
        content = sign_in.files[allowed]
        if len(content) > MAX_CREDENTIAL_BYTES:
            raise ValueError("account credential file is too large")
        descriptor = os.open(home / allowed, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        yield private_environment(spec, home, environ)


def detect_account(spec: RuntimeSpec, sign_in: SignIn, environ: Mapping[str, str]) -> RuntimeReport:
    """Version and auth status of the selected account, independent of host sign-in."""
    binary = find_binary(spec, environ)
    try:
        with staged_account(spec, sign_in, environ) as env:
            # detect_runtime builds its own allowlist. Reapply the private namespace to each
            # child so the CLI cannot consult a host Keychain service or XDG config.
            report = detect_runtime(
                spec,
                env,
                find=lambda _spec, _env: binary,
                probe=lambda argv, child, timeout: run_probe(
                    argv, {**child, **private_environment(spec, Path(env["HOME"]), env)}, timeout
                ),
            )
    except (OSError, ValueError):
        return RuntimeReport(
            spec.runtime,
            spec.vendor,
            RuntimeStatus.PROTOCOL_FAILURE,
            AuthState.UNKNOWN,
            None,
            None,
            "could not stage the selected account",
            None,
        )
    status, auth = report.status, report.auth
    if status is RuntimeStatus.OK and not subscription_auth(spec.vendor, report.detail):
        status, auth = RuntimeStatus.AUTH_REQUIRED, AuthState.UNAUTHENTICATED
    detail = (
        "selected subscription account authenticated"
        if status is RuntimeStatus.OK
        else "selected account probe: " + status.value
    )
    return RuntimeReport(
        report.runtime,
        report.vendor,
        status,
        auth,
        report.binary,
        report.version,
        detail,
        None,
    )
