"""Codex run as the user's own `codex` binary on a ChatGPT sign-in."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from arena.agents.local.types import StreamSummary, Usage

AUTH_FILE = ".codex/auth.json"
_LIMIT = re.compile(r"usage limit|rate limit|limit reached|too many requests|\b429\b", re.I)
_AUTH = re.compile(
    r"not logged in|log ?in again|sign in|unauthorized|authentication|\b401\b|token.*expired",
    re.I,
)


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class CodexLocalAdapter:
    runtime = "codex"

    def command(self, binary: str, model: str, prompt: str, workspace: Path) -> list[str]:
        return [
            binary,
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            # No `--ephemeral`: it stops Codex writing the session record the runner reads to
            # verify the served model. The record lands in the per-trial scratch home, which is
            # deleted on every outcome.
            "--cd",
            str(workspace),
            "--model",
            model,
            "--",
            prompt,
        ]

    def home_environment(self, home: Path, model: str) -> dict[str, str]:
        return {"HOME": str(home), "CODEX_HOME": str(home / ".codex")}

    def parse(self, lines: Sequence[str]) -> StreamSummary:
        events = 0
        usage: Usage | None = None
        text = ""
        limit = auth = False
        error: str | None = None
        for line in lines:
            try:
                raw: Any = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            event = cast(dict[str, Any], raw)
            events += 1
            kind = event.get("type")
            if kind == "item.completed":
                item = event.get("item")
                if isinstance(item, dict):
                    data = cast(dict[str, Any], item)
                    if data.get("type") == "agent_message" and isinstance(data.get("text"), str):
                        text = cast(str, data["text"])
            elif kind == "turn.completed":
                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    u = cast(dict[str, Any], raw_usage)
                    base = usage or Usage()
                    cached = _int(u.get("cached_input_tokens"))
                    usage = Usage(
                        input_tokens=base.input_tokens + _int(u.get("input_tokens")) - cached,
                        output_tokens=base.output_tokens + _int(u.get("output_tokens")),
                        cache_read_tokens=base.cache_read_tokens + cached,
                        cache_write_tokens=base.cache_write_tokens
                        + _int(u.get("cache_write_input_tokens")),
                        reasoning_tokens=base.reasoning_tokens
                        + _int(u.get("reasoning_output_tokens")),
                    )
            elif kind in {"turn.failed", "error"}:
                message = _message(event)
                error = message or str(kind)
                limit = limit or bool(_LIMIT.search(error))
                auth = auth or bool(_AUTH.search(error))
        return StreamSummary(
            events=events,
            # The event stream does not name the model served. The runner reads it from the
            # session record Codex writes under its own home when that is present.
            model_served=None,
            usage=usage,
            final_text=text,
            transcript="\n".join(lines),
            limit_hit=limit,
            auth_failed=auth,
            error=error,
        )


def served_model_from_rollout(content: str) -> str | None:
    """The model named in a Codex session record (`turn_context.payload.model`), if any."""
    for line in content.splitlines():
        try:
            raw: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        event = cast(dict[str, Any], raw)
        payload = event.get("payload")
        if event.get("type") == "turn_context" and isinstance(payload, dict):
            model = cast(dict[str, Any], payload).get("model")
            if isinstance(model, str) and model:
                return model
    return None


def _message(event: dict[str, Any]) -> str | None:
    error = event.get("error")
    if isinstance(error, dict):
        message = cast(dict[str, Any], error).get("message")
        if isinstance(message, str):
            return message
    message = event.get("message")
    return message if isinstance(message, str) else None
