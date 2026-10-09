"""Contestant matrix expansion and kit ablation pairing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import product
from typing import Any, cast

import yaml
from pydantic import ValidationError

from arena.core.ids import canonical_json
from arena.core.modelref import ModelRef
from arena.core.models import Contestant, Scaffold


@dataclass(frozen=True)
class AblationPair:
    """Two contestants sharing every identity field except their kit hash."""

    baseline: Contestant
    treatment: Contestant


def _scaffold(value: Any) -> Scaffold | None:
    if value is None or value == "none":
        return None
    if isinstance(value, Scaffold):
        return value
    if isinstance(value, Mapping):
        return Scaffold.model_validate(value)
    raise ValueError(f"invalid scaffold value: {value!r}")


def _model(value: Any) -> ModelRef:
    return value if isinstance(value, ModelRef) else ModelRef.parse(str(value))


def _exclusion_value(key: str, value: Any, kits: Mapping[str, str]) -> Any:
    if key == "model":
        return str(_model(value))
    if key == "scaffold":
        scaffold = _scaffold(value)
        return None if scaffold is None else scaffold.model_dump(mode="json")
    if key == "kit":
        return _kit_hash(value, kits)
    return value


def _kit_hash(value: Any, kits: Mapping[str, str]) -> str:
    if value is None or value == "none":
        return "none"
    if isinstance(value, str):
        if value in kits:
            return kits[value]
        if value in kits.values():
            return value
        raise ValueError(f"unknown kit {value!r}; provide its content hash in `kits`")
    raise ValueError(f"invalid kit value: {value!r}")


def expand_matrix(
    config: Mapping[str, Any] | str,
    *,
    kits: Mapping[str, str] | None = None,
) -> list[Contestant]:
    """Expand a matrix YAML string or mapping into distinct stable contestants."""
    if isinstance(config, str):
        try:
            parsed: Any = yaml.safe_load(config)
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid matrix YAML: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("matrix config must contain a mapping")
        config = cast(Mapping[str, Any], parsed)
    axes_value = config.get("matrix")
    if not isinstance(axes_value, Mapping):
        raise ValueError("matrix config must define a `matrix` mapping")
    axes = cast(Mapping[str, Any], axes_value)
    models = axes.get("model")
    scaffolds = axes.get("scaffold")
    kit_axis = axes.get("kit", config.get("kit", ["none"]))
    if not isinstance(models, list) or not models:
        raise ValueError("matrix.model must be a non-empty list")
    if not isinstance(scaffolds, list) or not scaffolds:
        raise ValueError("matrix.scaffold must be a non-empty list")
    model_values = cast(list[Any], models)
    scaffold_values = cast(list[Any], scaffolds)
    kit_values = [kit_axis] if not isinstance(kit_axis, list) else cast(list[Any], kit_axis)
    if not kit_values:
        raise ValueError("matrix.kit must not be empty")

    inherited = {
        key: config[key]
        for key in ("params", "orchestration", "prompt_version", "hooks", "scaffold_prompt")
        if key in config
    }
    excluded_value = config.get("exclude", [])
    if not isinstance(excluded_value, list):
        raise ValueError("exclude must be a list of mappings")
    raw_exclusions = cast(list[Any], excluded_value)
    if not all(isinstance(row, Mapping) for row in raw_exclusions):
        raise ValueError("exclude must be a list of mappings")
    excluded = cast(list[Mapping[str, Any]], raw_exclusions)
    kit_map = cast(Mapping[str, str], config.get("kits", kits or {}))
    expanded: list[Contestant] = []
    for model, scaffold_value, kit_value in product(model_values, scaffold_values, kit_values):
        scaffold = _scaffold(scaffold_value)
        resolved = {
            "model": _model(model),
            "scaffold": scaffold,
            "kit_hash": _kit_hash(kit_value, kit_map),
            **inherited,
        }
        excluded_combo = {
            "model": str(resolved["model"]),
            "scaffold": _exclusion_value("scaffold", scaffold_value, kit_map),
            "kit": _kit_hash(kit_value, kit_map),
        }
        if any(
            all(
                _exclusion_value(key, row[key], kit_map) == excluded_combo[key]
                for key in row
                if key in excluded_combo
            )
            and all(key in excluded_combo for key in row)
            for row in excluded
        ):
            continue
        try:
            expanded.append(Contestant.model_validate(resolved))
        except ValidationError as exc:
            raise ValueError(f"invalid contestant combination {resolved}: {exc}") from exc
    ids = [contestant.id for contestant in expanded]
    if len(set(ids)) != len(ids):
        raise ValueError("matrix expands to duplicate contestant identities")
    return expanded


def pair_ablations(contestants: Iterable[Contestant]) -> list[AblationPair]:
    """Pair contestants differing only by kit hash, using `none` as the baseline."""
    all_contestants = list(contestants)
    baselines = [contestant for contestant in all_contestants if contestant.kit_hash == "none"]
    treatments = [contestant for contestant in all_contestants if contestant.kit_hash != "none"]
    by_config = {
        canonical_json(contestant.resolved_config()): contestant for contestant in baselines
    }
    pairs: list[AblationPair] = []
    for treatment in treatments:
        expected = treatment.resolved_config()
        expected["kit_hash"] = "none"
        baseline = by_config.get(canonical_json(expected))
        if baseline is not None:
            pairs.append(AblationPair(baseline=baseline, treatment=treatment))
    return pairs
