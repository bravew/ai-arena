"""Validate the YAML documents a user writes: providers, catalog, suites, contestants, kits."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from arena.catalog.config import ModelCatalog
from arena.core.modelref import ModelRef
from arena.core.models import Contestant, Kit, Scaffold, Task
from arena.kits.matrix import expand_matrix
from arena.providers.config import ProviderConfig, format_validation_error

SUPPORTED = "providers.yaml, models.yaml, task.yaml, kit.yaml, rubrics/*.yaml, contestants/*.yaml"


def _mapping(raw: Any) -> dict[str, Any] | None:
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else None


def _task(path: Path, raw: dict[str, Any]) -> list[str]:
    task = Task.model_validate(raw)
    if not (path.parent / task.prompt_file).is_file():
        return [f"{path}:prompt_file: {task.prompt_file} does not exist next to task.yaml"]
    return []


def _kit(path: Path, raw: dict[str, Any]) -> list[str]:
    kit = Kit.model_validate(raw | {"hash": "pending"})
    issues: list[str] = []
    if kit.instructions and not (path.parent / kit.instructions).is_file():
        issues.append(f"{path}:instructions: {kit.instructions} does not exist")
    for index, skill in enumerate(kit.skills):
        if skill.path and not (path.parent / skill.path).is_dir():
            issues.append(f"{path}:skills[{index}].path: {skill.path} is not a directory")
    return issues


def _contestant(path: Path, raw: dict[str, Any]) -> list[str]:
    if "matrix" in raw:
        try:
            expand_matrix(raw)
        except ValueError as error:
            return [f"{path}:matrix: {error}"]
        return []
    config = dict(raw)
    if "id_label" in config:
        config["label"] = config.pop("id_label")
    config["model"] = ModelRef.parse(str(config.get("model", "")))
    scaffold = config.get("scaffold")
    if scaffold is None or scaffold == "none":
        config["scaffold"] = None
    elif isinstance(scaffold, dict):
        config["scaffold"] = Scaffold.model_validate(scaffold)
    Contestant.model_validate(config)
    return []


def _rubric(path: Path, raw: dict[str, Any]) -> list[str]:
    issues = [f"{path}:{key}: required" for key in ("id", "version", "kind") if key not in raw]
    anchors = _mapping(raw.get("anchors"))
    if not anchors:
        issues.append(f"{path}:anchors: required non-empty mapping of criterion to level text")
    return issues


def validate_document(path: Path, raw: Any) -> list[str]:
    """Return the problems in one parsed YAML document, chosen by its file name and location."""
    mapping = _mapping(raw)
    if mapping is None:
        return [f"{path}:<root>: expected a mapping"]
    try:
        if path.name == "providers.yaml":
            ProviderConfig.model_validate(mapping)
        elif path.name == "models.yaml":
            ModelCatalog.model_validate(mapping)
        elif path.name == "task.yaml":
            return _task(path, mapping)
        elif path.name == "kit.yaml":
            return _kit(path, mapping)
        elif "rubrics" in path.parts:
            return _rubric(path, mapping)
        elif "contestants" in path.parts:
            return _contestant(path, mapping)
        else:
            return [f"{path}:<root>: unsupported config file (expected {SUPPORTED})"]
    except ValidationError as error:
        return format_validation_error(path, error)
    except ValueError as error:
        return [f"{path}:<root>: {error}"]
    return []
