"""Claude Code run as the user's own `claude` binary on a subscription sign-in."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from arena.agents.local.types import StreamSummary, Usage

# Narrowest tool set that lets a writing or analysis task finish. Shell access is not granted.
ALLOWED_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")
CREDENTIALS_FILE = ".claude/.credentials.json"
_LIMIT = re.compile(r"usage limit|rate limit|limit reached|too many requests|\b429\b", re.I)
_AUTH = re.compile(
    r"not logged in|please run /login|invalid api key|oauth token has expired|"
    r"authentication|unauthorized|\b401\b",
    re.I,
)


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _usage(raw: Any) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    data = cast(dict[str, Any], raw)
    return Usage(
        input_tokens=_int(data.get("input_tokens")),
        output_tokens=_int(data.get("output_tokens")),
        cache_read_tokens=_int(data.get("cache_read_input_tokens")),
        cache_write_tokens=_int(data.get("cache_creation_input_tokens")),
    )


class ClaudeLocalAdapter:
    runtime = "claude"

    def command(self, binary: str, model: str, prompt: str, workspace: Path) -> list[str]:
        return [
            binary,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            model,
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            *ALLOWED_TOOLS,
            "--no-session-persistence",
            "--",
            prompt,
        ]

    def home_environment(self, home: Path, model: str) -> dict[str, str]:
        # The helper-model roles are pinned to the contestant so a cheaper model cannot
        # answer part of the trial unnoticed.
        return {
            "HOME": str(home),
            "CLAUDE_CONFIG_DIR": str(home / ".claude"),
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
            "ANTHROPIC_SMALL_FAST_MODEL": model,
        }

    def parse(self, lines: Sequence[str]) -> StreamSummary:
        events = 0
        served: str | None = None
        usage: Usage | None = None
        text = ""
        limit = auth = False
        error: str | None = None
        auxiliary: set[str] = set()
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
            if kind == "system" and event.get("subtype") == "init":
                served = served or _str(event.get("model"))
            elif kind == "assistant":
                message = event.get("message")
                if isinstance(message, dict):
                    served = served or _str(cast(dict[str, Any], message).get("model"))
            elif kind == "rate_limit_event":
                info = event.get("rate_limit_info")
                if (
                    isinstance(info, dict)
                    and cast(dict[str, Any], info).get("status") == "rejected"
                ):
                    limit = True
            elif kind == "result":
                usage = _usage(event.get("usage")) or usage
                result_text = _str(event.get("result")) or ""
                text = result_text or text
                by_model = event.get("modelUsage")
                if isinstance(by_model, dict):
                    auxiliary.update(str(name) for name in cast(dict[str, Any], by_model))
                if event.get("is_error") is True:
                    error = result_text or str(event.get("subtype") or "error")
                    limit = limit or bool(_LIMIT.search(error))
                    auth = auth or bool(_AUTH.search(error))
        if served is not None:
            auxiliary.discard(served)
        return StreamSummary(
            events=events,
            model_served=served,
            usage=usage,
            final_text=text,
            transcript="\n".join(lines),
            limit_hit=limit,
            auth_failed=auth,
            error=error,
            auxiliary_models=tuple(sorted(auxiliary)),
        )


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
