from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from arena.catalog.config import ModelCatalog
from arena.core.models import Call, Tokens
from arena.core.store import Store
from arena.gateway.budget import Budget, BudgetExceeded
from arena.obs.events import EventLog
from arena.obs.ledger import CallsLedger


def _call(*, run_id: str = "r1", call_id: str = "c1") -> Call:
    return Call(
        id=call_id,
        seq=1,
        run_id=run_id,
        trial_id="t1",
        protocol_in="anthropic",
        protocol_out="anthropic",
        provider="anthropic",
        account_id="acct-hash",
        model_asked="anthropic/claude-opus-5-5",
        tokens=Tokens.model_validate({"in": 1_000_000, "out": 100_000}),
    )


def _catalog() -> ModelCatalog:
    return ModelCatalog.model_validate(
        {
            "price_version": "v1",
            "models": [
                {
                    "ref": "anthropic/claude-opus-5-5",
                    "price_per_mtok": {"in": 15, "out": 75},
                },
                {"ref": "anthropic/claude-opus-5-5-sub", "pricing": "subscription"},
                {"ref": "openrouter/qwen3-coder:free", "price_per_mtok": {"in": 0, "out": 0}},
            ],
        }
    )


def test_ledger_persists_full_call_and_prices_tokens(tmp_path: Path) -> None:
    with Store(tmp_path / "arena.db") as store:
        store.execute("INSERT INTO runs(id, config_json, status) VALUES ('r1', '{}', 'running')")
        store.execute(
            "INSERT INTO trials(id, run_id, contestant_id, task_id, status) "
            "VALUES ('t1', 'r1', 'contestant', 'task', 'running')"
        )
        ledger = CallsLedger(store, _catalog())
        recorded = ledger.record(_call())
        assert recorded.cost_usd == 22.5
        assert recorded.price_version == "v1"
        assert ledger.get("c1") == recorded
        row = store.execute("SELECT details_json FROM calls WHERE id='c1'").fetchone()
        assert '"protocol_in":"anthropic"' in row[0]


def test_ledger_preserves_colon_in_non_effort_model_name(tmp_path: Path) -> None:
    call = _call().model_copy(
        update={
            "id": "c3",
            "provider": "openrouter",
            "model_asked": "openrouter/qwen3-coder:free",
        }
    )
    with Store(tmp_path / "arena.db") as store:
        store.execute("INSERT INTO runs(id, config_json, status) VALUES ('r1', '{}', 'running')")
        store.execute(
            "INSERT INTO trials(id, run_id, contestant_id, task_id, status) "
            "VALUES ('t1', 'r1', 'contestant', 'task', 'running')"
        )
        ledger = CallsLedger(store, _catalog())
        assert ledger.record(call).cost_usd == 0


def test_subscription_cost_is_unknown_and_budget_only_counts_api_spend(tmp_path: Path) -> None:
    call = _call().model_copy(update={"id": "c2", "model_asked": "anthropic/claude-opus-5-5-sub"})
    with Store(tmp_path / "arena.db") as store:
        store.execute("INSERT INTO runs(id, config_json, status) VALUES ('r1', '{}', 'running')")
        store.execute(
            "INSERT INTO trials(id, run_id, contestant_id, task_id, status) "
            "VALUES ('t1', 'r1', 'contestant', 'task', 'running')"
        )
        ledger = CallsLedger(store, _catalog())
        recorded = ledger.record(call)
        assert recorded.cost_usd is None
        budget = Budget()
        asyncio.run(budget.record_call(recorded, EventLog(tmp_path / "events"), subscription=True))
        budget.record_compute_cost(1.25)
        report = budget.report()
        assert report.metered_spend_usd == 0
        assert report.subscription_tokens == 1_100_000
        assert report.compute_cost_usd == 1.25


def test_budget_reserves_spend_for_concurrent_calls(tmp_path: Path) -> None:
    async def run() -> None:
        budget = Budget()
        events = EventLog(tmp_path / "events")
        accepted = await budget.check_call(
            _call(call_id="c1").model_copy(update={"cost_usd": 12.0}), events
        )
        with pytest.raises(BudgetExceeded):
            await budget.check_call(
                _call(call_id="c2").model_copy(update={"cost_usd": 9.0}), events
            )
        assert budget.report().remaining_usd == 8.0
        await accepted.settle(10.0)
        assert budget.metered_spend_usd == 10.0
        assert budget.report().remaining_usd == 10.0

    asyncio.run(run())


def test_budget_rejects_unpriced_metered_call(tmp_path: Path) -> None:
    async def run() -> None:
        budget = Budget()
        with pytest.raises(ValueError, match="metered calls require a price"):
            await budget.check_call(
                _call().model_copy(update={"cost_usd": None}), EventLog(tmp_path / "events")
            )

    asyncio.run(run())


def test_budget_records_actual_over_reservation_charge_before_raising(tmp_path: Path) -> None:
    async def run() -> None:
        budget = Budget()
        reservation = await budget.check_call(
            _call().model_copy(update={"cost_usd": 5.0}), EventLog(tmp_path / "events")
        )
        with pytest.raises(BudgetExceeded, match="actual charge"):
            await reservation.settle(6.0)
        assert budget.metered_spend_usd == 6.0
        assert budget.report().remaining_usd == 14.0
        assert budget._reserved == {}

    asyncio.run(run())


def test_budget_refuses_over_cap_call_and_emits_budget_event(tmp_path: Path) -> None:
    async def run() -> None:
        budget = Budget()
        events = EventLog(tmp_path)
        await budget.record_call(_call().model_copy(update={"cost_usd": 19.99}), events)
        with pytest.raises(BudgetExceeded):
            await budget.check_call(_call().model_copy(update={"cost_usd": 0.02}), events=events)
        emitted = await events.read_after("r1", after=0)
        assert len(emitted) == 1
        assert emitted[0].kind == "budget"
        assert emitted[0].data["cap_usd"] == 20.0
        assert emitted[0].data["compute_cost_usd"] == 0.0

    asyncio.run(run())
