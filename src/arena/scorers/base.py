"""Scorer contracts and deterministic score aggregation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from arena.core.cas import ArtifactStore
from arena.core.models import Artifact, Score


class ScorerError(ValueError):
    """Invalid scorer definitions, unreadable inputs or incompatible score results."""


class ArtifactIndex(Protocol):
    """Which artifacts a trial produced.

    The CAS stores blobs by digest only, and the CP1 schema has no trial-to-artifact
    link, so the runner that records artifacts supplies this lookup.
    """

    def for_trial(self, trial_id: str) -> Sequence[Artifact]: ...


class ScorerContext(BaseModel):
    """Immutable view of the stored trial inputs available to a scorer."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    trial_id: str
    artifacts: Mapping[str, Artifact] = Field(default_factory=dict)
    blobs: ArtifactStore
    config: Mapping[str, Any] = Field(default_factory=dict)

    def read(self, path: str) -> bytes:
        """Return an artifact's bytes, verified against its digest."""
        try:
            artifact = self.artifacts[path]
        except KeyError:
            raise ScorerError(f"trial {self.trial_id} has no artifact {path!r}") from None
        return read_artifact(self.blobs, artifact, self.trial_id)


def read_artifact(blobs: ArtifactStore, artifact: Artifact, trial_id: str) -> bytes:
    """Read a blob; a missing, unreadable or corrupt one is an error, never empty."""
    try:
        return blobs.get(artifact.sha256)
    except FileNotFoundError:
        raise ScorerError(
            f"missing stored artifact {artifact.sha256} for trial {trial_id}"
        ) from None
    except (OSError, ValueError) as exc:
        raise ScorerError(
            f"cannot read artifact {artifact.sha256} for trial {trial_id}: {exc}"
        ) from exc


def scorer_ref(score: Score) -> str:
    """The ``scorer_id@version`` identity that keeps versions as separate series."""
    return f"{score.scorer_id}@{score.scorer_version}"


class Scorer(Protocol):
    """Implementations must be deterministic for a context and config."""

    id: str
    version: str

    def score(self, context: ScorerContext) -> Score:
        """Return a score whose trial and scorer identity match this request."""
        ...


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
    refs. A failed gate caps the aggregate at 0.3, and a gate with no pass/fail
    verdict is an error rather than a pass. Different versions are
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
        ref = scorer_ref(score)
        if ref in seen:
            raise ScorerError(f"duplicate score for {ref}")
        seen.add(ref)

        weight = _weight_for(score, weights or {})
        if weight < 0:
            raise ScorerError(f"weight for {ref} must not be negative")
        if weight > 0:
            weighted_total += score.normalized * weight
            total_weight += weight
        if _is_gate(score, gates):
            if score.passed is None:
                raise ScorerError(f"gate {ref} must report passed true or false")
            failed_gate = failed_gate or not score.passed

    if total_weight == 0:
        raise ScorerError("at least one score must have a positive weight")
    result = weighted_total / total_weight
    return min(result, 0.3) if failed_gate else result


def _weight_for(score: Score, weights: Mapping[str, float]) -> float:
    return weights.get(scorer_ref(score), weights.get(score.scorer_id, 1.0))


def _is_gate(score: Score, gates: set[str] | frozenset[str]) -> bool:
    return scorer_ref(score) in gates or score.scorer_id in gates
