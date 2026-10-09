"""Pareto-efficient contestant choices across quality and resource use."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

CostLabel = Literal["priced", "flat"]


@dataclass(frozen=True)
class ParetoPoint:
    contestant_id: str
    quality: float
    tokens_per_task: float
    cost_usd_per_task: float | None = None
    subscription_backed: bool = False

    def __post_init__(self) -> None:
        if not self.contestant_id:
            raise ValueError("contestant_id must be non-empty")
        if not math.isfinite(self.quality):
            raise ValueError("quality must be finite")
        if not math.isfinite(self.tokens_per_task) or self.tokens_per_task < 0:
            raise ValueError("tokens_per_task must be finite and non-negative")
        if self.cost_usd_per_task is not None and (
            not math.isfinite(self.cost_usd_per_task) or self.cost_usd_per_task < 0
        ):
            raise ValueError("cost_usd_per_task must be finite and non-negative")
        if self.subscription_backed and self.cost_usd_per_task is not None:
            raise ValueError("subscription-backed contestants must use flat cost")

    @property
    def cost_label(self) -> CostLabel:
        return "flat" if self.subscription_backed else "priced"


@dataclass(frozen=True)
class ParetoResult:
    points: tuple[ParetoPoint, ...]
    frontier: tuple[ParetoPoint, ...]
    cost_axis: Literal["tokens_per_task", "cost_usd_per_task"]


def pareto_frontier(
    points: Iterable[ParetoPoint],
    *,
    cost_axis: Literal["auto", "tokens_per_task", "cost_usd_per_task"] = "auto",
) -> ParetoResult:
    """Return all non-dominated choices, maximizing quality and minimizing cost.

    Auto uses tokens/task if any point is subscription-backed (or has unknown dollar
    cost), since a flat fee is not a dollar amount. Exact coordinate ties are all kept.
    """
    rows = tuple(points)
    if len({row.contestant_id for row in rows}) != len(rows):
        raise ValueError("contestant ids must be unique")
    use_tokens = cost_axis == "tokens_per_task" or (
        cost_axis == "auto"
        and any(row.subscription_backed or row.cost_usd_per_task is None for row in rows)
    )
    axis: Literal["tokens_per_task", "cost_usd_per_task"] = (
        "tokens_per_task" if use_tokens else "cost_usd_per_task"
    )
    if axis == "cost_usd_per_task" and any(row.cost_usd_per_task is None for row in rows):
        raise ValueError("cost axis requires a dollar cost for every contestant")

    def cost(row: ParetoPoint) -> float:
        if axis == "tokens_per_task":
            return row.tokens_per_task
        assert row.cost_usd_per_task is not None
        return row.cost_usd_per_task

    frontier = tuple(
        row
        for row in rows
        if not any(
            other.contestant_id != row.contestant_id
            and other.quality >= row.quality
            and cost(other) <= cost(row)
            and (other.quality > row.quality or cost(other) < cost(row))
            for other in rows
        )
    )
    return ParetoResult(rows, frontier, axis)
