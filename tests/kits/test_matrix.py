from __future__ import annotations

from arena.core.modelref import ModelRef
from arena.core.models import Scaffold
from arena.kits.matrix import expand_matrix, pair_ablations


def config() -> dict[str, object]:
    return {
        "matrix": {
            "model": ["anthropic/claude-opus-5-5:high", "openai/gpt-x:medium"],
            "scaffold": [
                {"id": "claude-code", "version": "2.4.1"},
                {"id": "codex-cli", "version": "0.70.0"},
            ],
            "kit": ["none", "team@v1"],
        },
        "kits": {"team@v1": "kit-hash-v1"},
        "exclude": [
            {
                "model": "openai/gpt-x:medium",
                "scaffold": {"id": "codex-cli", "version": "0.70.0"},
                "kit": "kit-hash-v1",
            }
        ],
        "ablation": "kit",
        "scaffold_prompt": "pinned",
    }


def test_matrix_expands_axes_applies_exclusion_and_unique_ids() -> None:
    contestants = expand_matrix(config())
    assert len(contestants) == 7
    assert len({contestant.id for contestant in contestants}) == 7
    assert all(contestant.scaffold_prompt == "pinned" for contestant in contestants)
    assert not any(
        contestant.model == ModelRef.parse("openai/gpt-x:medium")
        and contestant.scaffold == Scaffold(id="codex-cli", version="0.70.0")
        and contestant.kit_hash == "kit-hash-v1"
        for contestant in contestants
    )


def test_ablation_pairs_only_contestants_differing_by_kit() -> None:
    contestants = expand_matrix(config())
    pairs = pair_ablations(contestants)
    assert len(pairs) == 3
    for pair in pairs:
        assert pair.baseline.kit_hash == "none"
        assert pair.treatment.kit_hash == "kit-hash-v1"
        assert pair.baseline.id != pair.treatment.id
        left = pair.baseline.resolved_config()
        right = pair.treatment.resolved_config()
        assert left.pop("kit_hash") == "none"
        assert right.pop("kit_hash") == "kit-hash-v1"
        assert left == right


def test_matrix_yaml_string_is_supported() -> None:
    contestants = expand_matrix(
        """
matrix:
  model: [anthropic/claude-opus-5-5]
  scaffold: [none]
  kit: [none]
"""
    )
    assert len(contestants) == 1
    assert contestants[0].scaffold is None
