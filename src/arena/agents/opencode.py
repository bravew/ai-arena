"""OpenCode adapter for trial containers (DEV_PLAN §6.2)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from shlex import quote
from typing import Any, cast

from arena.agents.base import (
    AgentBox,
    GatewayEndpoint,
    KitInstall,
    NativeTranscript,
    WiringCheck,
)
from arena.core.modelref import ModelRef
from arena.core.models import Call, Session, SkillEvent, Task, ToolCall, Turn
from arena.kits.install import KitTarget, McpEntry, install_kit

IMAGE = "ghcr.io/anomalyco/opencode:1.2.18"
HOME = "/home/agent"
CONFIG = f"{HOME}/.config/opencode/opencode.json"
TRANSCRIPT = "/tmp/arena-opencode-transcript.jsonl"
SYSTEM_PROMPT = "You are a coding agent. Complete the user's task using the available tools."


def _mcp_config(box: AgentBox, entries: Sequence[McpEntry]) -> Sequence[tuple[str, str]]:
    """Merge kit MCP entries into the config OpenCode reads."""
    config = json.loads(box.read_text(CONFIG))
    servers: dict[str, Any] = config.setdefault("mcp", {})
    for entry in entries:
        if entry.url:
            servers[entry.name] = {
                "type": "remote",
                "url": entry.url,
                "enabled": True,
                "headers": dict(entry.headers or {}),
            }
        else:
            servers[entry.name] = {
                "type": "local",
                "command": [entry.command, *entry.args],
                "enabled": True,
            }
    return ((CONFIG, json.dumps(config, sort_keys=True, indent=2) + "\n"),)


class OpenCodeAdapter:
    """Wire the pinned OpenCode CLI to the arena gateway inside a trial container."""

    id = "opencode"
    protocol = "chat"
    image = IMAGE

    def __init__(self, kit_root: Path | None = None) -> None:
        """Accept resolved kit source root (the shared runner does not pass it yet)."""
        self.kit_root = kit_root
        self.gateway: GatewayEndpoint | None = None

    def version(self, box: AgentBox) -> str:
        return box.execute(("opencode", "--version")).strip()

    def wire(self, box: AgentBox, gw: GatewayEndpoint, model: ModelRef) -> None:
        self.gateway = gw
        model_id = model.model
        config = {
            "$schema": "https://opencode.ai/config.json",
            "model": f"arena/{model_id}",
            "provider": {
                "arena": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Arena gateway",
                    "options": {"baseURL": f"{gw.url.rstrip('/')}/v1", "apiKey": gw.token},
                    "models": {
                        model_id: {
                            "name": model_id,
                            "id": model_id,
                            "limit": {"context": 200000, "output": 8192},
                        }
                    },
                }
            },
            "agent": {
                "arena": {
                    "mode": "primary",
                    "prompt": SYSTEM_PROMPT,
                    "permission": {
                        "edit": "allow",
                        "bash": "allow",
                        "read": "allow",
                        "write": "allow",
                    },
                }
            },
        }
        box.write_text(CONFIG, json.dumps(config, sort_keys=True, indent=2) + "\n")

    def install_kit(self, box: AgentBox, kit: Any) -> KitInstall:
        if self.kit_root is None:
            raise ValueError("OpenCode kit installation requires an explicit resolved kit root")
        if self.gateway is None:
            raise ValueError("OpenCode must be wired before installing a kit")
        target = KitTarget(
            agent_id=self.id,
            instructions_path=f"{HOME}/AGENTS.md",
            # OpenCode reads Claude Code skills too, so stage each skill only once.
            skills_dir=f"{HOME}/.claude/skills",
            mcp=lambda entries: _mcp_config(box, entries),
        )
        return install_kit(box, kit, self.kit_root, target, self.gateway)

    def command(self, task: Task, settings: Mapping[str, Any]) -> list[str]:
        del settings  # `wire()` selected the model in opencode.json.
        prompt_path = quote(task.prompt_file)
        transcript_path = quote(TRANSCRIPT)
        return [
            "sh",
            "-lc",
            "export HOME=/home/agent; "
            f"prompt=$(cat {prompt_path}) || exit $?; "
            f'opencode run --format json --agent arena "$prompt" > {transcript_path}; '
            f"status=$?; cat {transcript_path}; exit $status",
        ]

    def collect(self, box: AgentBox) -> NativeTranscript:
        return NativeTranscript(content=box.read_text(TRANSCRIPT))

    def sessions(self, native: NativeTranscript, calls: Sequence[Call]) -> list[Session]:
        del calls
        records: list[dict[str, Any]] = []
        partial = False
        for line in native.content.splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                partial = True
                break
            if not isinstance(value, dict):
                partial = True
                break
            records.append(cast(dict[str, Any], value))

        session_id = next(
            (str(row["sessionID"]) for row in records if row.get("sessionID")),
            "opencode-session",
        )
        turns: list[Turn] = []
        tool_calls: list[ToolCall] = []
        skill_events: list[SkillEvent] = []
        for row in records:
            event_type = row.get("type")
            if event_type == "step_start":
                if tool_calls or skill_events:
                    turns.append(Turn(tool_calls=tool_calls, skill_events=skill_events))
                    tool_calls, skill_events = [], []
                continue
            part_value = row.get("part", {})
            part = cast(dict[str, Any], part_value) if isinstance(part_value, dict) else {}
            if event_type == "tool_use":
                name = str(part.get("tool", part.get("name", "unknown")))
                state_value = part.get("state", {})
                state = cast(dict[str, Any], state_value) if isinstance(state_value, dict) else {}
                args_value = state.get("input", {})
                args = cast(dict[str, Any], args_value) if isinstance(args_value, dict) else {}
                tool_calls.append(
                    ToolCall(
                        name=name,
                        args_digest=hashlib.sha256(
                            json.dumps(args, sort_keys=True).encode()
                        ).hexdigest(),
                    )
                )
                if name == "skill":
                    skill_name = str(args.get("name", "unknown"))
                    skill_events.append(
                        SkillEvent(kind="invoked", skill=skill_name, kit_hash="none")
                    )
                if name in {"read", "read_file"}:
                    path = str(args.get("filePath", args.get("path", "")))
                    skill = _skill_name(path)
                    if skill:
                        skill_events.append(SkillEvent(kind="loaded", skill=skill, kit_hash="none"))
            elif event_type == "text":
                text_value = part.get("text")
                text = text_value if isinstance(text_value, str) else None
                if isinstance(text, str):
                    skill_events.extend(_listed_skills(text))
            elif event_type == "tool_result":
                tool = str(part.get("tool", part.get("name", "")))
                state_value = part.get("state", {})
                state = cast(dict[str, Any], state_value) if isinstance(state_value, dict) else {}
                args_value = state.get("input", {})
                args = cast(dict[str, Any], args_value) if isinstance(args_value, dict) else {}
                if tool in {"read", "read_file"}:
                    path = str(args.get("filePath", args.get("path", "")))
                    skill = _skill_name(path)
                    if skill:
                        skill_events.append(SkillEvent(kind="loaded", skill=skill, kit_hash="none"))
        if tool_calls or skill_events or not turns:
            turns.append(Turn(tool_calls=tool_calls, skill_events=skill_events))
        return [
            Session(
                id=session_id,
                trial_id="",
                agent=self.id,
                native_session_id=session_id,
                status="partial" if partial else "complete",
                turns=turns,
            )
        ]

    def check(self, box: AgentBox) -> WiringCheck:
        try:
            config = json.loads(box.read_text(CONFIG))
            options = config["provider"]["arena"]["options"]
            model_name = config["model"].split("/", maxsplit=1)[1]
            prompt = config["agent"]["arena"]["prompt"]
            valid = (
                options["baseURL"].endswith("/v1")
                and bool(options["apiKey"])
                and model_name in config["provider"]["arena"]["models"]
                and prompt == SYSTEM_PROMPT
            )
            return WiringCheck(ok=valid, detail="OpenCode gateway config validated")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return WiringCheck(ok=False, detail=f"invalid opencode.json: {error}")


def _listed_skills(text: str) -> list[SkillEvent]:
    """Recognize structured skill-list metadata when present in a text event."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, dict):
        return []
    parsed = cast(dict[str, Any], value)
    if parsed.get("type") != "skill_list":
        return []
    skills = parsed.get("skills", [])
    if not isinstance(skills, list):
        return []
    skill_names = cast(list[Any], skills)
    return [
        SkillEvent(kind="listed", skill=name, kit_hash="none")
        for name in skill_names
        if isinstance(name, str)
    ]


def _skill_name(path: str) -> str | None:
    marker = "/skills/"
    if marker not in path or not path.endswith("/SKILL.md"):
        return None
    return path.split(marker, maxsplit=1)[1].split("/", maxsplit=1)[0]
