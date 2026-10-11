from __future__ import annotations

import pytest

from arena.agents.aider import AiderAdapter
from arena.agents.claude_code import ClaudeCodeAdapter
from arena.agents.codex_cli import CodexCliAdapter
from arena.agents.opencode import OpenCodeAdapter
from arena.agents.pi import PiAdapter
from arena.core.modelref import ModelRef
from arena.core.models import Contestant, Scaffold
from arena.runners.agent_trial import adapter_for


@pytest.mark.parametrize(
    ("scaffold_id", "adapter_type"),
    [
        ("aider", AiderAdapter),
        ("claude-code", ClaudeCodeAdapter),
        ("codex-cli", CodexCliAdapter),
        ("opencode", OpenCodeAdapter),
        ("pi", PiAdapter),
    ],
)
def test_adapter_for_selects_configured_scaffold(scaffold_id: str, adapter_type: type) -> None:
    contestant = Contestant(
        model=ModelRef.parse("anthropic/claude-opus-5-5"),
        scaffold=Scaffold(id=scaffold_id, version="pinned"),
    )

    assert isinstance(adapter_for(contestant), adapter_type)


def test_adapter_for_rejects_missing_or_unsupported_scaffold() -> None:
    no_scaffold = Contestant(model=ModelRef.parse("anthropic/claude-opus-5-5"))
    unsupported = Contestant(
        model=ModelRef.parse("anthropic/claude-opus-5-5"),
        scaffold=Scaffold(id="unknown", version="pinned"),
    )

    with pytest.raises(ValueError, match="requires a configured scaffold"):
        adapter_for(no_scaffold)
    with pytest.raises(ValueError, match="unsupported agent scaffold"):
        adapter_for(unsupported)
