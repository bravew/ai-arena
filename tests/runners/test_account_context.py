"""Account-scoped execution context: sign-in snapshot, private lane keys and shared rests.

These exercise the `local-cli` scheduler inputs directly, with no vendor CLI and no model call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from arena.agents.local.types import SignIn
from arena.core.modelref import ModelRef
from arena.core.models import Contestant, Task
from arena.gateway.classify import FailureClass
from arena.providers.accounts import AccountStore
from arena.providers.config import Provider
from arena.runners.account_context import (
    RunScopedRestBook,
    SignInSnapshot,
    account_key,
    plan_lanes,
    provider_concurrency,
    snapshot_sign_ins,
)
from arena.runners.completion import CompletionResult
from arena.runners.scheduler import TrialJob, run_jobs

CODEX = ".codex/auth.json"
CLAUDE = ".claude/.credentials.json"
# The reference fixtures use the same JSON shape; this is a non-secret synthetic account id.
ACCOUNT_A = b'{"tokens": {"account_id": "acct-a", "access_token": "x"}}'
ACCOUNT_B = b'{"tokens": {"account_id": "acct-b", "access_token": "y"}}'


def _provider(provider_id: str, vendor: str, concurrency: int) -> Provider:
    return Provider.model_validate(
        {
            "id": provider_id,
            "keys": [],
            "subscription": {"vendor": vendor, "plan": "max", "import": "own-sign-in"},
            "limits": {"concurrency": concurrency},
        }
    )


def _snapshot(vendor: str, files: Mapping[str, bytes]) -> SignInSnapshot:
    return SignInSnapshot({vendor: SignIn(files)}, {})


class _FakeStore(AccountStore):
    """An account store that serves one fixed sign-in without touching the filesystem."""

    def __init__(self, files: Mapping[str, bytes]) -> None:
        super().__init__(Path("/does/not/exist"))
        self._files = files

    def load(self, vendor: str) -> SignIn | None:
        return SignIn(self._files) if vendor == "chatgpt" else None


def test_account_key_is_vendor_scoped_framed_and_full_length() -> None:
    a = SignIn({CODEX: ACCOUNT_A})
    b = SignIn({CODEX: ACCOUNT_B})

    key = account_key("chatgpt", a)

    assert len(key) == 64  # a full SHA-256, not the 8-character redacted display label
    assert key == account_key("chatgpt", SignIn({CODEX: ACCOUNT_A}))  # stable for the same bytes
    assert key != account_key("chatgpt", b)  # different account, different lane
    assert key != account_key("claude", a)  # the vendor is part of the identity

    # The path is framed, so moving bytes between paths does not collide with a same-length swap.
    assert account_key("chatgpt", SignIn({"a": b"xy"})) != account_key(
        "chatgpt", SignIn({"ax": b"y"})
    )


def test_snapshot_resolves_once_and_freezes_the_same_bytes() -> None:
    store = AccountStore(Path("/does/not/exist"))  # a stored copy wins without any file read

    class CountingStore(AccountStore):
        def __init__(self) -> None:
            super().__init__(Path("/does/not/exist"))
            self.loads = 0

        def load(self, vendor: str) -> SignIn | None:
            self.loads += 1
            return SignIn({CODEX: ACCOUNT_A})

    counting = CountingStore()
    snapshot = snapshot_sign_ins(["chatgpt", "chatgpt"], counting, {})

    assert counting.loads == 1  # resolved once per vendor, not once per alias
    first = snapshot.for_vendor("chatgpt")
    second = snapshot.for_vendor("chatgpt")
    assert first is second  # the same frozen object every trial copies
    assert first.files[CODEX] == ACCOUNT_A
    assert store.load("chatgpt") is None  # the unused store is untouched


def test_snapshot_records_a_missing_sign_in_as_an_error() -> None:
    snapshot = snapshot_sign_ins(["chatgpt"], AccountStore(Path("/does/not/exist")), {})

    assert snapshot.error_for("chatgpt") is not None
    assert snapshot.error_for("claude") is None


def test_a_sign_in_change_after_the_snapshot_does_not_move_the_lane() -> None:
    snapshot = snapshot_sign_ins(
        ["chatgpt"],
        _FakeStore({CODEX: ACCOUNT_A}),
        {},
    )
    frozen = snapshot.for_vendor("chatgpt")
    original = account_key("chatgpt", frozen)

    # A later read returns a refreshed token for the same account; the snapshot must not follow it.
    changed = _FakeStore({CODEX: b'{"tokens": {"account_id": "acct-a", "access_token": "new"}}'})
    refreshed = changed.load("chatgpt")
    assert refreshed is not None
    assert account_key("chatgpt", refreshed) != original
    assert snapshot.for_vendor("chatgpt").files[CODEX] == ACCOUNT_A
    assert account_key("chatgpt", snapshot.for_vendor("chatgpt")) == original


def test_alias_providers_share_a_lane_at_the_minimum_concurrency() -> None:
    snapshot = _snapshot("chatgpt", {CODEX: ACCOUNT_A})
    fast = _provider("codex-fast", "chatgpt", 4)
    slow = _provider("codex-slow", "chatgpt", 1)
    other = _provider("codex-other", "chatgpt", 8)

    # The alias order is reversed on purpose: the lane must not depend on which one is seen first.
    lanes = plan_lanes([fast, slow, other], snapshot)

    assert lanes["codex-fast"] == lanes["codex-slow"]
    assert lanes["codex-fast"][1] == 1  # both aliases drop to the lowest configured limit
    assert lanes["codex-other"][0] == lanes["codex-fast"][0]  # same account, same key
    assert lanes["codex-other"][1] == 1


def test_lane_lookup_is_pure_and_reverse_order_is_still_capped() -> None:
    snapshot = _snapshot("chatgpt", {CODEX: ACCOUNT_A})
    providers = [_provider("a", "chatgpt", 3), _provider("b", "chatgpt", 1)]
    lanes = plan_lanes(providers, snapshot)
    # A pure mapping lookup must not touch the snapshot or any provider state.
    assert lanes.get("missing") is None
    assert lanes["a"] == lanes["b"]

    running = peak = 0

    async def dispatch(_job: TrialJob) -> CompletionResult:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return CompletionResult(text="ok")

    jobs = [
        TrialJob(
            contestant=Contestant(model=ModelRef.parse(f"{alias}/m")),
            task=Task(id="t", version=1, kind="content", prompt_file="prompt.md"),
            repeat=1,
        )
        for alias in ("a", "b")
    ]
    summary = asyncio.run(
        run_jobs(
            None,
            "lanes",
            jobs,
            dispatch,
            lane_for=lambda job: lanes.get(job.contestant.model.provider),
        )
    )
    assert summary.completed == 2
    assert peak == 1  # the shared account lane serializes both aliases


def test_run_scoped_rest_is_shared_across_aliases_and_does_not_change_gateway_defaults() -> None:
    snapshot = _snapshot("chatgpt", {CODEX: ACCOUNT_A})
    lanes = plan_lanes([_provider("a", "chatgpt", 1), _provider("b", "chatgpt", 1)], snapshot)
    key = lanes["a"][0]
    assert key == lanes["b"][0]

    later = datetime.now(UTC) + timedelta(days=30)

    book = RunScopedRestBook()
    rest = book.rest(key, FailureClass.SUBSCRIPTION_LIMIT)
    assert rest is not None
    assert book.is_resting(key)
    assert book.is_resting(lanes["b"][0])  # the alias shares the run-long rest
    assert book.is_resting(key, now=later)  # it outlasts any single trial, not a 30-minute pause

    # The gateway's own defaults are untouched: a plain book rests for a bounded time only.
    from arena.gateway.rests import RestBook

    plain = RestBook()
    plain_rest = plain.rest("k", FailureClass.SUBSCRIPTION_LIMIT)
    assert plain_rest is not None
    assert not plain.is_resting("k", now=later)  # the default rest has long since expired


def test_provider_concurrency_defaults_to_one_and_ignores_bad_values() -> None:
    assert provider_concurrency(_provider("a", "chatgpt", 5)) == 5
    assert provider_concurrency(_provider("a", "chatgpt", 0)) == 1
