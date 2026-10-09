"""Validated pairwise judgments for distinct model and human leaderboards."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Outcome = Literal["left", "tie", "right"]
JudgeKind = Literal["model", "human"]


@dataclass(frozen=True)
class PairwiseJudgment:
    left: str
    right: str
    outcome: Outcome
    judge: JudgeKind
    task_id: str = ""

    def __post_init__(self) -> None:
        if not self.left or not self.right:
            raise ValueError("contestant ids must be non-empty")
        if self.left == self.right:
            raise ValueError("a contestant cannot be compared with itself")
        if self.outcome not in ("left", "tie", "right"):
            raise ValueError("outcome must be 'left', 'tie', or 'right'")
        if self.judge not in ("model", "human"):
            raise ValueError("judge must be 'model' or 'human'")


def judgments_for(
    judgments: Iterable[PairwiseJudgment], judge: JudgeKind
) -> tuple[PairwiseJudgment, ...]:
    """Return one judgment source only, preserving input order."""
    if judge not in ("model", "human"):
        raise ValueError("judge must be 'model' or 'human'")
    return tuple(item for item in judgments if item.judge == judge)
