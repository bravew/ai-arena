"""Resolve a provider protocol to its endpoint and trial-scoped credential."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from arena.gateway.resolve import Target
from arena.providers.config import ProviderKey
from arena.providers.subscriptions import SubscriptionError, SubscriptionStore, sign_request


class ProviderEndpointError(Exception):
    """The selected provider candidate has no safe, usable endpoint configuration."""


@dataclass(frozen=True, slots=True)
class ProviderEndpoint:
    """Where to send one call and how to authenticate it.

    API-key providers carry `api_key`; subscription providers carry vendor-signed
    `auth_headers` and the `account_id` they belong to. Neither shows in `repr`.
    """

    protocol: str
    base_url: str
    key_id: str
    api_key: str = field(repr=False)
    auth_headers: Mapping[str, str] | None = field(default=None, repr=False)
    account_id: str | None = None


class EndpointResolver:
    """Resolve exactly one configured candidate without consulting user config files."""

    def __init__(
        self,
        *,
        subscriptions: SubscriptionStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.subscriptions = subscriptions
        self.clock = clock

    def resolve(
        self,
        target: Target,
        *,
        protocol: str,
        candidate: ProviderKey | None = None,
        account_id: str | None = None,
    ) -> ProviderEndpoint:
        extra: dict[str, Any] = target.provider.model_extra or {}
        apis = extra.get("apis")
        if not isinstance(apis, dict):
            raise ProviderEndpointError("provider has no configured API endpoints")
        api_map = cast(dict[str, Any], apis)
        base_url = api_map.get(protocol)
        if not isinstance(base_url, str) or not base_url.startswith(("https://", "http://")):
            raise ProviderEndpointError(f"provider has no endpoint for protocol {protocol!r}")

        if target.provider.kind == "mock":
            return ProviderEndpoint(protocol, base_url, "mock", "")
        if target.provider.subscription is not None:
            return self._subscription(target, protocol, base_url.rstrip("/"), account_id)
        if account_id is not None:
            raise ProviderEndpointError("account selection applies only to subscription providers")

        candidates = tuple(target.provider.keys)
        selected = candidate
        if selected is None and len(candidates) == 1:
            selected = candidates[0]
        if selected is not None and selected not in candidates:
            raise ProviderEndpointError("selected candidate does not belong to the provider")
        if selected is None:
            if not candidates:
                raise ProviderEndpointError("provider has no API key candidate")
            raise ProviderEndpointError("provider candidate is ambiguous or unavailable")
        api_key = os.environ.get(selected.env)
        if not api_key:
            raise ProviderEndpointError("provider credential is unavailable")
        return ProviderEndpoint(protocol, base_url.rstrip("/"), selected.id, api_key)

    def _subscription(
        self, target: Target, protocol: str, base_url: str, account_id: str | None
    ) -> ProviderEndpoint:
        subscription = target.provider.subscription
        assert subscription is not None
        if self.subscriptions is None:
            raise ProviderEndpointError("subscription accounts are not configured")
        try:
            account = self.subscriptions.load(subscription.vendor)
        except SubscriptionError as error:
            raise ProviderEndpointError(
                f"{subscription.vendor} subscription account is not imported"
            ) from error
        if account_id is not None and account.account_id != account_id:
            raise ProviderEndpointError("selected account does not belong to the provider")
        if account.expires_at is not None and account.expires_at <= self.clock():
            raise ProviderEndpointError(
                f"{account.display_name} access token has expired; sign in again and re-import"
            )
        try:
            headers = sign_request(account, {})
        except SubscriptionError as error:
            raise ProviderEndpointError(str(error)) from error
        return ProviderEndpoint(
            protocol, base_url, account.account_id, "", headers, account.account_id
        )
