"""Explicit scorer registry with immutable versioned identities."""

from __future__ import annotations

from collections.abc import Iterable

from arena.scorers.base import Scorer, ScorerError


class ScorerRegistry:
    """Resolve scorers by ID or by an exact ``id@version`` reference."""

    def __init__(self, scorers: Iterable[Scorer] = ()) -> None:
        self._scorers: dict[tuple[str, str], Scorer] = {}
        for scorer in scorers:
            self.register(scorer)

    def register(self, scorer: Scorer) -> None:
        if not scorer.id or "@" in scorer.id:
            raise ScorerError("scorer ID must be non-empty and must not contain '@'")
        if not scorer.version:
            raise ScorerError(f"scorer {scorer.id!r} must have a non-empty version")
        key = (scorer.id, scorer.version)
        if key in self._scorers:
            raise ScorerError(f"scorer {scorer.id}@{scorer.version} is already registered")
        self._scorers[key] = scorer

    def get(self, scorer_ref: str) -> Scorer:
        """Resolve exact version when supplied; reject ambiguous unversioned IDs."""
        if "@" in scorer_ref:
            scorer_id, version = scorer_ref.rsplit("@", maxsplit=1)
            try:
                return self._scorers[(scorer_id, version)]
            except KeyError as exc:
                raise ScorerError(f"unknown scorer {scorer_ref!r}") from exc

        matches = [
            scorer for (scorer_id, _), scorer in self._scorers.items() if scorer_id == scorer_ref
        ]
        if not matches:
            raise ScorerError(f"unknown scorer {scorer_ref!r}")
        if len(matches) > 1:
            versions = sorted(scorer.version for scorer in matches)
            raise ScorerError(
                f"scorer {scorer_ref!r} has multiple versions {versions}; use scorer_id@version"
            )
        return matches[0]

    def versions(self, scorer_id: str) -> tuple[str, ...]:
        """Return registered versions in stable lexical order."""
        return tuple(
            sorted(version for candidate_id, version in self._scorers if candidate_id == scorer_id)
        )

    def __len__(self) -> int:
        return len(self._scorers)
