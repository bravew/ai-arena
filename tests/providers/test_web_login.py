"""Fake vendor CLIs exercise login lifecycle; no OAuth or real Keychain is used."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from arena.agents.local.types import SignIn
from arena.catalog.config import CatalogModel
from arena.core.models import Contestant
from arena.providers.accounts.keychain import (
    delete_scoped_claude_keychain,
    read_scoped_claude_keychain,
    scoped_claude_service,
)
from arena.providers.accounts.probe import detect_account, subscription_auth, valid_sign_in
from arena.providers.accounts.web_login import LoginManager, safe_login_url
from arena.providers.config import Provider
from arena.providers.runtimes import RuntimeStatus, spec_for_vendor
from arena.runners.account_context import SignInSnapshot
from arena.runners.local_cli_trial import preflight_local_cli

AUTH = json.dumps(
    {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "access_token": "SECRET-access",
            "refresh_token": "SECRET-refresh",
            "id_token": "SECRET-id",
            "account_id": "SECRET-account",
        },
    }
)
CLAUDE = json.dumps({"claudeAiOauth": {"accessToken": "SECRET-access"}})
CLAUDE_URL = (
    "https://claude.ai/oauth/authorize?client_id=client&response_type=code&"
    "redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&"
    "scope=user%3Ainference&state=nonce&code_challenge=challenge&code_challenge_method=S256"
)


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\nPATH=/usr/bin:/bin\n" + body + "\n")
    path.chmod(0o700)
    return path


@pytest.fixture(autouse=True)
def fake_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    # New private login cleanup is exercised separately with a fake security binary.
    monkeypatch.setattr(
        "arena.providers.accounts.web_login.delete_scoped_claude_keychain", lambda *_: None
    )

    def reject(*_: object) -> SignIn:
        raise AssertionError("test attempted a real Keychain read")

    monkeypatch.setattr("arena.providers.accounts.web_login.read_scoped_claude_keychain", reject)


def fake_cli(tmp_path: Path, vendor: str, login: str, version: str | None = None) -> Path:
    root = tmp_path / vendor
    root.mkdir(exist_ok=True)
    if vendor == "chatgpt":
        status = """if [ -f "$CODEX_HOME/auth.json" ]; then
 echo 'Logged in using ChatGPT'; else echo 'Not logged in'; exit 1; fi"""
        prefix = '[ "$1 $2" = "login status" ]'
    else:
        status = """if [ -f "$CLAUDE_CONFIG_DIR/.credentials.json" ]; then
 echo '{"loggedIn":true,"authMethod":"oauth_token"}'; else
 echo '{"loggedIn":false,"authMethod":"none"}'; fi"""
        prefix = '[ "$1 $2" = "auth status" ]'
    version = version or ("2.1.296" if vendor == "claude" else "0.162.1")
    return script(
        root / "cli",
        f"""
if [ "$1" = "--version" ]; then echo '{version}'; exit; fi
if {prefix}; then {status}; exit; fi
printf '%s\\n' "$*" > '{root / "argv"}'
printf '%s\\n' "$HOME" > '{root / "home"}'
env > '{root / "env"}'
{login}
""",
    )


def env(tmp_path: Path, binary: Path, vendor: str) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "host"),
        "USER": "test-username",
        "PATH": "/usr/bin:/bin",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "host-claude"),
        "CODEX_HOME": str(tmp_path / "host-codex"),
        "ANTHROPIC_API_KEY": "SECRET-host-key",
        "OPENAI_API_KEY": "SECRET-host-key",
        "CLAUDE_SECURESTORAGE_CONFIG_DIR": str(tmp_path / "host-keychain"),
        "CLAUDE_CODE_PATH" if vendor == "claude" else "CODEX_CLI_PATH": str(binary),
    }


def wait(manager: LoginManager, id: str, states: set[str], seconds: float = 4) -> dict[str, str]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = manager.status(id)
        assert "SECRET" not in repr(result)
        if result["state"] in states:
            return result
        time.sleep(0.01)
    pytest.fail(f"login did not reach {states}: {manager.status(id)}")


def finished(manager: LoginManager, id: str) -> dict[str, str]:
    return wait(manager, id, {"succeeded", "failed", "cancelled", "expired"})


def test_device_success_is_private_validated_and_scrubbed(tmp_path: Path) -> None:
    binary = fake_cli(
        tmp_path,
        "chatgpt",
        f"""
