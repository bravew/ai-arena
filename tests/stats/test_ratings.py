from __future__ import annotations

import math

import pytest

from arena.stats.pairwise import PairwiseJudgment, judgments_for
from arena.stats.pareto import ParetoPoint, pareto_frontier
from arena.stats.ratings import bradley_terry


def _judgment(left: str, right: str, outcome: str, judge: str = "model") -> PairwiseJudgment:
    return PairwiseJudgment(left, right, outcome, judge)  # type: ignore[arg-type]


def test_pairwise_keeps_model_and_human_leaderboards_separate() -> None:
    rows = [
        _judgment("a", "b", "left", "model"),
        _judgment("a", "b", "right", "human"),
    ]
    assert len(judgments_for(rows, "model")) == 1
    assert bradley_terry(rows, judge="model", samples=20).leaderboard()[0].contestant_id == "a"
    assert bradley_terry(rows, judge="human", samples=20).leaderboard()[0].contestant_id == "b"


def test_pairwise_rejects_invalid_ids_and_self_comparisons() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        PairwiseJudgment("", "b", "left", "model")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="itself"):
        PairwiseJudgment("a", "a", "tie", "human")


def test_bradley_terry_recovers_hand_computed_strengths() -> None:
    # Three a wins and one b win yield the symmetric-prior estimate 3.5 / 1.5.
    rows = [_judgment("a", "b", "left") for _ in range(3)] + [_judgment("a", "b", "right")]
    result = bradley_terry(rows, samples=200, seed=11)
    ratings = {row.contestant_id: row for row in result.entries}
    assert ratings["a"].strength / ratings["b"].strength == pytest.approx(3.5 / 1.5)
    assert ratings["a"].wins == 3
    assert ratings["a"].losses == 1
    assert math.prod(row.strength for row in result.entries) == pytest.approx(1)


def test_bradley_terry_recovers_planted_ordering_from_2k_comparisons() -> None:
    strengths = {"top": 4.0, "upper": 2.0, "lower": 1.0, "bottom": 0.5}
    pairs = [
        ("top", "upper"),
        ("top", "lower"),
        ("top", "bottom"),
        ("upper", "lower"),
        ("upper", "bottom"),
        ("lower", "bottom"),
    ]
    rows = [
        _judgment(
            left,
            right,
            "left"
            if index < int(333 * strengths[left] / (strengths[left] + strengths[right]))
            else "right",
        )
        for left, right in pairs
        for index in range(333)
    ]
    rows.extend([_judgment("top", "bottom", "left")] * 2)
    result = bradley_terry(rows, samples=40, seed=12)

    assert len(rows) == 2_000
    assert [row.contestant_id for row in result.leaderboard()] == [
        "top",
        "upper",
        "lower",
        "bottom",
    ]


def test_bradley_terry_handles_complete_separation_and_ties() -> None:
    rows = [_judgment("a", "b", "left") for _ in range(4)]
    rows += [_judgment("b", "c", "tie")]
    result = bradley_terry(rows, samples=60, seed=4)
    assert all(math.isfinite(row.strength) and row.strength > 0 for row in result.entries)
    assert {row.contestant_id: row.ties for row in result.entries}["b"] == 1


def test_disconnected_graph_is_reported_as_separate_components() -> None:
    result = bradley_terry([_judgment("a", "b", "left"), _judgment("x", "y", "right")], samples=20)
    assert result.connected_components == (("a", "b"), ("x", "y"))
    assert {row.component for row in result.entries} == {0, 1}


def test_reproducibility_and_invalid_bootstrap_inputs() -> None:
    rows = [_judgment("a", "b", outcome) for outcome in ("left", "left", "right", "tie")]
    assert bradley_terry(rows, samples=100, seed=2) == bradley_terry(rows, samples=100, seed=2)
    with pytest.raises(ValueError, match="samples"):
        bradley_terry(rows, samples=0)
    with pytest.raises(ValueError, match="at least one judgment"):
        bradley_terry([], judge="human")


def test_pareto_dominance_ties_and_subscription_cost_axis() -> None:
    points = [
        ParetoPoint("best", 0.9, 120, 1.2),
        ParetoPoint("dominated", 0.8, 150, 1.5),
        ParetoPoint("same-quality-cheaper", 0.9, 100, 1.4),
        ParetoPoint("coordinate-tie", 0.9, 100, 1.4),
        ParetoPoint("flat", 0.85, 80, subscription_backed=True),
    ]
    result = pareto_frontier(points)
    assert result.cost_axis == "tokens_per_task"
    assert {point.contestant_id for point in result.frontier} == {
        "same-quality-cheaper",
        "coordinate-tie",
        "flat",
    }
    assert next(point for point in points if point.contestant_id == "flat").cost_label == "flat"


def test_pareto_price_axis_requires_costs_and_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="requires a dollar cost"):
        pareto_frontier([ParetoPoint("flat", 0.5, 100)], cost_axis="cost_usd_per_task")
    with pytest.raises(ValueError, match="unique"):
        pareto_frontier([ParetoPoint("a", 0.5, 100), ParetoPoint("a", 0.7, 90)])
