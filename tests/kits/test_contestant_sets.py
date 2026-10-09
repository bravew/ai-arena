from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from arena.kits.loading import load_kit
from arena.kits.matrix import expand_matrix, pair_ablations

ROOT = Path(__file__).resolve().parents[2]
SETS = ROOT / "contestants" / "sets"
KIT_MANIFEST = ROOT / "kits" / "team-coding" / "kit.yaml"


def read_matrix(name: str) -> dict[str, Any]:
    raw: Any = yaml.safe_load((SETS / name).read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return cast(dict[str, Any], raw)


def test_contestant_sets_have_expected_counts_and_unique_ids() -> None:
    kit_hash = load_kit(KIT_MANIFEST).hash
    model_axis = expand_matrix(read_matrix("model-axis.yaml"))
    product_axis = expand_matrix(read_matrix("product-axis.yaml"))
    ablation = expand_matrix(read_matrix("ablation.yaml"))

    assert len(model_axis) == 8
    assert len({contestant.id for contestant in model_axis}) == 8
    assert {contestant.scaffold.id for contestant in model_axis if contestant.scaffold} == {
        "opencode",
        "pi",
    }
    assert all(contestant.scaffold_prompt == "pinned" for contestant in model_axis)

    assert len(product_axis) == 3
    assert len({contestant.id for contestant in product_axis}) == 3
    assert {
        (str(contestant.model), contestant.scaffold.id)
        for contestant in product_axis
        if contestant.scaffold
    } == {
        ("anthropic/claude-opus-5-5:high", "claude-code"),
        ("openai/gpt-x:medium", "codex-cli"),
        ("google/gemini-3-pro:high", "gemini-cli"),
    }

    assert len(ablation) == 2
    assert len({contestant.id for contestant in ablation}) == 2
    pairs = pair_ablations(ablation)
    assert len(pairs) == 1
    assert pairs[0].baseline.kit_hash == "none"
    assert pairs[0].treatment.kit_hash == kit_hash
    baseline = pairs[0].baseline.resolved_config()
    treatment = pairs[0].treatment.resolved_config()
    baseline.pop("kit_hash")
    treatment.pop("kit_hash")
    assert baseline == treatment
