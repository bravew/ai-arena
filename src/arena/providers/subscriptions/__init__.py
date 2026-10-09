"""Daemon-side subscription account import, signing, refresh, and failure handling.

The module has no CLI or gateway-server dependency; callers provide those boundaries.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

Vendor = Literal["claude", "chatgpt", "copilot"]


class SubscriptionError(RuntimeError):
    """An account could not be safely imported or refreshed."""


class FailureKind(StrEnum):
    """Failure classes understood by the gateway candidate/rest planner."""

    SUBSCRIPTION_LIMIT = "subscription_limit"
    AUTH_REFRESH = "auth_refresh"


@dataclass(frozen=True, slots=True)
class SubscriptionAccount:
    vendor: Vendor
    account_id: str
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    display_name: str
    disabled: bool = False
    disabled_reason: str | None = None
    terms_warning_required: bool = True


@dataclass(frozen=True, slots=True)
class FailureOutcome:
    kind: str | None = None
    account_id: str | None = None
    rest_until: datetime | None = None
    account_scoped: bool = False
    disable_account: bool = False
    counts_against_contestant: bool = False
    user_message: str | None = None


Refresh = Callable[[SubscriptionAccount], Awaitable[SubscriptionAccount]]


class SubscriptionStore:
    """Store subscription credentials in a private daemon-owned directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.refresh_lock = asyncio.Lock()

    def save(self, account: SubscriptionAccount) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        path = self._path(account.vendor)
        payload = json.dumps(_serialize(account), sort_keys=True, separators=(",", ":"))
        fd, temporary = tempfile.mkstemp(prefix=f".{account.vendor}-", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except OSError:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
            raise

    def load(self, vendor: Vendor) -> SubscriptionAccount:
        path = self._path(vendor)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            account = _deserialize(raw)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise SubscriptionError(f"cannot read stored {vendor} subscription account") from error
        if account.vendor != vendor:
            raise SubscriptionError(f"stored account vendor mismatch for {vendor}")
        return account

    def _path(self, vendor: Vendor) -> Path:
        return self.directory / f"{vendor}.json"


def import_subscription(
    vendor: Vendor, sign_in_path: Path, store: SubscriptionStore
) -> SubscriptionAccount:
    """Read a supported native CLI sign-in and copy credentials to daemon storage."""
    try:
        raw = json.loads(sign_in_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SubscriptionError(f"cannot read {vendor} sign-in at {sign_in_path}") from error
    try:
        account = _parse_native_signin(vendor, raw)
    except (KeyError, TypeError, ValueError) as error:
        raise SubscriptionError(
            f"unsupported or incomplete {vendor} sign-in at {sign_in_path}"
        ) from error
    store.save(account)
    return account


def sign_request(account: SubscriptionAccount, headers: Mapping[str, str]) -> dict[str, str]:
    """Return vendor-client auth headers while preserving caller header input."""
    if account.disabled:
        raise SubscriptionError(account.disabled_reason or f"{account.display_name} is disabled")
    result = {str(key): str(value) for key, value in headers.items()}
    lower = {key.lower(): key for key in result}
    result.pop(lower.get("authorization", "authorization"), None)
    result["authorization"] = (
        f"token {account.access_token}"
        if account.vendor == "copilot"
        else f"Bearer {account.access_token}"
    )
    if account.vendor == "copilot":
        result.setdefault("x-github-api-version", "2022-11-28")
    elif account.vendor == "chatgpt":
        result.setdefault("chatgpt-account-id", account.account_id)
        result.setdefault("openai-beta", "responses=experimental")
        result.setdefault("originator", "codex_cli_rs")
    elif account.vendor == "claude":
        result.setdefault("anthropic-version", "2023-06-01")
    return result


def classify_response(
    vendor: Vendor,
    status: int,
    body: bytes,
    *,
    account_id: str | None = None,
    now: datetime | None = None,
) -> FailureOutcome:
    """Map vendor account failures into account-scoped gateway dispositions."""
    payload: Any = None
    with suppress(json.JSONDecodeError, UnicodeDecodeError):
        payload = json.loads(body)
    if vendor == "chatgpt" and isinstance(payload, dict):
        payload_map = cast(dict[str, Any], payload)
        rate_limit_value = payload_map.get("rate_limit")
        rate_limit: dict[str, Any] = (
            cast(dict[str, Any], rate_limit_value) if isinstance(rate_limit_value, dict) else {}
        )
        if rate_limit.get("allowed") is False:
            reset = rate_limit.get("reset_at")
            reset_at = (
                datetime.fromtimestamp(reset, tz=UTC) if isinstance(reset, int | float) else None
            )
            return FailureOutcome(
                kind=FailureKind.SUBSCRIPTION_LIMIT,
                account_id=account_id,
                rest_until=reset_at,
                account_scoped=True,
            )
    message = _message(payload).casefold()
    if status == 429 and any(
        term in message for term in ("weekly", "usage limit", "plan limit", "quota")
    ):
        reset_at = _reset_time(payload)
        return FailureOutcome(
            kind=FailureKind.SUBSCRIPTION_LIMIT,
            account_id=account_id,
            rest_until=reset_at,
            account_scoped=True,
        )
    if status == 401 or any(
        term in message for term in ("session expired", "invalid_grant", "token revoked")
    ):
        return FailureOutcome(
            kind=FailureKind.AUTH_REFRESH,
            account_id=account_id,
            account_scoped=True,
            disable_account=True,
            user_message=f"subscription account {account_id or 'unknown'} needs {vendor} sign-in",
        )
    return FailureOutcome(account_id=account_id)


async def refresh_account(
    account: SubscriptionAccount, store: SubscriptionStore, refresh: Refresh
) -> SubscriptionAccount:
    """Refresh one account; disable and persist it when refresh authentication fails."""
    async with store.refresh_lock:
        try:
            updated = await refresh(account)
        except SubscriptionError:
            updated = replace(account, disabled=True, disabled_reason=FailureKind.AUTH_REFRESH)
            store.save(updated)
            return updated
        if updated.vendor != account.vendor or updated.account_id != account.account_id:
            raise SubscriptionError("refresh changed subscription account identity")
        store.save(updated)
        return updated


def _parse_native_signin(vendor: Vendor, raw: Any) -> SubscriptionAccount:
    if not isinstance(raw, dict):
        raise TypeError("sign-in must be an object")
    raw = _as_mapping(raw)
    if raw.get("fixture_kind") == "synthetic_shape_only":
        credentials: Any = raw.get("credentials")
        if vendor == "claude" and isinstance(credentials, dict):
            oauth = _as_mapping(credentials)["claudeAiOauth"]
            oauth = _as_mapping(oauth) if isinstance(oauth, dict) else {}
            tokens = (oauth["accessToken"], oauth.get("refreshToken"), oauth.get("expiresAt"))
            account_id = _stable_account_id(
                vendor, str(raw.get("account", {}).get("email", "synthetic"))
            )
        elif vendor == "chatgpt":
            tokens = (
                raw["tokens"]["access_token"],
                raw["tokens"].get("refresh_token"),
                raw["tokens"].get("expires_at"),
            )
            account_id = str(raw["account_id"])
        elif vendor == "copilot":
            tokens = (raw["github_token"], None, raw.get("expires_at"))
            account_id = _stable_account_id(vendor, str(raw.get("user", "synthetic")))
        else:
            raise ValueError("fixture vendor shape mismatch")
    elif vendor == "claude":
        oauth = raw["claudeAiOauth"]
        tokens = (oauth["accessToken"], oauth.get("refreshToken"), oauth.get("expiresAt"))
        account_id = _stable_account_id(
            vendor, str(raw.get("oauthAccount", {}).get("emailAddress", "default"))
        )
    elif vendor == "chatgpt":
        tokens = (
            raw["tokens"]["access_token"],
            raw["tokens"].get("refresh_token"),
            raw["tokens"].get("expires_at"),
        )
        account_id = str(
            raw.get("tokens", {}).get("account_id")
            or raw.get("account_id")
            or _stable_account_id(vendor, "default")
        )
    else:
        tokens = (raw["oauth_token"], None, raw.get("expires_at"))
        account_id = _stable_account_id(vendor, str(raw.get("user", "default")))
    access, refresh_token, expiration = tokens
    if not isinstance(access, str) or not access:
        raise ValueError("missing access token")
    expires_at = _expiration(expiration)
    return SubscriptionAccount(
        vendor=vendor,
        account_id=account_id,
        access_token=access,
        refresh_token=refresh_token if isinstance(refresh_token, str) else None,
        expires_at=expires_at,
        display_name=f"{vendor} account {account_id}",
    )


def _stable_account_id(vendor: Vendor, identity: str) -> str:
    return f"{vendor}-{hashlib.sha256(identity.encode()).hexdigest()[:12]}"


def _expiration(value: Any) -> datetime | None:
    if isinstance(value, int | float):
        # Claude's local CLI stores milliseconds; other clients use Unix seconds.
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return None


def _as_mapping(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value)


def _message(payload: Any) -> str:
    if isinstance(payload, dict):
        payload_map = _as_mapping(payload)
        error = payload_map.get("error", payload_map)
        if isinstance(error, dict):
            error_map = _as_mapping(error)
            return " ".join(str(error_map.get(key, "")) for key in ("code", "type", "message"))
    return ""


def _reset_time(payload: Any) -> datetime | None:
    if isinstance(payload, dict):
        payload_map = _as_mapping(payload)
        for key in ("reset_at", "resets_at"):
            value = payload_map.get(key)
            if isinstance(value, str | int | float):
                try:
                    return _expiration(value)
                except (OverflowError, OSError, ValueError):
                    return None
    return None


def _serialize(account: SubscriptionAccount) -> dict[str, Any]:
    result = {
        "vendor": account.vendor,
        "account_id": account.account_id,
        "access_token": account.access_token,
        "refresh_token": account.refresh_token,
        "expires_at": account.expires_at.isoformat() if account.expires_at else None,
        "display_name": account.display_name,
        "disabled": account.disabled,
        "disabled_reason": account.disabled_reason,
        "terms_warning_required": account.terms_warning_required,
    }
    return result


def _deserialize(raw: Any) -> SubscriptionAccount:
    if not isinstance(raw, dict):
        raise TypeError("stored account must be an object")
    values = _as_mapping(raw)
    vendor = values["vendor"]
    account_id = values["account_id"]
    access_token = values["access_token"]
    display_name = values["display_name"]
    if vendor not in ("claude", "chatgpt", "copilot"):
        raise ValueError("unknown stored vendor")
    if not all(isinstance(value, str) for value in (account_id, access_token, display_name)):
        raise ValueError("invalid stored account identity")
    refresh_token = values.get("refresh_token")
    if refresh_token is not None and not isinstance(refresh_token, str):
        raise ValueError("invalid stored refresh token")
    disabled_reason = values.get("disabled_reason")
    if disabled_reason is not None and not isinstance(disabled_reason, str):
        raise ValueError("invalid stored disabled reason")
    expires_value = values.get("expires_at")
    if expires_value is not None and not isinstance(expires_value, str):
        raise ValueError("invalid stored expiration")
    expires_at = datetime.fromisoformat(expires_value) if isinstance(expires_value, str) else None
    return SubscriptionAccount(
        vendor=vendor,
        account_id=account_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        display_name=display_name,
        disabled=bool(values.get("disabled", False)),
        disabled_reason=disabled_reason,
        terms_warning_required=bool(values.get("terms_warning_required", True)),
    )
