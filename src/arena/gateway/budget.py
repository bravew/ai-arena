"""Hard metered-spend budget for a full comparison run."""

from __future__ import annotations

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


class Budget:
    """Track API spend against a hard cap, reporting subscription and compute separately."""

    def __init__(self, cap_usd: float = DEFAULT_BUDGET_USD) -> None:
        if not 0 < cap_usd <= DEFAULT_BUDGET_USD:
            raise ValueError(f"budget cap must be between $0 and ${DEFAULT_BUDGET_USD:.2f}")
        self.cap_usd = cap_usd
        self._metered_spend = 0.0
        self._subscription_tokens = 0
        self._compute_cost = 0.0

    @property
    def metered_spend_usd(self) -> float:
        return self._metered_spend

    async def check_call(self, call: Call, events: EventLog | None = None) -> None:
        proposed_spend = self._metered_spend + (call.cost_usd or 0.0)
        if proposed_spend > self.cap_usd:
            if events is not None:
                await events.append(
                    RunEvent(
                        seq=0,
                        ts=datetime.now(UTC),
                        run_id=call.run_id,
                        kind="budget",
                        ref=call.id,
                        data={
                            "cap_usd": self.cap_usd,
                            "spent_usd": self._metered_spend,
                            "requested_usd": call.cost_usd or 0.0,
                            "subscription_tokens": self._subscription_tokens,
                            "compute_cost_usd": self._compute_cost,
                        },
                    )
                )
            raise BudgetExceeded(
                f"call would exceed the ${self.cap_usd:.2f} metered API budget "
                f"(${self._metered_spend:.6f} spent, ${call.cost_usd or 0.0:.6f} requested)"
            )

    def record_call(self, call: Call) -> None:
        if self._metered_spend + (call.cost_usd or 0.0) > self.cap_usd:
            raise BudgetExceeded("call exceeds the metered API budget; check_call must run first")
        if call.cost_usd is None:
            self._subscription_tokens += sum(
                (call.tokens.in_, call.tokens.out, call.tokens.reasoning,
                 call.tokens.cache_read, call.tokens.cache_write)
            )
        else:
            self._metered_spend += call.cost_usd

    def record_compute_cost(self, cost_usd: float) -> None:
        if cost_usd < 0:
            raise ValueError("compute cost must be non-negative")
        self._compute_cost += cost_usd

    def report(self) -> BudgetReport:
        return BudgetReport(
            cap_usd=self.cap_usd,
            metered_spend_usd=self._metered_spend,
            remaining_usd=max(0.0, self.cap_usd - self._metered_spend),
            subscription_tokens=self._subscription_tokens,
            compute_cost_usd=self._compute_cost,
        )
