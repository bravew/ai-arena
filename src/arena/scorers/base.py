"""Scorer contracts and deterministic score aggregation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ScorerContext(BaseModel):
    """Immutable view of the stored trial inputs available to a scorer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trial_id: str
    artifacts: Mapping[str, Any] = Field(default_factory=dict)
    config: Mapping[str, Any] = Field(default_factory=dict)


class Score(BaseModel):
    """A version-pinned scorer result; versions form separate time series."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trial_id: str
    scorer_id: str
    scorer_version: str
    value: float
    normalized: float = Field(ge=0.0, le=1.0)
    passed: bool | None = None
    rationale: str = ""
    evidence: Mapping[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def scorer_ref(self) -> str:
        return f"{self.scorer_id}@{self.scorer_version}"


class Scorer(Protocol):
    """Implementations must be deterministic for a context and config."""

    id: str
    version: str

    def score(self, context: ScorerContext) -> Score:
        """Return a score whose trial and scorer identity match this request."""
        ...


class ScorerError(ValueError):
    """Invalid scorer definitions or incompatible score results."""


def validate_score(scorer: Scorer, context: ScorerContext, score: Score) -> Score:
    """Reject a scorer result that cannot safely be associated with its inputs."""
    if score.trial_id != context.trial_id:
        raise ScorerError(
            f"scorer {scorer.id}@{scorer.version} returned trial {score.trial_id!r}; "
            f"expected {context.trial_id!r}"
        )
    if score.scorer_id != scorer.id or score.scorer_version != scorer.version:
        raise ScorerError(
            f"scorer {scorer.id}@{scorer.version} returned identity "
            f"{score.scorer_id}@{score.scorer_version}"
        )
    return score


def aggregate_scores(
    scores: Sequence[Score],
    weights: Mapping[str, float] | None = None,
    gates: set[str] | frozenset[str] = frozenset(),
) -> float:
    """Return weighted normalized quality, applying the documented gate cap.

    Keys in ``weights`` and ``gates`` are scorer IDs or versioned ``id@version``
    refs. A failed gate caps the aggregate at 0.3. Different versions are
    rejected together so a caller cannot accidentally mix their series.
    """
    if not scores:
        raise ScorerError("cannot aggregate an empty score sequence")

    versions_by_id: dict[str, set[str]] = {}
    for score in scores:
        versions_by_id.setdefault(score.scorer_id, set()).add(score.scorer_version)
    mixed = {key: versions for key, versions in versions_by_id.items() if len(versions) > 1}
    if mixed:
        raise ScorerError(f"mixed scorer versions cannot be aggregated: {mixed}")

    seen: set[str] = set()
    weighted_total = 0.0
    total_weight = 0.0
    failed_gate = False
    for score in scores:
        ref = score.scorer_ref
        if ref in seen:
            raise ScorerError(f"duplicate score for {ref}")
        seen.add(ref)

        weight = _weight_for(score, weights or {})
        if weight < 0:
            raise ScorerError(f"weight for {ref} must not be negative")
        if weight > 0:
            weighted_total += score.normalized * weight
            total_weight += weight
        if score.passed is False and _is_gate(score, gates):
            failed_gate = True

    if total_weight == 0:
        raise ScorerError("at least one score must have a positive weight")
    result = weighted_total / total_weight
    return min(result, 0.3) if failed_gate else result


def _weight_for(score: Score, weights: Mapping[str, float]) -> float:
    return weights.get(score.scorer_ref, weights.get(score.scorer_id, 1.0))


def _is_gate(score: Score, gates: set[str] | frozenset[str]) -> bool:
    return score.scorer_ref in gates or score.scorer_id in gates
