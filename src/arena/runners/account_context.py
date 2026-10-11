"""Account-scoped execution context for `local-cli` trials.

A vendor CLI trial runs on one subscription account. Everything that decides *which* account a
trial uses, how many may run at once and when the account must rest is derived here, once, before
the scheduler starts:

- the sign-in for each vendor is resolved once and frozen, so every trial in the run copies the
  same bytes and a token refresh mid-run cannot split the run across two accounts;
- providers that resolve to the same account (aliases) share one lane at the lowest configured
  concurrency and one run-scoped rest;
- the lane and rest key is a full-length, private, vendor-scoped hash of the sign-in. It is never
  the redacted display id, and it never leaves the process.

The hash and the bytes are private: nothing here writes them to a record, a log or an event.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import cast

from arena.agents.local import SignIn, SignInError
from arena.gateway.classify import FailureClass
from arena.gateway.rests import Rest, RestBook
from arena.providers.accounts import AccountStore, resolve_sign_in
from arena.providers.config import Provider


def provider_concurrency(provider: Provider) -> int:
    """`limits.concurrency` for the account, default 1 so a lone account is never oversubscribed."""
    limits: object = (provider.model_extra or {}).get("limits")
    value: object = (
        cast("dict[str, object]", limits).get("concurrency") if isinstance(limits, dict) else None
    )
    return value if isinstance(value, int) and value >= 1 else 1


def _frame(chunk: bytes) -> bytes:
    """Length-prefix a chunk so framed path/content pairs cannot be re-split ambiguously."""
    return len(chunk).to_bytes(8, "big") + chunk


def account_key(vendor: str, sign_in: SignIn) -> str:
    """A full-length, private, vendor-scoped key for the account a sign-in belongs to.

    The key frames the vendor, then each relative path and its bytes, so two different sign-ins
    never collide and the same bytes always key the same lane and rest. It is a hash, so it cannot
    be read back into an identity, and it is longer than the redacted display label on purpose.
    """
    digest = hashlib.sha256()
    digest.update(_frame(vendor.encode("utf-8")))
    for path, content in sorted(sign_in.files.items()):
        digest.update(_frame(path.encode("utf-8")))
        digest.update(_frame(content))
    return digest.hexdigest()


@dataclass(frozen=True)
class SignInSnapshot:
    """The sign-ins a run resolved before scheduling, frozen for the whole run."""

    sign_ins: Mapping[str, SignIn] = field(repr=False)
    errors: Mapping[str, str] = field(repr=False)

    def __post_init__(self) -> None:
        frozen = {
            vendor: SignIn(MappingProxyType(dict(sign_in.files)))
            for vendor, sign_in in self.sign_ins.items()
        }
        object.__setattr__(self, "sign_ins", MappingProxyType(frozen))
        object.__setattr__(self, "errors", MappingProxyType(dict(self.errors)))

    @classmethod
    def empty(cls) -> SignInSnapshot:
        """No vendors resolved; used when a run has no `local-cli` contestants."""
        return cls({}, {})

    def for_vendor(self, vendor: str) -> SignIn:
        sign_in = self.sign_ins.get(vendor)
        if sign_in is None:
            raise SignInError(self.errors.get(vendor, f"no {vendor} sign-in"))
        return sign_in

    def error_for(self, vendor: str) -> str | None:
        return self.errors.get(vendor)


def snapshot_sign_ins(
    vendors: Iterable[str], store: AccountStore, environ: Mapping[str, str]
) -> SignInSnapshot:
    """Resolve each vendor's sign-in once, before scheduling, and freeze it for the whole run.

    A vendor with no usable sign-in is recorded as an error rather than raised, so preflight can
    report it as its own issue. Every trial on that vendor reads the same frozen `SignIn`.
    """
    sign_ins: dict[str, SignIn] = {}
    errors: dict[str, str] = {}
    for vendor in sorted(set(vendors)):
        try:
            sign_ins[vendor] = resolve_sign_in(vendor, store, environ)
        except SignInError as error:
            errors[vendor] = str(error)
    return SignInSnapshot(sign_ins, errors)


def plan_lanes(
    providers: Sequence[Provider], sign_ins: SignInSnapshot
) -> dict[str, tuple[str, int]]:
    """Map each provider id to its `(account key, normalized concurrency)` lane.

    Aliases that resolve to the same account key share one lane at the lowest configured
    concurrency, so the limit no longer depends on which alias the scheduler sees first. The
    scheduler then does a pure lookup instead of recomputing the lane per job.
    """
    keys: dict[str, str] = {}
    limits: dict[str, int] = {}
    for provider in providers:
        subscription = provider.subscription
        if subscription is None:
            continue
        key = account_key(subscription.vendor, sign_ins.for_vendor(subscription.vendor))
        keys[provider.id] = key
        limit = provider_concurrency(provider)
        limits[key] = min(limits.get(key, limit), limit)
    return {provider_id: (key, limits[key]) for provider_id, key in keys.items()}


class RunScopedRestBook(RestBook):
    """Rests for one `local-cli` run.

    A subscription limit rests the account for the rest of the run, as documented in
    `docs/subsystems/provider-runtimes.md`. The book is created per run and dropped on resume, so
    a rest simply lasts as long as the book is alive; the gateway keeps its own `RestBook` and its
    timed default, and is not changed.
    """

    def rest(
        self,
        key: str,
        failure: FailureClass,
        retry_after: str | None = None,
        *,
        now: datetime | None = None,
    ) -> Rest | None:
        if failure is not FailureClass.SUBSCRIPTION_LIMIT:
            return super().rest(key, failure, retry_after, now=now)
        result = Rest(key, failure, datetime.max.replace(tzinfo=UTC))
        with self._lock:
            self._rests[key] = result
            return result
