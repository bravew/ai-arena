"""Candidate ordering for gateway retries (DEV_PLAN §5.4 step 5)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Candidate:
    """One key/account that can serve the requested model."""

    key: str
    model: str
    resting: bool = False


def plan_candidates[T: Candidate](candidates: tuple[T, ...], requested_model: str) -> tuple[T, ...]:
    """Order same-model candidates with resting keys last, retaining input order."""
    matching = tuple(candidate for candidate in candidates if candidate.model == requested_model)
    ready = tuple(candidate for candidate in matching if not candidate.resting)
    resting = tuple(candidate for candidate in matching if candidate.resting)
    return ready + resting
