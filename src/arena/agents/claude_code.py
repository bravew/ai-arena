"""Claude Code adapter for trial containers (DEV_PLAN §6.2)."""

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

VERSION = "2.4.1"
IMAGE = f"arena-claude-code:{VERSION}"
IMAGE_REF = "node:20-bookworm-slim"
HOME = "/home/agent"
CLAUDE_CONFIG = f"{HOME}/.claude.json"
TRANSCRIPT_GLOB = f"{HOME}/.claude/projects/**/*.jsonl"
TRANSCRIPT = "/tmp/arena-claude-code-transcript.jsonl"
INSTRUCTIONS = f"{HOME}/.claude/CLAUDE.md"
SKILLS_DIR = f"{HOME}/.claude/skills"
SYSTEM_PROMPT = "You are a coding agent. Complete the user's task using the available tools."


def _mcp_config(box: AgentBox, entries: Sequence[McpEntry]) -> Sequence[tuple[str, str]]:
    """Render the project-level Claude Code MCP configuration inside the container."""
    try:
        config = json.loads(box.read_text(CLAUDE_CONFIG))
    except (FileNotFoundError, KeyError):
        config = {}
    if "mcpServers" not in config:
        config["mcpServers"] = {}
    servers_value: object = cast(object, config["mcpServers"])
    if not isinstance(servers_value, dict):
        raise ValueError("Claude Code mcpServers config must be an object")
    servers = cast(dict[str, Any], servers_value)
    for entry in entries:
        if entry.url:
            servers[entry.name] = {
                "type": "http",
                "url": entry.url,
                "headers": dict(entry.headers or {}),
            }
        else:
            servers[entry.name] = {
                "type": "stdio",
                "command": entry.command,
                "args": list(entry.args),
            }
    return ((CLAUDE_CONFIG, json.dumps(config, sort_keys=True, indent=2) + "\n"),)


