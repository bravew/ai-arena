"""Pi coding agent adapter for trial containers (DEV_PLAN §6.2)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from shlex import quote
from typing import Any, cast

from arena.agents.base import AgentBox, GatewayEndpoint, KitInstall, NativeTranscript, WiringCheck
from arena.core.modelref import ModelRef
from arena.core.models import Call, Session, SkillEvent, Task, ToolCall, Turn
from arena.kits.install import KitTarget, McpEntry, install_kit

IMAGE = "arena-pi:0.99.2"
VERSION = "0.99.2"
HOME = "/home/agent"
AGENT_DIR = f"{HOME}/.pi/agent"
MODELS_CONFIG = f"{AGENT_DIR}/models.json"
MCP_CONFIG = f"{AGENT_DIR}/mcp.json"
INSTRUCTIONS = f"{HOME}/AGENTS.md"
SKILLS_DIR = f"{AGENT_DIR}/skills"
TRANSCRIPT = "/tmp/arena-pi-transcript.jsonl"
SYSTEM_PROMPT = "You are a coding agent. Complete the user's task using the available tools."


def _mcp_config(entries: Sequence[McpEntry]) -> Sequence[tuple[str, str]]:
    """Render Pi's MCP server configuration after kit URLs are routed through the gateway."""
    servers: dict[str, Any] = {}
    for entry in entries:
        if entry.url:
            servers[entry.name] = {
                "url": entry.url,
                "headers": dict(entry.headers or {}),
            }
        else:
            servers[entry.name] = {"command": entry.command, "args": list(entry.args)}
    return ((MCP_CONFIG, json.dumps({"mcpServers": servers}, sort_keys=True, indent=2) + "\n"),)


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _skill_name(path: str) -> str | None:
    marker = "/skills/"
    if marker not in path or not path.endswith("/SKILL.md"):
        return None
    return path.split(marker, maxsplit=1)[1].split("/", maxsplit=1)[0]


