"""Bradley-Terry ratings with deterministic parametric bootstrap intervals."""

from __future__ import annotations

import math
import random
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from arena.stats.bootstrap import (
    DEFAULT_CONFIDENCE,
    DEFAULT_SAMPLES,
    DEFAULT_SEED,
    ConfidenceInterval,
)
from arena.stats.pairwise import JudgeKind, PairwiseJudgment

_PRIOR_WIN = 0.5


@dataclass(frozen=True)
class Rating:
    contestant_id: str
    strength: float
    log_strength: float
    interval: ConfidenceInterval
    wins: float
    losses: float
    ties: int
    judge: JudgeKind
    component: int


@dataclass(frozen=True)
class Ratings:
    entries: tuple[Rating, ...]
    judge: JudgeKind
    connected_components: tuple[tuple[str, ...], ...]

    def leaderboard(self) -> tuple[Rating, ...]:
        """Sort by strength, breaking exact ties by contestant id."""
        return tuple(sorted(self.entries, key=lambda row: (-row.strength, row.contestant_id)))


def bradley_terry(
    judgments: Iterable[PairwiseJudgment],
    *,
    judge: JudgeKind = "model",
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
    confidence: float = DEFAULT_CONFIDENCE,
    max_iterations: int = 10_000,
    tolerance: float = 1e-10,
) -> Ratings:
    """Fit BT strengths separately per comparison component.

    A tie contributes half a win to each side. Every pair in a connected component
    receives a symmetric Jeffreys pseudo-count of half a win per side. This keeps
    estimates finite under complete separation. Components have independent geometric
    mean one scales and must not be compared across component boundaries.
    """
    if judge not in ("model", "human"):
        raise ValueError("judge must be 'model' or 'human'")
    if samples < 1:
        raise ValueError("samples must be positive")
    if max_iterations < 1 or not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("max_iterations and tolerance must be positive")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    selected = tuple(item for item in judgments if item.judge == judge)
    ids = sorted({person for item in selected for person in (item.left, item.right)})
    if not ids:
        raise ValueError("at least one judgment for the selected judge is required")
    components = _components(ids, selected)
    rng = random.Random(seed)
    rows: list[Rating] = []
    for component_index, component in enumerate(components):
        edges = tuple(item for item in selected if item.left in component)
        point = _fit(component, edges, max_iterations, tolerance)
        bootstrap: dict[str, list[float]] = {item: [] for item in component}
        if len(component) == 1:
            bootstrap[component[0]] = [1.0] * samples
        else:
            for _ in range(samples):
                simulated = tuple(_simulate(edge, point, rng) for edge in edges)
                fitted = _fit(component, simulated, max_iterations, tolerance)
                for contestant_id in component:
                    bootstrap[contestant_id].append(math.exp(fitted[contestant_id]))
        for contestant_id in component:
            wins, losses, ties = _record(edges, contestant_id)
            strength = math.exp(point[contestant_id])
            values = sorted(bootstrap[contestant_id])
            tail = (1 - confidence) / 2
            interval = ConfidenceInterval(
                estimate=strength,
                low=_quantile(values, tail),
                high=_quantile(values, 1 - tail),
                confidence=confidence,
            )
            rows.append(
                Rating(
                    contestant_id,
                    strength,
                    point[contestant_id],
                    interval,
                    wins,
                    losses,
                    ties,
                    judge,
                    component_index,
                )
            )
    return Ratings(tuple(rows), judge, components)


def _record(edges: tuple[PairwiseJudgment, ...], contestant: str) -> tuple[float, float, int]:
    wins, losses, ties = 0.0, 0.0, 0
    for edge in edges:
        if contestant not in (edge.left, edge.right):
            continue
        if edge.outcome == "tie":
            wins += 0.5
            losses += 0.5
            ties += 1
        elif (edge.outcome == "left") == (contestant == edge.left):
            wins += 1
        else:
            losses += 1
    return wins, losses, ties


def _fit(
    ids: tuple[str, ...], edges: tuple[PairwiseJudgment, ...], max_iterations: int, tolerance: float
) -> dict[str, float]:
    if len(ids) == 1:
        return {ids[0]: 0.0}
    counts = {(left, right): [0.0, 0.0] for left in ids for right in ids if left != right}
    for left in ids:
        for right in ids:
            if left != right:
                counts[(left, right)][0] = _PRIOR_WIN
                counts[(left, right)][1] = _PRIOR_WIN
    for edge in edges:
        pair = counts[(edge.left, edge.right)]
        reverse = counts[(edge.right, edge.left)]
        if edge.outcome == "left":
            pair[0] += 1
            pair[1] += 0
            reverse[0] += 0
            reverse[1] += 1
        elif edge.outcome == "right":
            pair[0] += 0
            pair[1] += 1
            reverse[0] += 1
            reverse[1] += 0
        else:
            pair[0] += 0.5
            pair[1] += 0.5
            reverse[0] += 0.5
            reverse[1] += 0.5
    strengths = {item: 1.0 for item in ids}
    for _ in range(max_iterations):
        updated: dict[str, float] = {}
        for player in ids:
            numerator = math.fsum(counts[(player, other)][0] for other in ids if other != player)
            denominator = math.fsum(
                sum(counts[(player, other)]) / (strengths[player] + strengths[other])
                for other in ids
                if other != player
            )
            updated[player] = numerator / denominator
        scale = math.exp(math.fsum(math.log(value) for value in updated.values()) / len(ids))
        updated = {item: value / scale for item, value in updated.items()}
        delta = max(abs(math.log(updated[item] / strengths[item])) for item in ids)
        strengths = updated
        if delta < tolerance:
            return {item: math.log(strengths[item]) for item in ids}
    raise ValueError("Bradley-Terry fit did not converge")


def _simulate(
    edge: PairwiseJudgment, strengths: dict[str, float], rng: random.Random
) -> PairwiseJudgment:
    probability = strengths[edge.left] / (strengths[edge.left] + strengths[edge.right])
    outcome: Literal["left", "right"] = "left" if rng.random() < probability else "right"
    return PairwiseJudgment(edge.left, edge.right, outcome, edge.judge, edge.task_id)


def _components(ids: list[str], edges: tuple[PairwiseJudgment, ...]) -> tuple[tuple[str, ...], ...]:
    neighbors: dict[str, set[str]] = {item: set() for item in ids}
    for edge in edges:
        neighbors[edge.left].add(edge.right)
        neighbors[edge.right].add(edge.left)
    components: list[tuple[str, ...]] = []
    remaining = set(ids)
    while remaining:
        root = min(remaining)
        pending, found = [root], set[str]()
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            pending.extend(neighbors[current] - found)
        remaining -= found
        components.append(tuple(sorted(found)))
    return tuple(components)


def _quantile(values: list[float], probability: float) -> float:
    position = (len(values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction
