"""Judge calibration measures against human-labeled pairwise judgments."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from arena.core.models import Call

Verdict = Literal["a", "b", "tie"]


@dataclass(frozen=True)
class PairJudgment:
    item_id: str
    judge_id: str
    verdict: Verdict | None
    human_verdict: Verdict | None
    swapped_verdict: Verdict | None = None
    length_a: float | None = None
    length_b: float | None = None
    contestant_a_family: str | None = None
    contestant_b_family: str | None = None
    judge_family: str | None = None


@dataclass(frozen=True)
class JudgeCalibrationReport:
    judge_id: str
    judgments: int
    human_labeled: int
    agreement: float | None
    cohens_kappa: float | None
    position_bias_rate: float | None
    length_correlation: float | None
    self_preference_rate: float | None
    cost_per_judgment_usd: float | None
    missing_verdicts: int
    missing_human_labels: int
    missing_order_swaps: int
    missing_lengths: int
    missing_costs: int


def calibration_report(
    judge_id: str,
    judgments: Iterable[PairJudgment],
    calls: Iterable[Call],
    *,
    judge_model: str | None = None,
) -> JudgeCalibrationReport:
    """Calculate available judge metrics; absent evidence remains None and is counted."""
    rows = sorted(
        (row for row in judgments if row.judge_id == judge_id), key=lambda row: row.item_id
    )
    if not rows:
        raise ValueError(f"no judgments for judge {judge_id}")
    labeled = [row for row in rows if row.verdict is not None and row.human_verdict is not None]
    agreement = (
        (sum(row.verdict == row.human_verdict for row in labeled) / len(labeled))
        if labeled
        else None
    )
    kappa = (
        _kappa([row.verdict for row in labeled], [row.human_verdict for row in labeled])
        if labeled
        else None
    )
    swapped = [row for row in rows if row.verdict is not None and row.swapped_verdict is not None]
    position_bias = (
        (sum(row.verdict != row.swapped_verdict for row in swapped) / len(swapped))
        if swapped
        else None
    )
    lengths = [
        row
        for row in rows
        if row.verdict is not None and row.length_a is not None and row.length_b is not None
    ]
    length_values = [
        (row.length_a - row.length_b, _verdict_score(row.verdict))
        for row in lengths
        if row.length_a is not None and row.length_b is not None and row.verdict is not None
    ]
    length_correlation = _correlation(length_values) if len(length_values) >= 2 else None
    eligible_self = [
        row
        for row in rows
        if row.verdict is not None
        and row.judge_family is not None
        and row.contestant_a_family is not None
        and row.contestant_b_family is not None
        and row.contestant_a_family != row.contestant_b_family
    ]
    self_preference = (
        (
            sum(
                (row.verdict == "a" and row.contestant_a_family == row.judge_family)
                or (row.verdict == "b" and row.contestant_b_family == row.judge_family)
                for row in eligible_self
            )
            / len(eligible_self)
        )
        if eligible_self
        else None
    )
    judge_calls = [
        call
        for call in calls
        if judge_model is not None and call.purpose == "judge" and call.model_asked == judge_model
    ]
    cost_known = bool(judge_calls) and all(call.cost_usd is not None for call in judge_calls)
    cost_per = sum(call.cost_usd or 0.0 for call in judge_calls) / len(rows) if cost_known else None
    return JudgeCalibrationReport(
        judge_id,
        len(rows),
        len(labeled),
        agreement,
        kappa,
        position_bias,
        length_correlation,
        self_preference,
        cost_per,
        sum(row.verdict is None for row in rows),
        sum(row.human_verdict is None for row in rows),
        sum(row.swapped_verdict is None for row in rows),
        sum(row.length_a is None or row.length_b is None for row in rows),
        sum(not cost_known for _ in rows),
    )


def _verdict_score(verdict: Verdict | None) -> float:
    return {"a": 1.0, "tie": 0.5, "b": 0.0}[verdict] if verdict is not None else math.nan


def _kappa(left: list[Verdict | None], right: list[Verdict | None]) -> float | None:
    if not left:
        return None
    categories: tuple[Verdict, ...] = ("a", "b", "tie")
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / len(left)
    expected = sum(
        sum(value == category for value in left) * sum(value == category for value in right)
        for category in categories
    ) / (len(left) ** 2)
    return (
        (observed - expected) / (1 - expected) if expected < 1 else (1.0 if observed == 1 else 0.0)
    )


def _correlation(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    xs, ys = zip(*pairs, strict=True)
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    denominator = math.sqrt(sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys))
    return covariance / denominator if denominator else None
