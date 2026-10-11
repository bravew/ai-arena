"""Build the scorers a task YAML lists, for the ones that need no sandbox and no model.

A task lists scorers as `{id, weight, gate, ...options}`. `constraints` maps to a
`ConstraintScorer` over the trial's `answer.md`. `execution` needs a sandbox with the task's
files and `rubric-judge` needs a judge model behind the gateway, so `arena run` leaves them
unapplied and says so. An option this module does not know is an error, never ignored: a
scorer that quietly dropped a check would report a pass nobody earned.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from arena.core.models import Task
from arena.scorers.base import Scorer, ScorerError
from arena.scorers.constraint import Check, ConstraintScorer, RequiredKeywords, WordCount

ANSWER_PATH = "answer.md"
_COMMON_KEYS = frozenset({"id", "weight", "gate"})
_CONSTRAINT_KEYS = frozenset({"min_words", "max_words", "must_include"})


@dataclass(frozen=True)
class TaskScorers:
    scorers: tuple[Scorer, ...]
    unapplied: tuple[str, ...]


def task_scorers(task: Task) -> TaskScorers:
    """The scorers `arena run` can apply to `task`, and the ids of those it cannot."""
    scorers: list[Scorer] = []
    unapplied: list[str] = []
    for entry in task.scorers:
        scorer_id = entry.get("id")
        if scorer_id == "constraints":
            scorers.append(_constraints(task, entry))
        elif isinstance(scorer_id, str):
            unapplied.append(scorer_id)
        else:
            raise ScorerError(f"task {task.id} has a scorer without an id")
    return TaskScorers(tuple(scorers), tuple(unapplied))


def _constraints(task: Task, entry: Mapping[str, Any]) -> ConstraintScorer:
    unknown = sorted(set(entry) - _COMMON_KEYS - _CONSTRAINT_KEYS)
    if unknown:
        raise ScorerError(f"task {task.id}: unknown constraints option(s) {', '.join(unknown)}")
    checks: list[Check] = []
    if "min_words" in entry or "max_words" in entry:
        checks.append(WordCount(min=entry.get("min_words"), max=entry.get("max_words")))
    if "must_include" in entry:
        checks.append(RequiredKeywords(keywords=list(entry["must_include"])))
    return ConstraintScorer(ANSWER_PATH, checks, scorer_id="constraints")
