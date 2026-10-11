"""Adapters that run a vendor CLI as a subprocess on the user's own subscription sign-in."""

from __future__ import annotations

from arena.agents.local.claude import ClaudeLocalAdapter
from arena.agents.local.codex import CodexLocalAdapter, served_model_from_rollout
from arena.agents.local.signin import SignInError, claude_sign_in, codex_sign_in
from arena.agents.local.types import LocalCliAdapter, SignIn, StreamSummary, Usage

__all__ = [
    "ClaudeLocalAdapter",
    "CodexLocalAdapter",
    "LocalCliAdapter",
    "SignIn",
    "SignInError",
    "StreamSummary",
    "Usage",
    "claude_sign_in",
    "codex_sign_in",
    "served_model_from_rollout",
]