class PiAdapter:
    """Wire Pi to the trial gateway without touching host agent configuration."""

    id = "pi"
    protocol = "chat"
    image = IMAGE

    def __init__(self, kit_root: Path | None = None) -> None:
        self.kit_root = kit_root
        self.gateway: GatewayEndpoint | None = None
        self.model_id: str | None = None
        self.scaffold_prompt = "native"

    def version(self, box: AgentBox) -> str:
        version = box.execute(("pi", "--version")).strip()
        parsed = _version_tuple(version)
        if parsed is None:
            raise ValueError(f"could not determine Pi version from {version!r}")
        if parsed < (0, 99, 0):
            raise ValueError(
                f"Pi {version} is unsupported; version 0.99.0 or newer is required for native MCP"
            )
        return version

    def wire(self, box: AgentBox, gw: GatewayEndpoint, model: ModelRef) -> None:
        self.gateway = gw
        self.model_id = model.model
        provider = {
            "baseUrl": f"{gw.url.rstrip('/')}/v1",
            "apiKey": gw.token,
            "api": "openai-completions",
            "models": [
                {
                    "id": model.model,
                    "name": model.model,
                    "contextWindow": 200000,
                    "maxTokens": 8192,
                    "input": ["text"],
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                }
            ],
        }
        box.write_text(
            MODELS_CONFIG,
            json.dumps({"providers": {"arena": provider}}, sort_keys=True, indent=2) + "\n",
        )

    def install_kit(self, box: AgentBox, kit: Any) -> KitInstall:
        if self.kit_root is None:
            raise ValueError("Pi kit installation requires an explicit resolved kit root")
        if self.gateway is None:
            raise ValueError("Pi must be wired before installing a kit")
        target = KitTarget(
            agent_id=self.id,
            instructions_path=INSTRUCTIONS,
            skills_dir=SKILLS_DIR,
            mcp=_mcp_config,
        )
        return install_kit(box, kit, self.kit_root, target, self.gateway)

    def command(self, task: Task, settings: Mapping[str, Any]) -> list[str]:
        prompt_path = quote(task.prompt_file)
        model_id = quote(self.model_id or str(settings.get("model", "")))
        transcript_path = quote(TRANSCRIPT)
        pinned = settings.get("scaffold_prompt") == "pinned"
        self.scaffold_prompt = "pinned" if pinned else "native"
        system_prompt = f" --system-prompt {quote(SYSTEM_PROMPT)}" if pinned else ""
        return [
            "sh",
            "-lc",
            "export HOME=/home/agent PI_CODING_AGENT_DIR=/home/agent/.pi/agent; "
            f"prompt=$(cat {prompt_path}) || exit $?; "
            f"pi --print --mode json --model {quote('arena/' + (self.model_id or model_id))}"
            f'{system_prompt} "$prompt" > {transcript_path}; '
            f"status=$?; cat {transcript_path}; exit $status",
        ]

    def collect(self, box: AgentBox) -> NativeTranscript:
        return NativeTranscript(content=box.read_text(TRANSCRIPT))

    def sessions(self, native: NativeTranscript, calls: Sequence[Call]) -> list[Session]:
        del calls  # Call association is performed by the shared session assembler (#29).
        rows: list[dict[str, Any]] = []
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
            rows.append(cast(dict[str, Any], value))

        session_id = next(
            (str(row[key]) for row in rows for key in ("session_id", "sessionId") if row.get(key)),
            "pi-session",
        )
        turns: list[Turn] = []
        tool_calls: list[ToolCall] = []
        skill_events: list[SkillEvent] = []
        for row in rows:
            kind = row.get("type")
            if kind == "turn_start":
                if tool_calls or skill_events:
                    turns.append(Turn(tool_calls=tool_calls, skill_events=skill_events))
                    tool_calls, skill_events = [], []
            elif kind in {"tool_execution_start", "tool_call"}:
                name = str(row.get("toolName", row.get("name", "unknown")))
                args_value = row.get("args", row.get("input", {}))
                args: dict[str, Any] = (
                    cast(dict[str, Any], args_value) if isinstance(args_value, dict) else {}
                )
                tool_calls.append(
                    ToolCall(
                        name=name,
                        args_digest=hashlib.sha256(
                            json.dumps(args, sort_keys=True, separators=(",", ":")).encode()
                        ).hexdigest(),
                    )
                )
                if name in {"read", "read_file"}:
                    skill = _skill_name(str(args.get("path", args.get("filePath", ""))))
                    if skill:
                        skill_events.append(SkillEvent(kind="loaded", skill=skill, kit_hash="none"))
                if name == "skill":
                    skill_events.append(
                        SkillEvent(
                            kind="invoked",
                            skill=str(args.get("name", "unknown")),
                            kit_hash="none",
                        )
                    )
            elif kind == "skill_list":
                values = row.get("skills", [])
                if isinstance(values, list):
                    skill_names = cast(list[Any], values)
                    skill_events.extend(
                        SkillEvent(kind="listed", skill=name, kit_hash="none")
                        for name in skill_names
                        if isinstance(name, str)
                    )
            elif kind == "turn_end":
                turns.append(Turn(tool_calls=tool_calls, skill_events=skill_events))
                tool_calls, skill_events = [], []
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
            config = json.loads(box.read_text(MODELS_CONFIG))
            providers = cast(dict[str, Any], config["providers"])
            provider = cast(dict[str, Any], providers["arena"])
            models = provider["models"]
            valid = (
                provider["baseUrl"].endswith("/v1")
                and bool(provider["apiKey"])
                and provider["api"] == "openai-completions"
                and isinstance(models, list)
                and any(
                    isinstance(model, dict)
                    and cast(dict[str, Any], model).get("id") == self.model_id
                    for model in cast(list[Any], models)
                )
            )
            return WiringCheck(ok=valid, detail="Pi gateway config validated")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return WiringCheck(ok=False, detail=f"invalid Pi models.json: {error}")
