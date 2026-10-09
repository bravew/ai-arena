"""Deterministic comparison of a text artifact with a reference and key-point list.

Similarity uses token-multiset Dice overlap, not an embedding or a model. Key-point
coverage uses deterministic token recall; evidence records each matched phrase and lists
missed points so a reviewer can see why coverage was awarded.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from arena.core.models import Score
from arena.scorers.base import ScorerContext, ScorerError
from arena.scorers.constraint import read_text

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
MAX_EVIDENCE_ITEMS = 50


class _Check(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Similarity(_Check):
    """Token multiset Dice similarity against the reference text."""

    kind: Literal["similarity"] = "similarity"
    threshold: float = Field(default=0.0, ge=0.0, le=1.0)


class KeyPointCoverage(_Check):
    """Mean token recall of required key points, with per-point match evidence."""

    kind: Literal["key_point_coverage"] = "key_point_coverage"
    points: list[str] = Field(min_length=1)
    threshold: float = Field(default=1.0, ge=0.0, le=1.0)
    case_sensitive: bool = False


ReferenceCheck = Annotated[Similarity | KeyPointCoverage, Field(discriminator="kind")]


def _tokens(text: str, case_sensitive: bool = False) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text)
    if not case_sensitive:
        normalized = normalized.casefold()
    return _TOKEN.findall(normalized)


class ReferenceScorer:
    """Compare the artifact at ``path`` with ``reference`` using configured checks."""

    def __init__(
        self,
        path: str,
        *,
        reference: str,
        checks: Sequence[ReferenceCheck],
        scorer_id: str = "reference",
        version: str = "1",
    ) -> None:
        if not checks:
            raise ScorerError("reference scorer needs at least one check")
        self.id = scorer_id
        self.version = version
        self.path = path
        self.reference = reference
        self.checks = tuple(checks)

    def score(self, context: ScorerContext) -> Score:
        text = read_text(context, self.path)
        results = [
            _similarity(check, text, self.reference)
            if isinstance(check, Similarity)
            else _coverage(check, text)
            for check in self.checks
        ]
        failed = [str(result["check"]) for result in results if not result["passed"]]
        normalized = sum(float(result["score"]) for result in results) / len(results)
        return Score(
            trial_id=context.trial_id,
            scorer_id=self.id,
            scorer_version=self.version,
            value=normalized,
            normalized=normalized,
            passed=not failed,
            rationale=(
                f"Normalized reference score {normalized:.3f} across {len(results)} checks"
                + (f"; failed: {', '.join(failed)}" if failed else "")
            ),
            evidence={"path": self.path, "checks": results},
        )


def _similarity(check: Similarity, text: str, reference: str) -> dict[str, Any]:
    answer_tokens = Counter(_tokens(text))
    reference_tokens = Counter(_tokens(reference))
    intersection = sum((answer_tokens & reference_tokens).values())
    denominator = sum(answer_tokens.values()) + sum(reference_tokens.values())
    value = 1.0 if denominator == 0 else 2.0 * intersection / denominator
    return {
        "check": "similarity",
        "metric": "token_dice",
        "passed": value >= check.threshold,
        "similarity": value,
        "score": value,
        "matched_tokens": intersection,
        "answer_tokens": sum(answer_tokens.values()),
        "reference_tokens": sum(reference_tokens.values()),
        "threshold": check.threshold,
    }


def _coverage(check: KeyPointCoverage, text: str) -> dict[str, Any]:
    answer_tokens = set(_tokens(text, check.case_sensitive))
    coverage: dict[str, float] = {}
    evidence: dict[str, str] = {}
    invalid: list[str] = []
    for point in check.points:
        tokens = _tokens(point, check.case_sensitive)
        if not tokens:
            invalid.append(point)
            continue
        matched = [token for token in tokens if token in answer_tokens]
        fraction = len(matched) / len(tokens)
        coverage[point] = fraction
        if fraction >= check.threshold:
            evidence[point] = " ".join(token for token in tokens if token in answer_tokens)
    if invalid:
        raise ScorerError(f"key points must contain at least one token: {invalid}")
    covered = [point for point in check.points if point in evidence]
    missed = [point for point in check.points if point not in evidence]
    score = sum(coverage.values()) / len(check.points)
    return {
        "check": "key_point_coverage",
        "passed": not missed,
        "coverage_score": score,
        "score": score,
        "coverage": coverage,
        "covered": covered,
        "missed": missed,
        "evidence": {point: evidence[point] for point in covered[:MAX_EVIDENCE_ITEMS]},
        "threshold": check.threshold,
    }