printf 'https://auth.openai.com/codex/device\\nABCD-EFGH\\n'
sleep .2
printf '%s' '{AUTH}' > "$CODEX_HOME/auth.json"
echo SECRET-access >&2
""",
    )
    manager = LoginManager(tmp_path / "arena", env(tmp_path, binary, "chatgpt"))
    try:
        job = manager.start("chatgpt")
        ready = wait(manager, job["id"], {"awaiting_device"})
        assert ready["url"] == "https://auth.openai.com/codex/device"
        wait(manager, job["id"], {"awaiting_device"})
        done = finished(manager, job["id"])
        assert done["state"] == "succeeded"
        assert set(done) == {"id", "vendor", "state", "message"}
        sign_in = manager.store.load("chatgpt")
        assert sign_in and sign_in.files == {".codex/auth.json": AUTH.encode()}
        root = binary.parent
        assert (root / "argv").read_text().strip() == (
            'login --device-auth -c cli_auth_credentials_store="file"'
        )
        private_home = Path((root / "home").read_text().strip())
        assert not private_home.exists()
        child_env = (root / "env").read_text()
        assert "SECRET" not in child_env and "host-codex" not in child_env
        assert not (tmp_path / "host").exists()
    finally:
        manager.close()


def test_claude_browser_fallback_and_code_stdin(tmp_path: Path) -> None:
    binary = fake_cli(
        tmp_path,
        "claude",
        f"""
