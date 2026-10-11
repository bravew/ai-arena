"""Accounts for local-cli providers: store, discovery, Keychain import and the CLI commands."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.agents.local.signin import SignInError, claude_sign_in
from arena.agents.local.types import SignIn
from arena.cli import app
from arena.providers.accounts import (
    AccountError,
    AccountStore,
    KeychainError,
    LoginError,
    discover,
    read_claude_keychain,
    resolve_sign_in,
    run_vendor_login,
)

TOKEN = "tok-SECRET-access-value"
REFRESH = "tok-SECRET-refresh-value"
CODEX_AUTH = json.dumps(
    {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": "tok-SECRET-id",
            "access_token": TOKEN,
            "refresh_token": REFRESH,
            "account_id": "acct-0001",
        },
    }
).encode()
CLAUDE_CREDENTIALS = json.dumps({"claudeAiOauth": {"accessToken": TOKEN}}).encode()
SECRETS = (TOKEN, REFRESH, "tok-SECRET-id", "acct-0001")


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    (path / ".codex").mkdir(parents=True)
    (path / ".codex" / "auth.json").write_bytes(CODEX_AUTH)
    return path


def _environ(home: Path, **extra: str) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "CODEX_HOME": str(home / ".codex"),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        **extra,
    }


def test_store_keeps_the_sign_in_private_and_round_trips(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    account = store.save("chatgpt", SignIn({".codex/auth.json": CODEX_AUTH}), source="test")
    sign_in = store.load("chatgpt")
    assert sign_in is not None and sign_in.files == {".codex/auth.json": CODEX_AUTH}
    for path in (tmp_path / "accounts").rglob("*"):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == (0o700 if path.is_dir() else 0o600), path
    meta = (tmp_path / "accounts" / "chatgpt" / "meta.json").read_text(encoding="utf-8")
    assert not any(secret in meta for secret in SECRETS)
    assert account.account_id in meta and "acct-0001" not in account.account_id
    assert store.list() == [account]
    assert store.forget("chatgpt") and not store.forget("chatgpt")
    assert store.load("chatgpt") is None and store.list() == []


def test_account_id_follows_the_account_not_the_token(tmp_path: Path) -> None:
    store = AccountStore(tmp_path)
    first = store.save("chatgpt", SignIn({"a": CODEX_AUTH}), "t").account_id
    refreshed = CODEX_AUTH.replace(TOKEN.encode(), b"tok-SECRET-newer")
    assert store.save("chatgpt", SignIn({"a": refreshed}), "t").account_id == first


def test_store_rejects_unknown_vendors_and_escaping_paths(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    with pytest.raises(AccountError, match="unknown vendor"):
        store.save("copilot", SignIn({"a": b"x"}), "t")
    with pytest.raises(AccountError, match="unknown vendor"):
        store.forget("../x")
    with pytest.raises(AccountError, match="leaves"):
        store.save("claude", SignIn({"../escape": b"x"}), "t")
    assert not (tmp_path / "escape").exists()
    assert store.list() == []


def test_discovery_is_read_only_and_redacted(home: Path) -> None:
    before = _tree(home)
    found = discover(_environ(home))
    assert [(item.vendor, item.location) for item in found] == [("chatgpt", "~/.codex/auth.json")]
    assert not any(secret in repr(found) for secret in SECRETS)
    assert _tree(home) == before


def test_discovery_ignores_an_api_key_sign_in_and_obeys_the_switch(home: Path) -> None:
    assert discover(_environ(home, ARENA_DISCOVER_SIGNINS="0")) == []
    (home / ".codex" / "auth.json").write_text('{"auth_mode": "apikey"}', encoding="utf-8")
    assert discover(_environ(home)) == []


def test_resolve_prefers_the_stored_copy_then_discovery(home: Path, tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts")
    environ = _environ(home)
    assert resolve_sign_in("chatgpt", store, environ).files == {".codex/auth.json": CODEX_AUTH}
    store.save("chatgpt", SignIn({".codex/auth.json": b"stored"}), "t")
    assert resolve_sign_in("chatgpt", store, environ).files == {".codex/auth.json": b"stored"}
    store.forget("chatgpt")
    with pytest.raises(SignInError, match="discovery is off"):
        resolve_sign_in("chatgpt", store, _environ(home, ARENA_DISCOVER_SIGNINS="off"))


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\nPATH=/usr/bin:/bin\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_keychain_import_reads_the_item_through_the_security_tool(tmp_path: Path) -> None:
    security = _script(
        tmp_path / "security",
        f'[ "$1 $2 $3" = "find-generic-password -s Claude Code-credentials" ] || exit 44\n'
        f"echo '{CLAUDE_CREDENTIALS.decode()}'",
    )
    sign_in = read_claude_keychain({}, platform="darwin", security=str(security))
    assert sign_in.files == {".claude/.credentials.json": CLAUDE_CREDENTIALS}


def test_keychain_import_fails_without_leaking(tmp_path: Path) -> None:
    denied = _script(tmp_path / "denied", f"echo {TOKEN} >&2; exit 44")
    with pytest.raises(KeychainError) as denied_error:
        read_claude_keychain({}, platform="darwin", security=str(denied))
    junk = _script(tmp_path / "junk", f"echo {TOKEN}")
    with pytest.raises(KeychainError) as junk_error:
        read_claude_keychain({}, platform="darwin", security=str(junk))
    assert TOKEN not in str(denied_error.value) + str(junk_error.value)
    with pytest.raises(KeychainError, match="macOS"):
        read_claude_keychain({}, platform="linux")


def test_login_runs_the_vendor_cli_without_key_variables(home: Path, tmp_path: Path) -> None:
    record = tmp_path / "record"
    codex = _script(tmp_path / "codex", f'echo "$*" > {record}; env | cut -d= -f1 >> {record}')
    environ = _environ(home, CODEX_CLI_PATH=str(codex), OPENAI_API_KEY="k", CODEX_API_KEY="k")
    run_vendor_login("chatgpt", environ)
    lines = record.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "login"
    assert not {"OPENAI_API_KEY", "CODEX_API_KEY"} & set(lines)
    failing = _script(tmp_path / "codex2", "exit 3")
    with pytest.raises(LoginError, match="status 3"):
        run_vendor_login("chatgpt", _environ(home, CODEX_CLI_PATH=str(failing)))
    with pytest.raises(LoginError, match="not installed"):
        run_vendor_login("chatgpt", _environ(home, CODEX_CLI_PATH=str(tmp_path / "missing")))


def _invoke(args: list[str], home: Path, **env: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, args, env={**_environ(home), **env})
    return result.exit_code, result.output


def test_add_list_forget_never_print_a_token_and_leave_the_real_sign_in_alone(
    home: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    accounts_home = tmp_path / "arena-home"
    before = _tree(home)
    outputs: list[str] = []
    code, output = _invoke(
        ["providers", "add", "codex", "--no-login", "--home", str(accounts_home)], home
    )
    outputs.append(output)
    assert code == 0, output
    assert "warning:" in output and "added codex account" in output
    code, output = _invoke(["providers", "list", "--home", str(accounts_home)], home)
    outputs.append(output)
    assert code == 0 and "chatgpt:" in output and "stored" in output
    code, output = _invoke(["providers", "forget", "codex", "--home", str(accounts_home)], home)
    outputs.append(output)
    assert code == 0 and "forgot codex" in output
    code, output = _invoke(["providers", "forget", "codex", "--home", str(accounts_home)], home)
    outputs.append(output)
    assert "no stored codex account" in output
    for text in [*outputs, caplog.text]:
        assert not any(secret in text for secret in SECRETS)
    assert _tree(home) == before


def test_add_runs_the_login_then_registers(home: Path, tmp_path: Path) -> None:
    record = tmp_path / "record"
    codex = _script(tmp_path / "codex", f'echo "$*" > {record}')
    code, output = _invoke(
        ["providers", "add", "codex", "--home", str(tmp_path / "h")],
        home,
        CODEX_CLI_PATH=str(codex),
    )
    assert code == 0, output
    assert record.read_text(encoding="utf-8").strip() == "login"


def test_list_shows_a_found_sign_in_and_honours_the_switch(home: Path, tmp_path: Path) -> None:
    args = ["providers", "list", "--home", str(tmp_path / "h")]
    code, output = _invoke(args, home)
    assert code == 0 and "found at ~/.codex/auth.json, not added" in output
    code, output = _invoke(args, home, ARENA_DISCOVER_SIGNINS="0")
    assert "found at" not in output and "discovery is off" in output


def test_add_claude_without_a_file_points_at_the_keychain_import(
    home: Path, tmp_path: Path
) -> None:
    code, output = _invoke(
        ["providers", "add", "claude", "--no-login", "--home", str(tmp_path / "h")], home
    )
    assert code == 1 and "providers import claude --keychain" in output


def test_import_keychain_stores_the_sign_in(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "arena.cli_gateway.read_claude_keychain",
        lambda environ: SignIn({".claude/.credentials.json": CLAUDE_CREDENTIALS}),
    )
    accounts_home = tmp_path / "h"
    code, output = _invoke(
        ["providers", "import", "claude", "--keychain", "--home", str(accounts_home)], home
    )
    assert code == 0, output
    assert TOKEN not in output
    stored = AccountStore(accounts_home / "accounts").load("claude")
    assert stored is not None and stored.files == {".claude/.credentials.json": CLAUDE_CREDENTIALS}
    code, output = _invoke(["providers", "import", "codex", "--keychain"], home)
    assert code == 2
    code, output = _invoke(["providers", "import", "claude"], home)
    assert code == 2 and "--from" in output


def test_add_api_provider_checks_the_variable_without_reading_it(home: Path) -> None:
    secret = "sk-SECRET-key-value"
    config = ["--config", "providers.yaml"]
    code, output = _invoke(
        ["providers", "add", "openai", "--env", "OPENAI_API_KEY", *config],
        home,
        OPENAI_API_KEY=secret,
    )
    assert code == 0 and "OPENAI_API_KEY is set" in output and secret not in output
    assert "keys: [{id: main, env: OPENAI_API_KEY}]" in output
    code, output = _invoke(["providers", "add", "openai", "--env", "NOT_SET_VAR", *config], home)
    assert "is not set" in output
    code, output = _invoke(["providers", "add", "openai", "--env", "bad name", *config], home)
    assert code == 2
    code, output = _invoke(["providers", "add", "openai", *config], home)
    assert code == 2
    code, output = _invoke(["providers", "add", "openai-plus", "--env", "X", *config], home)
    assert code == 2 and "subscription provider" in output
    code, output = _invoke(["providers", "add", "nope", "--env", "X", *config], home)
    assert code == 2


def test_the_real_environment_is_not_modified() -> None:
    assert os.environ.get("ARENA_DISCOVER_SIGNINS") is None


def test_claude_sign_in_follows_claude_config_dir(tmp_path: Path) -> None:
    config = tmp_path / "cfg"
    config.mkdir()
    (config / ".credentials.json").write_bytes(CLAUDE_CREDENTIALS)
    sign_in = claude_sign_in({"HOME": str(tmp_path / "other"), "CLAUDE_CONFIG_DIR": str(config)})
    assert sign_in.files == {".claude/.credentials.json": CLAUDE_CREDENTIALS}
