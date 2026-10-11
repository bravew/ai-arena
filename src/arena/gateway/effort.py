"""Map normalized model effort to provider request parameters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from arena.core.modelref import Effort


@dataclass(frozen=True, slots=True)
class EffortResult:
    """Updated provider parameters and which mapped values were actually applied."""

    parameters: dict[str, Any]
    effort_applied: Effort | None
    added_parameters: dict[str, Any]


def apply_effort(
    payload: Mapping[str, Any],
    effort: Effort | None,
    *,
    mapping: Mapping[str, Mapping[str, Any]] | None = None,
    scaffold_carries_effort: bool = False,
) -> EffortResult:
    """Apply catalog-defined parameters when the scaffold does not carry effort.

    Caller-supplied provider parameters win. `effort_applied` is set only when every
    configured value is present with the requested value after applying the mapping.
    """
    parameters = dict(payload)
    if effort is None or scaffold_carries_effort:
        return EffortResult(parameters, None, {})

    settings = (mapping or {}).get(effort)
    if not settings:
        return EffortResult(parameters, None, {})

    added: dict[str, Any] = {}
    for key, value in settings.items():
        if key not in parameters:
            parameters[key] = value
            added[key] = value
    all_settings_applied = all(parameters.get(key) == value for key, value in settings.items())
    applied = effort if all_settings_applied else None
    return EffortResult(parameters, applied, added)