"$BROWSER" '{CLAUDE_URL}'
read code
[ "$code" = 'authorization-code#nonce' ] || exit 21
printf '%s' '{CLAUDE}' > "$CLAUDE_CONFIG_DIR/.credentials.json"
""",
    )
    manager = LoginManager(tmp_path / "arena", env(tmp_path, binary, "claude"))
    try:
        job = manager.start("claude")
        ready = wait(manager, job["id"], {"awaiting_code"})
        assert ready["url"] == CLAUDE_URL
        with pytest.raises(ValueError, match="invalid"):
            manager.submit_code(job["id"], "bad\nSECRET-token")
        result = manager.submit_code(job["id"], "authorization-code#nonce")
        assert result["id"] == job["id"] and "code" not in result
        assert finished(manager, job["id"])["state"] == "succeeded"
        assert (binary.parent / "argv").read_text().strip() == "auth login --claudeai"
        child_env = (binary.parent / "env").read_text()
        home = (binary.parent / "home").read_text().strip()
        assert f"CLAUDE_SECURESTORAGE_CONFIG_DIR={home}/.claude" in child_env
        assert "USER=test-username" in child_env
        assert manager.store.get("claude") is not None
    finally:
        manager.close()


@pytest.mark.parametrize("vendor", ["claude", "chatgpt"])
def test_cancel_and_shutdown_remove_private_homes(tmp_path: Path, vendor: str) -> None:
    binary = fake_cli(tmp_path, vendor, "sleep 60")
    manager = LoginManager(tmp_path / "arena", env(tmp_path, binary, vendor))
    job = manager.start(vendor)
    deadline = time.monotonic() + 3
    while not (binary.parent / "home").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.start(vendor)["id"] == job["id"]
    home = Path((binary.parent / "home").read_text().strip())
    before = time.monotonic()
    if vendor == "claude":
        result = manager.cancel(job["id"])
        assert result["state"] == "cancelled"
    else:
        manager.close()
    assert time.monotonic() - before < 3
    assert manager.status(job["id"])["state"] == "cancelled"
    assert not home.exists() and manager.store.load(vendor) is None
    manager.close()


def test_timeout_and_output_overflow_are_bounded(tmp_path: Path) -> None:
    for index, body in enumerate(["sleep 60", "yes SECRET-token"]):
        root = tmp_path / str(index)
        root.mkdir()
        binary = fake_cli(root, "chatgpt", body)
        manager = LoginManager(root / "arena", env(root, binary, "chatgpt"), timeout=0.3)
        try:
            done = finished(manager, manager.start("chatgpt")["id"])
            assert done["state"] == ("expired" if index == 0 else "failed")
            assert manager.store.load("chatgpt") is None
            assert not Path((binary.parent / "home").read_text().strip()).exists()
        finally:
            manager.close()


def test_version_floor_checked_before_login(tmp_path: Path) -> None:
    binary = fake_cli(tmp_path, "claude", "exit 44", version="2.1.295")
    manager = LoginManager(tmp_path / "arena", env(tmp_path, binary, "claude"))
    try:
        assert finished(manager, manager.start("claude")["id"])["state"] == "failed"
        assert not (binary.parent / "argv").exists()
    finally:
        manager.close()


@pytest.mark.parametrize(
    "credentials",
    [
        '{"auth_mode":"apikey","OPENAI_API_KEY":"SECRET"}',
        '{"auth_mode":"chatgpt","tokens":{"access_token":"SECRET"}}',
        "SECRET-token user@example.com",
    ],
)
def test_success_exit_without_valid_subscription_credentials_fails(
    tmp_path: Path,
    credentials: str,
) -> None:
    binary = fake_cli(
        tmp_path, "chatgpt", f"printf '%s' '{credentials}' > \"$CODEX_HOME/auth.json\""
    )
    manager = LoginManager(tmp_path / "arena", env(tmp_path, binary, "chatgpt"))
    try:
        assert finished(manager, manager.start("chatgpt")["id"])["state"] == "failed"
        assert manager.store.load("chatgpt") is None
    finally:
        manager.close()


def test_cancel_vendor_preserves_saved_account_and_blocks_late_save(tmp_path: Path) -> None:
    binary = fake_cli(
        tmp_path, "chatgpt", f"sleep 60; printf '%s' '{AUTH}' > \"$CODEX_HOME/auth.json\""
    )
    manager = LoginManager(tmp_path / "arena", env(tmp_path, binary, "chatgpt"))
    old = SignIn({".codex/auth.json": AUTH.encode()})
    manager.store.save("chatgpt", old, "existing")
    job = manager.start("chatgpt")
    manager.cancel_vendor("chatgpt")
    assert manager.status(job["id"])["state"] == "cancelled"
    assert manager.store.get("chatgpt").source == "existing"  # type: ignore[union-attr]
    assert manager.disconnect("chatgpt")
    assert not manager.disconnect("chatgpt")
    with pytest.raises(ValueError):
        manager.disconnect("../claude")
    manager.close()
    with pytest.raises(ValueError, match="closed"):
        manager.start("chatgpt")


@pytest.mark.parametrize(
    "url",
    [
        "http://claude.ai/oauth/authorize",
        "https://claude.ai.evil.test/oauth/authorize",
        "https://user:pass@claude.ai/oauth/authorize",
        "https://claude.ai:444/oauth/authorize",
        "https://claude.ai/logout",
        "https://auth.openai.com/codex/device?access_token=SECRET",
        "https://claude.ai/oauth/authorize?state=user%40example.com",
        "https://claude.ai/oauth/authorize?state=one&state=two",
        "https://claude.ai/oauth/authorize#SECRET",
        "https://claude.ai/oauth/authorize\n",
        "https://claude.ai/oauth/authorize?redirect_uri=https%3A%2F%2Fevil.test",
    ],
)
def test_url_parser_rejects_nonvendor_and_secret_fields(url: str) -> None:
    assert safe_login_url("claude", url) is None
    assert safe_login_url("chatgpt", url) is None


def test_scoped_keychain_read_delete_exact_item_only(tmp_path: Path) -> None:
    config = tmp_path / "private" / ".claude"
    service = scoped_claude_service(config)
    assert (
        service == "Claude Code-credentials-" + hashlib.sha256(str(config).encode()).hexdigest()[:8]
    )
    security = script(
        tmp_path / "security",
        f"""
