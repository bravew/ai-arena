"""Map normalized model effort to provider request parameters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from arena.core.modelref import Effort


class EffortResult(dict[str, Any]):
    """Parameters after effort mapping and the normalized level applied, if any."""

    effort_applied: Effort | None

    def __init__(self, parameters: Mapping[str, Any], effort_applied: Effort | None) -> None:
        super().__init__(parameters)
        self.effort_applied = effort_applied


def apply_effort(
    payload: Mapping[str, Any],
    effort: Effort | None,
    *,
    mapping: Mapping[str, Mapping[str, Any]] | None = None,
    scaffold_carries_effort: bool = False,
) -> EffortResult:
    """Apply catalog-defined provider parameters when the caller scaffold does not carry effort.

    `mapping` is the model catalog's `efforts` object, such as
    `{"high": {"reasoning_effort": "high"}}`. Existing request values are retained.
    """
    parameters = dict(payload)
    if effort is None or scaffold_carries_effort:
        return EffortResult(parameters, None)

    settings = (mapping or {}).get(effort)
    if settings is None:
        return EffortResult(parameters, None)
    for key, value in settings.items():
        parameters.setdefault(key, value)
    return EffortResult(parameters, effort)
