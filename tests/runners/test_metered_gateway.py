from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from arena.core.models import Call
from arena.core.store import Store
from arena.runners.metered_gateway import RunGateway


def _call(*, run_id: str, trial_id: str, call_id: str, protocol: str = "anthropic") -> Call:
    return Call(
        id=call_id,
        seq=1,
        run_id=run_id,
        trial_id=trial_id,
        protocol_in=protocol,
        protocol_out=protocol,
        provider="anthropic",
        account_id="account-hash",
        model_asked="anthropic/claude-opus-5-5",
    )


def test_run_gateway_allocates_unique_contexts_concurrently(tmp_path: Path) -> None:
    async def scenario() -> None:
        with Store(tmp_path / "arena.db") as store:
            gateway = RunGateway("run-1", store)
            contexts = await asyncio.gather(
                *(asyncio.to_thread(gateway.context, f"trial-{index % 4}") for index in range(32))
            )
            assert sorted(context.seq for context in contexts) == list(range(1, 33))
            assert len({context.call_id for context in contexts}) == 32
            assert all(context.run_id == "run-1" for context in contexts)

    asyncio.run(scenario())


def test_run_gateway_reads_calls_only_for_the_token_trial(tmp_path: Path) -> None:
    async def scenario() -> None:
        with Store(tmp_path / "arena.db") as store:
            store.execute(
                "INSERT INTO runs(id, config_json, status) VALUES ('run-1', '{}', 'running')"
            )
            for trial_id in ("trial-1", "trial-2"):
                store.execute(
                    "INSERT INTO trials(id, run_id, contestant_id, task_id, status, attempt) "
                    "VALUES (?, 'run-1', ?, 'task', 'running', 1)",
                    (trial_id, trial_id),
                )
            gateway = RunGateway("run-1", store)
            call = _call(run_id="run-1", trial_id="trial-2", call_id="call-2")
            store.execute(
                "INSERT INTO calls(id, seq, run_id, trial_id, provider, account_id, model_asked, "
                "model_served, status, error_class, tokens_json, cost_usd, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    call.id,
                    call.seq,
                    call.run_id,
                    call.trial_id,
                    call.provider,
                    call.account_id,
                    call.model_asked,
                    call.model_served,
                    call.status,
                    call.error_class,
                    json.dumps(call.tokens.model_dump(mode="json")),
                    call.cost_usd,
                    json.dumps(call.model_dump(mode="json")),
                ),
            )
            assert await gateway.wait_for_call("arena-trial-2", "anthropic", 0.05)
            assert not await gateway.wait_for_call("arena-trial-1", "anthropic", 0.01)
            assert await gateway.calls("arena-trial-1") == []
            assert [row.id for row in await gateway.calls("arena-trial-2")] == ["call-2"]

    asyncio.run(scenario())


def test_run_gateway_allocates_context_and_rejects_nontrial_tokens(tmp_path: Path) -> None:
    with Store(tmp_path / "arena.db") as store:
        gateway = RunGateway("run-1", store)
        assert gateway.context("trial-1").cost_usd is None
        with pytest.raises(ValueError, match="trial"):
            asyncio.run(gateway.calls("arena-ops"))