[ "$3" = '{service}' ] || exit 9
[ "$4 $5" = '-a test-username' ] || exit 9
printf '%s\\n' "$*" >> '{tmp_path / "calls"}'
if [ "$1" = find-generic-password ]; then printf '%s' '{CLAUDE}'; fi
""",
    )
    environ = {"USER": "test-username", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    read = read_scoped_claude_keychain(config, environ, security=str(security))
    assert valid_sign_in("claude", read)
    delete_scoped_claude_keychain(config, environ, security=str(security))
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls == [
        f"find-generic-password -s {service} -a test-username -w",
        f"delete-generic-password -s {service} -a test-username",
    ]


def test_selected_account_probe_and_preflight_ignore_host_signed_out(tmp_path: Path) -> None:
    binary = fake_cli(tmp_path, "claude", "exit 88")
    environ = env(tmp_path, binary, "claude")
    sign_in = SignIn({".claude/.credentials.json": CLAUDE.encode()})
    spec = spec_for_vendor("claude")
    assert spec is not None
    report = detect_account(spec, sign_in, environ)
    assert report.status is RuntimeStatus.OK
    assert "SECRET" not in repr(report)
    provider = Provider.model_validate(
        {
            "id": "plan",
            "keys": [],
            "subscription": {
                "vendor": "claude",
                "plan": "max",
                "import": "own-sign-in",
                "runtime": "local-cli",
            },
        }
    )
    contestant = Contestant.model_validate(
        {
            "label": "selected",
            "model": {"provider": "plan", "model": "claude-opus-5-5"},
            "scaffold": None,
            "orchestration": "single",
        }
    )
    model = CatalogModel.model_validate(
        {
            "ref": "plan/claude-opus-5-5",
            "protocols": ["anthropic"],
            "pricing": "subscription",
        }
    )
    assert (
        preflight_local_cli(
            contestant,
            provider,
            {str(model.ref): model},
            SignInSnapshot({"claude": sign_in}, {}),
            environ,
        )
        == []
    )
    assert not (tmp_path / "host").exists()


def test_probe_never_exposes_vendor_status_text(tmp_path: Path) -> None:
    binary = script(
        tmp_path / "cli",
        """
if [ "$1" = --version ]; then echo 0.162.1; else echo 'Logged in using SECRET-email@example.com'; fi
""",
    )
    spec = spec_for_vendor("chatgpt")
    assert spec is not None
    report = detect_account(
        spec, SignIn({".codex/auth.json": AUTH.encode()}), env(tmp_path, binary, "chatgpt")
    )
    assert report.status is RuntimeStatus.AUTH_REQUIRED
    assert "SECRET" not in repr(report) and "@" not in repr(report)


@pytest.mark.parametrize(
    ("vendor", "detail", "accepted"),
    [
        ("claude", "authMethod=claude.ai", True),
        ("claude", "authMethod=oauth_token", True),
        ("claude", "authMethod=api_key", False),
        ("claude", "authMethod=none", False),
        ("claude", None, False),
        ("chatgpt", "Logged in using ChatGPT", True),
        ("chatgpt", "Logged in using an API key", False),
    ],
)
def test_subscription_auth_accepts_only_subscription_sign_ins(
    vendor: str, detail: str | None, accepted: bool
) -> None:
    assert subscription_auth(vendor, detail) is accepted
