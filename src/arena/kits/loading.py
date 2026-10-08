"""Load a kit manifest, pin Git skills, and compute its content identity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from arena.core.models import Kit
from arena.kits.git import GitResolver, pin_skill_source
from arena.kits.hashing import hash_kit


def load_kit(
    manifest: Path,
    *,
    resolver: GitResolver | None = None,
) -> Kit:
    """Parse `kit.yaml`, pin Git sources, and return a kit with its computed hash."""
    try:
        raw: Any = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read kit manifest {manifest}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"kit manifest {manifest} must contain a mapping")
    try:
        kit = Kit.model_validate(raw | {"hash": "pending"})
    except ValidationError as exc:
        raise ValueError(f"invalid kit manifest {manifest}: {exc}") from exc

    if any(skill.git for skill in kit.skills):
        if resolver is None:
            from arena.kits.git import SubprocessGitResolver

            resolver = SubprocessGitResolver()
        pinned_skills = [pin_skill_source(skill, resolver) for skill in kit.skills]
        kit = kit.model_copy(update={"skills": pinned_skills})

    return kit.model_copy(update={"hash": hash_kit(kit, manifest.parent)})
