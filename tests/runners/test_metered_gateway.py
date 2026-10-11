from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from arena.catalog.config import load_catalog
from arena.core.models import Call
from arena.core.store import Store
from arena.gateway.run import RunContextProvider
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
        cost_usd=0.01,
    )


def test_run_context_provider_allocates_unique_attributed_calls() -> None:
    from arena.gateway.auth import Purpose
    from arena.gateway.resolve import resolve_target
    from arena.providers.config import ProviderConfig

    catalog = load_catalog(Path(__file__).parents[2] / "catalog/models.yaml")
    providers = ProviderConfig.model_validate(
        {"providers": [{"id": "anthropic", "keys": [{"id": "key", "env": "KEY"}]}]}
    )
    target = resolve_target("anthropic/claude-opus-5-5", providers, catalog)
    caller = type("Caller", (), {"purpose": Purpose.TRIAL, "trial_id": "trial-1"})()
    provider = RunContextProvider("run-1", {"trial-1": "anthropic/claude-opus-5-5"})
    provider.set_catalog(catalog)

    contexts = [provider(target, caller) for _ in range(4)]

    assert [context.seq for context in contexts if context is not None] == [1, 2, 3, 4]
    assert len({context.call_id for context in contexts if context is not None}) == 4
    assert all(
        context.run_id == "run-1" and context.cost_usd == 0 for context in contexts if context
    )


def test_run_gateway_reads_persisted_calls_and_completion_events_by_trial(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        event_root = tmp_path / "events"
        event_path = event_root / "run-1" / "events.jsonl"
        event_path.parent.mkdir(parents=True)
        call = _call(run_id="run-1", trial_id="trial-2", call_id="call-2")
        event_path.write_text(
            json.dumps(
                {
                    "seq": 1,
                    "run_id": "run-1",
                    "kind": "call_finished",
                    "ref": call.id,
                    "data": {"trial_id": "trial-2"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
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
            gateway = RunGateway("run-1", store, event_root)
            assert await gateway.wait_for_call("arena-trial-2", "anthropic", 0.05)
            assert not await gateway.wait_for_call("arena-trial-1", "anthropic", 0.01)
            assert await gateway.calls("arena-trial-1") == []
            assert [row.id for row in await gateway.calls("arena-trial-2")] == ["call-2"]
            assert [event["ref"] for event in gateway.call_events_for_trial("trial-2")] == [
                "call-2"
            ]

    asyncio.run(scenario())


def test_run_gateway_rejects_nontrial_tokens(tmp_path: Path) -> None:
    with Store(tmp_path / "arena.db") as store:
        gateway = RunGateway("run-1", store, tmp_path / "events")
        with pytest.raises(ValueError, match="trial"):
            asyncio.run(gateway.calls("arena-ops"))


def test_gateway_container_fails_on_missing_provider_credentials(tmp_path: Path) -> None:
    from arena.catalog.config import load_catalog
    from arena.providers.config import ProviderConfig
    from arena.runners.metered_gateway import GatewayContainer, GatewayRuntimeError

    provider_config = ProviderConfig.model_validate(
        {"providers": [{"id": "provider-x", "keys": [{"id": "main", "env": "ARENA_TEST_KEY"}]}]}
    )
    catalog_path = Path(__file__).parents[2] / "catalog/models.yaml"
    catalog = load_catalog(catalog_path)
    assert catalog.price_version
    container = GatewayContainer(
        project_root=tmp_path,
        run_id="run-1",
        trial_models={"trial-1": "provider-x/model"},
        providers_path=tmp_path / "providers.yaml",
        catalog_path=catalog_path,
        database_path=tmp_path / "arena.db",
        event_root=tmp_path / "events",
        subscriptions_root=tmp_path / "subscriptions",
        budget_usd=20,
        providers=provider_config,
        provider_ids={"provider-x"},
        docker=lambda *args, **kwargs: pytest.fail(
            "Docker must not start before credential preflight"
        ),
        port_factory=lambda: 34567,
    )
    assert container.url == "http://gateway:8765"
    with pytest.raises(GatewayRuntimeError, match="credential is unavailable"):
        container.preflight(["arena-agent:test"])
