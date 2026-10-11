from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arena.providers.subscriptions import (
    FailureKind,
    SubscriptionError,
    SubscriptionStore,
    classify_response,
    import_subscription,
    refresh_account,
    sign_request,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "subscriptions"


def test_imports_claude_own_sign_in_into_private_daemon_store(tmp_path: Path) -> None:
    source = FIXTURES / "claude.synthetic.json"
    store = SubscriptionStore(tmp_path / "accounts")

    account = import_subscription("claude", source, store)

    assert account.vendor == "claude"
    assert account.account_id
    assert account.access_token == "synthetic-claude-access"
    assert account.refresh_token == "synthetic-claude-refresh"
    assert account.terms_warning_required
    saved = tmp_path / "accounts" / "claude.json"
    assert saved.stat().st_mode & 0o777 == 0o600
    serialized = saved.read_text()
    assert "synthetic-claude-access" in serialized


def test_import_does_not_modify_source_sign_in(tmp_path: Path) -> None:
    source = FIXTURES / "chatgpt.synthetic.json"
    before = source.read_bytes()

    import_subscription("chatgpt", source, SubscriptionStore(tmp_path / "accounts"))

    assert source.read_bytes() == before


def test_missing_sign_in_is_named_and_does_not_create_store(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path / "accounts")
    with pytest.raises(SubscriptionError, match=r"copilot.*sign-in"):
        import_subscription("copilot", tmp_path / "missing.json", store)
    assert not (tmp_path / "accounts").exists()


def test_vendor_signing_adds_expected_headers_without_mutating_input(tmp_path: Path) -> None:
    cases = [
        (
            "claude",
            {"anthropic-version": "2023-06-01"},
            {"authorization": "Bearer synthetic-claude-access", "anthropic-version": "2023-06-01"},
        ),
        (
            "chatgpt",
            {},
            {
                "authorization": "Bearer synthetic-chatgpt-access",
                "chatgpt-account-id": "acct-synthetic-chatgpt",
                "openai-beta": "responses=experimental",
                "originator": "codex_cli_rs",
            },
        ),
        (
            "copilot",
            {},
            {
                "authorization": "token synthetic-copilot-access",
                "x-github-api-version": "2022-11-28",
            },
        ),
    ]
    for vendor, input_headers, expected in cases:
        account = import_subscription(
            vendor, FIXTURES / f"{vendor}.synthetic.json", SubscriptionStore(tmp_path / vendor)
        )
        original = dict(input_headers)
        assert sign_request(account, input_headers) == expected
        assert input_headers == original


def test_codex_rate_limit_allowed_false_is_subscription_limit() -> None:
    outcome = classify_response(
        "chatgpt", 200, b'{"rate_limit":{"allowed":false,"reset_at":1791504000}}'
    )
    assert outcome.kind is FailureKind.SUBSCRIPTION_LIMIT
    assert outcome.rest_until == datetime.fromtimestamp(1791504000, tz=UTC)
    assert outcome.account_scoped
    assert not outcome.counts_against_contestant


def test_subscription_limit_is_account_scoped_and_auth_refresh_disables_named_account() -> None:
    limited = classify_response(
        "copilot", 429, b'{"message":"weekly usage limit reached"}', account_id="acct-a"
    )
    sibling = classify_response("copilot", 200, b'{"choices":[]} ', account_id="acct-b")
    expired = classify_response(
        "claude", 401, b'{"error":{"message":"session expired"}}', account_id="acct-a"
    )

    assert limited.kind is FailureKind.SUBSCRIPTION_LIMIT
    assert limited.account_id == "acct-a"
    assert sibling.kind is None
    assert expired.kind is FailureKind.AUTH_REFRESH
    assert expired.disable_account
    assert expired.account_id == "acct-a"
    assert expired.user_message is not None
    assert "acct-a" in expired.user_message


def test_refresh_failure_persists_disabled_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SubscriptionStore(tmp_path / "accounts")
        account = import_subscription("claude", FIXTURES / "claude.synthetic.json", store)

        async def refresh(_account):
            raise SubscriptionError("refresh rejected")

        updated = await refresh_account(account, store, refresh)
        assert updated.disabled
        assert updated.disabled_reason == "auth_refresh"
        assert "claude" in updated.display_name

    asyncio.run(scenario())


def test_malformed_sign_in_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    source.write_text("{not json")
    with pytest.raises(SubscriptionError, match="cannot read claude sign-in"):
        import_subscription("claude", source, SubscriptionStore(tmp_path / "accounts"))
    assert not (tmp_path / "accounts").exists()
