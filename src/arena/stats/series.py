"""Precomputed chart series for calls recorded in the bundle."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from arena.core.models import Call, Trial


@dataclass(frozen=True)
class UsagePoint:
    contestant_id: str
    seq: int
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float | None


@dataclass(frozen=True)
class LatencyDistribution:
    contestant_id: str
    latencies_ms: tuple[int, ...]
    p50_ms: float | None
    p95_ms: float | None


@dataclass(frozen=True)
class PromptCompositionPoint:
    call_id: str
    seq: int
    parts: tuple[tuple[str, int], ...]


def chart_series(
    calls: list[Call] | tuple[Call, ...], trials: list[Trial] | tuple[Trial, ...]
) -> tuple[
    tuple[UsagePoint, ...], tuple[LatencyDistribution, ...], tuple[PromptCompositionPoint, ...]
]:
    """Build deterministic usage, latency, and prompt-composition series.

    Calls missing a trial link or pointing at an unknown trial are skipped because
    they cannot be attributed to a contestant. Judge/orchestration calls are included
    when linked to a trial, and nullable cost stays unknown if any call cost is unknown.
    """
    trial_owner = {trial.id: trial.contestant_id for trial in trials}
    usage: dict[str, list[Call]] = defaultdict(list)
    latencies: dict[str, list[int]] = defaultdict(list)
    compositions: list[PromptCompositionPoint] = []
    for call in sorted(calls, key=lambda row: (row.seq, row.id)):
        contestant = trial_owner.get(call.trial_id or "")
        if contestant is None:
            continue
        usage[contestant].append(call)
        latencies[contestant].append(call.total_ms)
        compositions.append(
            PromptCompositionPoint(
                call.id, call.seq, tuple((part.kind, part.tokens) for part in call.prompt_parts)
            )
        )
    usage_points: list[UsagePoint] = []
    latency_points: list[LatencyDistribution] = []
    for contestant_id in sorted(usage):
        running_in = running_out = 0
        known_cost = 0.0
        costs_known = True
        for call in usage[contestant_id]:
            running_in += call.tokens.in_
            running_out += call.tokens.out
            if call.cost_usd is None:
                costs_known = False
            else:
                known_cost += call.cost_usd
            usage_points.append(
                UsagePoint(
                    contestant_id,
                    call.seq,
                    1,
                    running_in,
                    running_out,
                    known_cost if costs_known else None,
                )
            )
        ordered = tuple(sorted(latencies[contestant_id]))
        latency_points.append(
            LatencyDistribution(
                contestant_id, ordered, _quantile(ordered, 0.5), _quantile(ordered, 0.95)
            )
        )
    return tuple(usage_points), tuple(latency_points), tuple(compositions)


def _quantile(values: tuple[int, ...], quantile: float) -> float | None:
    if not values:
        return None
    position = (len(values) - 1) * quantile
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] * (high - position) + values[high] * (position - low)