class ClaudeCodeAdapter:
    """Wire Claude Code to the trial gateway without accessing host configuration."""

    id = "claude-code"
    protocol = "anthropic"
    image = IMAGE

    def __init__(self, kit_root: Path | None = None, *, subscription: bool = False) -> None:
        self.kit_root = kit_root
        self.subscription = subscription
        self.gateway: GatewayEndpoint | None = None
        self.model_id: str | None = None
        self.environment: dict[str, str] = {}

    def version(self, box: AgentBox) -> str:
        value = box.execute(("claude", "--version")).strip()
        if VERSION not in value:
            raise ValueError(f"expected Claude Code {VERSION}, got {value!r}")
        return value

    def wire(self, box: AgentBox, gw: GatewayEndpoint, model: ModelRef) -> None:
        del box  # Credentials are injected only into the CLI process environment.
        self.gateway = gw
        self.model_id = model.model
        self.environment = {
            "ANTHROPIC_BASE_URL": gw.url.rstrip("/"),
            "ANTHROPIC_AUTH_TOKEN": gw.token,
        }
        if not self.subscription:
            self.environment["ANTHROPIC_MODEL"] = model.model
        return None

    def install_kit(self, box: AgentBox, kit: Any) -> KitInstall:
        if self.kit_root is None:
            raise ValueError("Claude Code kit installation requires an explicit resolved kit root")
        if self.gateway is None:
            raise ValueError("Claude Code must be wired before installing a kit")
        target = KitTarget(
            agent_id=self.id,
            instructions_path=INSTRUCTIONS,
            skills_dir=SKILLS_DIR,
            mcp=lambda entries: _mcp_config(box, entries),
        )
        return install_kit(box, kit, self.kit_root, target, self.gateway)

    def command(self, task: Task, settings: Mapping[str, Any]) -> list[str]:
        prompt_path = quote(task.prompt_file)
        transcript_path = quote(TRANSCRIPT)
        model_arg = "" if self.subscription else f" --model {quote(self.model_id or '')}"
        cwd_path = quote(HOME + "/.claude/projects")
        env_assignments = " ".join(
            f"{name}={quote(value)}" for name, value in self.environment.items()
        )
        system_arg = (
            f" --system-prompt {quote(SYSTEM_PROMPT)}"
            if settings.get("scaffold_prompt") == "pinned"
            else ""
        )
        return [
            "sh", "-lc",
            f"export HOME={quote(HOME)}; "
            f"prompt=$(cat {prompt_path}) || exit $?; "
            f"env {env_assignments} claude --print --output-format stream-json --verbose"
            f"{model_arg}{system_arg} \"$prompt\"; "
            "status=$?; "
            f"find {cwd_path} -name '*.jsonl' -type f -print0 2>/dev/null "
            f"| xargs -0 -r cat > {transcript_path}; "
            "exit $status",
        ]

    def collect(self, box: AgentBox) -> NativeTranscript | None:
        try:
            return NativeTranscript(content=box.read_text(TRANSCRIPT))
        except (FileNotFoundError, OSError):
            return None

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
            (str(row[key]) for row in rows for key in ("sessionId", "session_id") if row.get(key)),
            "claude-code-session",
        )
        turns: list[Turn] = []
        tools: list[ToolCall] = []
        skills: list[SkillEvent] = []
        for row in rows:
            kind = row.get("type")
            if kind == "assistant":
                message = _mapping(row.get("message"))
                content = message.get("content")
                blocks = cast(list[Any], content) if isinstance(content, list) else []
                for raw in blocks:
                    block = _mapping(raw)
                    if block.get("type") != "tool_use":
                        continue
                    name = str(block.get("name", "unknown"))
                    args = _mapping(block.get("input"))
                    tools.append(ToolCall(
                        name=name,
                        args_digest=hashlib.sha256(
                            json.dumps(args, sort_keys=True, separators=(",", ":")).encode()
                        ).hexdigest(),
                    ))
                    if name == "Skill":
                        skill = args.get("skill")
                        if isinstance(skill, str):
                            skills.append(SkillEvent(kind="invoked", skill=skill, kit_hash="none"))
                    if name in {"Read", "read"}:
                        skill = _skill_name(str(args.get("file_path", args.get("filePath", ""))))
                        if skill:
                            skills.append(SkillEvent(kind="loaded", skill=skill, kit_hash="none"))
                    if name in {"Glob", "glob"}:
                        pattern = str(args.get("pattern", ""))
                        search_path = str(args.get("path", ""))
                        skill = _skill_name(pattern) or _skill_name(
                            f"{search_path.rstrip('/')}/{pattern.lstrip('/')}"
                        )
                        if skill:
                            skills.append(SkillEvent(kind="listed", skill=skill, kit_hash="none"))
            elif kind in {"queue-operation", "turn"}:
                if tools or skills:
                    turns.append(Turn(tool_calls=tools, skill_events=skills))
                    tools, skills = [], []
        if tools or skills or not turns:
            turns.append(Turn(tool_calls=tools, skill_events=skills))
        return [Session(
            id=session_id,
            trial_id="",
            agent=self.id,
            native_session_id=session_id,
            status="partial" if partial else "complete",
            turns=turns,
        )]

    def check(self, box: AgentBox) -> WiringCheck:
        try:
            valid = (
                bool(self.environment.get("ANTHROPIC_BASE_URL"))
                and bool(self.environment.get("ANTHROPIC_AUTH_TOKEN"))
                and (self.subscription or bool(self.environment.get("ANTHROPIC_MODEL")))
                and self.model_id is not None
            )
            return WiringCheck(ok=valid, detail="Claude Code gateway environment validated")
        except (KeyError, TypeError, ValueError) as error:
            return WiringCheck(ok=False, detail=f"invalid Claude Code environment: {error}")


def _mapping(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _skill_name(path: str) -> str | None:
    marker = "/skills/"
    if marker not in path or not path.endswith("/SKILL.md"):
        return None
    return path.split(marker, maxsplit=1)[1].split("/", maxsplit=1)[0]
