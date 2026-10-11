"""Accounts for `local-cli` subscription providers: stored, discovered and imported sign-ins."""

from __future__ import annotations

from collections.abc import Mapping

from arena.agents.local.signin import SignInError
from arena.agents.local.types import SignIn
from arena.providers.accounts.discovery import (
    DISCOVERY_SWITCH,
    FoundSignIn,
    discover,
    discovery_enabled,
    read_existing,
)
from arena.providers.accounts.keychain import KeychainError, read_claude_keychain
from arena.providers.accounts.login import LoginError, run_vendor_login
from arena.providers.accounts.store import (
    AccountError,
    AccountStore,
    StoredAccount,
    account_label,
    redact_id,
)


def resolve_sign_in(vendor: str, store: AccountStore, environ: Mapping[str, str]) -> SignIn:
    """The sign-in a trial uses: the stored copy, else the CLI's own file if discovery is on."""
    stored = store.load(vendor)
    if stored is not None and stored.files:
        return stored
    if discovery_enabled(environ):
        return read_existing(vendor, environ)
    raise SignInError(
        f"no stored {vendor} sign-in and discovery is off; run `arena providers add` "
        f"or unset {DISCOVERY_SWITCH}"
    )


__all__ = [
    "DISCOVERY_SWITCH",
    "AccountError",
    "AccountStore",
    "FoundSignIn",
    "KeychainError",
    "LoginError",
    "StoredAccount",
    "account_label",
    "discover",
    "discovery_enabled",
    "read_claude_keychain",
    "read_existing",
    "redact_id",
    "resolve_sign_in",
    "run_vendor_login",
]
