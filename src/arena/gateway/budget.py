"""Hard metered-spend budget for a full comparison run."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from arena.core.models import Call, RunEvent
from arena.obs.events import EventLog

DEFAULT_BUDGET_USD = 20.0


class BudgetExceeded(RuntimeError):
    """A proposed metered call would exceed the run's hard spend cap."""


@dataclass(frozen=True, slots=True)
class BudgetReport:
    cap_usd: float
    metered_spend_usd: float
    remaining_usd: float
    subscription_tokens: int
    compute_cost_usd: float


class BudgetReservation:
    """One call's reserved share of the cap."""

    def __init__(self, budget: Budget, call: Call) -> None:
        self.budget = budget
        self.call = call
        self._settled = False

    async def settle(self, final_cost_usd: float | None = None) -> None:
        if self._settled:
            raise RuntimeError("budget reservation is already settled")
        await self.budget.settle(self, final_cost_usd, billable=True)
        self._settled = True

    async def release(self) -> None:
        if self._settled:
            raise RuntimeError("budget reservation is already settled")
        await self.budget.settle(self, None, billable=False)
        self._settled = True


class Budget:
    """Track and reserve API spend against a hard cap, reporting other costs separately."""

    def __init__(self, cap_usd: float = DEFAULT_BUDGET_USD) -> None:
        if not 0 < cap_usd <= DEFAULT_BUDGET_USD:
            raise ValueError(f"budget cap must be between $0 and ${DEFAULT_BUDGET_USD:.2f}")
        self.cap_usd = cap_usd
        self._metered_spend = 0.0
        self._reserved: dict[str, tuple[float, Call]] = {}
        self._subscription_tokens = 0
        self._compute_cost = 0.0
        self._lock = asyncio.Lock()

    @property
    def metered_spend_usd(self) -> float:
        return self._metered_spend

    async def check_call(self, call: Call, events: EventLog) -> BudgetReservation:
        """Atomically reserve a call's estimated cost before dispatching it upstream."""
        amount = call.cost_usd or 0.0
        async with self._lock:
            proposed_spend = (
                self._metered_spend
                + sum(reserved_amount for reserved_amount, _ in self._reserved.values())
                + amount
            )
            if proposed_spend > self.cap_usd:
                spent = self._metered_spend
                await events.append(
                    RunEvent(
                        seq=0,
                        ts=datetime.now(UTC),
                        run_id=call.run_id,
                        kind="budget",
                        ref=call.id,
                        data={
                            "cap_usd": self.cap_usd,
                            "spent_usd": spent,
                            "reserved_usd": sum(
                                reserved_amount for reserved_amount, _ in self._reserved.values()
                            ),
                            "requested_usd": amount,
                            "subscription_tokens": self._subscription_tokens,
                            "compute_cost_usd": self._compute_cost,
                        },
                    )
                )
                raise BudgetExceeded(
                    f"call would exceed the ${self.cap_usd:.2f} metered API budget "
                    f"(${spent:.6f} spent, ${amount:.6f} requested)"
                )
            if call.id in self._reserved:
                raise ValueError(f"call already has a budget reservation: {call.id}")
            self._reserved[call.id] = (amount, call)
        return BudgetReservation(self, call)

    async def settle(
        self,
        reservation: BudgetReservation,
        final_cost_usd: float | None,
        *,
        billable: bool,
    ) -> None:
        call = reservation.call
        if final_cost_usd is not None and final_cost_usd < 0:
            raise ValueError("final cost must be non-negative")
        async with self._lock:
            if call.id not in self._reserved:
                raise ValueError(f"budget reservation not found for call: {call.id}")
            reserved_amount, reserved_call = self._reserved[call.id]
            if not billable:
                del self._reserved[call.id]
                return
            if reserved_call.cost_usd is None:
                del self._reserved[call.id]
                self._subscription_tokens += sum(
                    (
                        reserved_call.tokens.in_,
                        reserved_call.tokens.out,
                        reserved_call.tokens.reasoning,
                        reserved_call.tokens.cache_read,
                        reserved_call.tokens.cache_write,
                    )
                )
                return
            if final_cost_usd is None:
                del self._reserved[call.id]
                return
            del self._reserved[call.id]
            self._metered_spend += final_cost_usd
            if final_cost_usd > reserved_amount:
                raise BudgetExceeded(
                    f"actual charge ${final_cost_usd:.6f} exceeded "
                    f"reserved amount ${reserved_amount:.6f}"
                )

    async def record_call(self, call: Call, events: EventLog) -> None:
        """Reserve and settle immediately for callers without a separate dispatch phase."""
        reservation = await self.check_call(call, events)
        await reservation.settle(call.cost_usd)

    def record_compute_cost(self, cost_usd: float) -> None:
        if cost_usd < 0:
            raise ValueError("compute cost must be non-negative")
        self._compute_cost += cost_usd

    def report(self) -> BudgetReport:
        return BudgetReport(
            cap_usd=self.cap_usd,
            metered_spend_usd=self._metered_spend,
            remaining_usd=max(
                0.0,
                self.cap_usd
                - self._metered_spend
                - sum(amount for amount, _ in self._reserved.values()),
            ),
            subscription_tokens=self._subscription_tokens,
            compute_cost_usd=self._compute_cost,
        )
