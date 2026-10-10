"""Codex CLI adapter for the Responses product axis."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from arena.agents.base import (
    AgentBox,
    GatewayEndpoint,
    KitInstall,
    NativeTranscript,
    WiringCheck,
)
from arena.agents.sessions import assemble_session
from arena.core.modelref import ModelRef
from arena.core.models import Call, Kit, Session, Task
from arena.kits.install import KitTarget, install_kit

CODEX_VERSION = "0.162.1"
CODEX_HOME = "/home/agent/.codex"
CODEX_CONFIG = f"{CODEX_HOME}/config.toml"
CODEX_TOKEN_ENV = "ARENA_GATEWAY_TOKEN"


@dataclass(frozen=True)
class ResolvedCodexKit:
    """A loaded kit and its source root for safe in-container installation."""

    kit: Kit
    root: Path
    gateway: GatewayEndpoint


class CodexCliAdapter:
    id = "codex-cli"
    protocol = "responses"
    image = f"arena-codex-cli:{CODEX_VERSION}"

    def version(self, box: AgentBox) -> str:
        output = box.execute(("codex", "--version"))
        match = re.search(r"\b(\d+\.\d+\.\d+)\b", output)
        if not match:
            raise ValueError(f"could not parse Codex CLI version from {output!r}")
        return match.group(1)

    def wire(self, box: AgentBox, gw: GatewayEndpoint, model: ModelRef) -> None:
        provider_id = "arena"
        config = (
            f"model = {_toml_string(model.model)}\n"
            f"model_provider = {_toml_string(provider_id)}\n\n"
            f"[model_providers.{provider_id}]\n"
            f'name = "AI Arena gateway"\n'
            f"base_url = {_toml_string(gw.url.rstrip('/'))}\n"
            'wire_api = "responses"\n'
            f"env_key = {_toml_string(CODEX_TOKEN_ENV)}\n"
            "requires_openai_auth = false\n"
        )
        box.write_text(CODEX_CONFIG, config)

    def install_kit(self, box: AgentBox, kit: Any) -> KitInstall:
        if kit is None:
            return KitInstall(written=())
        if not isinstance(kit, ResolvedCodexKit):
            raise TypeError("Codex kit install requires ResolvedCodexKit(kit, root)")
        target = KitTarget(
            agent_id=self.id,
            instructions_path=f"{CODEX_HOME}/AGENTS.md",
            skills_dir=f"{CODEX_HOME}/skills",
            mcp=_codex_mcp,
            settings=_codex_settings,
        )
        # Fragment files let the shared installer hash-check each kit contribution before
        # these sections are appended to Codex's single config.toml.
        try:
            box.read_text(CODEX_CONFIG)
        except (KeyError, OSError):
            self.wire(box, kit.gateway, ModelRef.parse(f"openai/{kit.kit.id}"))
        endpoint = GatewayEndpoint(
            url=kit.gateway.url,
            token=f"${{{CODEX_TOKEN_ENV}}}",
        )
        result = install_kit(box, kit.kit, kit.root, target, endpoint)
        fragments = (f"{CODEX_HOME}/.arena-mcp.toml", f"{CODEX_HOME}/.arena-settings.toml")
        additions = [box.read_text(path) for path in fragments if path in result.written]
        if additions:
            original = box.read_text(CODEX_CONFIG)
            box.write_text(CODEX_CONFIG, original.rstrip() + "\\n\\n" + "\\n".join(additions))
        return KitInstall(
            written=tuple((*result.written, *((CODEX_CONFIG,) if additions else ()))),
            refused=result.refused,
        )

    def command(self, task: Task, settings: Mapping[str, Any]) -> list[str]:
        command = ["codex", "exec", "--json", "--full-auto"]
        for key, value in sorted(settings.items()):
            if key in {"model", "model_provider"}:
                continue
            command.extend(("-c", f"{key}={_toml_value(value)}"))
        prompt = Path(task.prompt_file).read_text(encoding="utf-8")
        command.append(prompt)
        return command

    def collect(self, box: AgentBox) -> NativeTranscript | None:
        output = box.execute(
            (
                "sh",
                "-c",
                "find /home/agent/.codex/sessions -type f -name 'rollout-*.jsonl' -print0 "
                "| xargs -0 -r cat",
            )
        )
        return NativeTranscript(output) if output.strip() else None

    def sessions(self, native: NativeTranscript, calls: Sequence[Call]) -> list[Session]:
        normalized = _normalize_rollout(native.content)
        trial_id = next((call.trial_id for call in calls if call.trial_id), "codex-cli")
        session, _ = assemble_session(
            normalized,
            trial_id=trial_id,
            agent=self.id,
            calls=tuple(calls),
        )
        return [session]

    def check(self, box: AgentBox) -> WiringCheck:
        try:
            config = tomllib.loads(box.read_text(CODEX_CONFIG))
        except (KeyError, OSError, tomllib.TOMLDecodeError) as error:
            return WiringCheck(ok=False, detail=f"Codex config is missing or invalid: {error}")
        provider_id_value = config.get("model_provider")
        provider_id = provider_id_value if isinstance(provider_id_value, str) else ""
        providers_value = config.get("model_providers", {})
        providers = (
            cast(dict[str, Any], providers_value) if isinstance(providers_value, dict) else {}
        )
        provider_value = providers.get(provider_id, {})
        provider = cast(dict[str, Any], provider_value) if isinstance(provider_value, dict) else {}
        valid = (
            bool(config.get("model"))
            and provider.get("wire_api") == "responses"
            and provider.get("env_key") == CODEX_TOKEN_ENV
            and isinstance(provider.get("base_url"), str)
            and bool(provider.get("base_url"))
        )
        return WiringCheck(ok=valid, detail="Codex Responses provider config verified")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in cast(list[Any], value)):
        return "[" + ", ".join(_toml_string(item) for item in cast(list[str], value)) + "]"
    raise ValueError(f"unsupported Codex setting value: {value!r}")


def _codex_mcp(entries: Sequence[Any]) -> Sequence[tuple[str, str]]:
    chunks: list[str] = []
    for entry in entries:
        chunks.extend(
            (
                f"[mcp_servers.{entry.name}]",
                f"url = {_toml_string(entry.url)}"
                if entry.url
                else f"command = {_toml_string(entry.command)}",
            )
        )
        if entry.args:
            chunks.append("args = " + _toml_value(list(entry.args)))
        headers = dict(entry.headers or {})
        if "Authorization" in headers:
            chunks.append(f"bearer_token_env_var = {_toml_string(CODEX_TOKEN_ENV)}")
        chunks.append("")
    return [(f"{CODEX_HOME}/.arena-mcp.toml", "\n".join(chunks))] if chunks else ()


def _codex_settings(settings: Mapping[str, object]) -> Sequence[tuple[str, str]]:
    lines = [f"{key} = {_toml_value(value)}" for key, value in sorted(settings.items())]
    return [(f"{CODEX_HOME}/.arena-settings.toml", "\n".join(lines) + "\n")] if lines else ()


def _normalize_rollout(content: str) -> str:
    """Translate Codex rollout JSONL into the shared session assembler's event records."""
    normalized: list[dict[str, Any]] = []
    seen_turns: set[str] = set()
    skill_names: set[str] = set()
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return "\n".join(json.dumps(item) for item in normalized) + "\n{"
        if not isinstance(record, dict):
            return "\n".join(json.dumps(item) for item in normalized) + "\n[]"
        record = cast(dict[str, Any], record)
        kind = record.get("type")
        payload_value = record.get("payload", {})
        if not isinstance(payload_value, dict):
            continue
        payload = cast(dict[str, Any], payload_value)
        if kind == "session_meta":
            session_id = payload.get("id") or payload.get("session_id")
            if isinstance(session_id, str):
                normalized.append({"type": "session", "session_id": session_id})
            _collect_skill_names(payload, skill_names)
            continue
        if kind == "turn_context":
            turn_id = str(payload.get("turn_id") or f"turn-{len(seen_turns) + 1}")
            if turn_id not in seen_turns:
                seen_turns.add(turn_id)
                normalized.append({"type": "turn", "turn_id": turn_id})
            _collect_skill_names(payload, skill_names)
            for skill in sorted(skill_names):
                normalized.append({"type": "skill_event", "kind": "listed", "skill": skill})
            continue
        if kind == "event_msg":
            event_type = payload.get("type")
            if event_type in {"task_started", "turn_started"}:
                turn_id = str(payload.get("turn_id") or f"turn-{len(seen_turns) + 1}")
                if turn_id not in seen_turns:
                    seen_turns.add(turn_id)
                    normalized.append({"type": "turn", "turn_id": turn_id})
            _collect_skill_names(payload, skill_names)
            continue
        if kind != "response_item":
            continue
        item_type = payload.get("type")
        if item_type == "function_call":
            name = str(payload.get("name", "unknown"))
            arguments = _json_object(payload.get("arguments"))
            normalized.append({"type": "tool_call", "name": name, "args": arguments})
            _skill_activity(name, arguments, skill_names, normalized)
        elif item_type == "custom_tool_call":
            name = str(payload.get("name", "unknown"))
            arguments = _json_object(payload.get("input"))
            normalized.append({"type": "tool_call", "name": name, "args": arguments})
            _skill_activity(name, arguments, skill_names, normalized)
    return "\n".join(json.dumps(record) for record in normalized)


def _collect_skill_names(value: Any, names: set[str]) -> None:
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        for key, item in mapping.items():
            if key in {"skill", "skill_name"} and isinstance(item, str):
                names.add(item)
            elif key in {"selected_capability_roots", "skills"} and isinstance(item, list):
                names.update(
                    str(entry) for entry in cast(list[Any], item) if isinstance(entry, str)
                )
            else:
                _collect_skill_names(item, names)
    elif isinstance(value, list):
        for item in cast(list[Any], value):
            _collect_skill_names(item, names)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        if isinstance(parsed, dict):
            return cast(dict[str, Any], parsed)
        return {"value": parsed}
    return {}


def _skill_activity(
    tool_name: str,
    arguments: Mapping[str, Any],
    known_skills: set[str],
    normalized: list[dict[str, Any]],
) -> None:
    serialized = f"{tool_name} {json.dumps(arguments, sort_keys=True)}"
    lowered = serialized.lower()
    for skill in sorted(known_skills):
        skill_path = skill.lower().replace("\\", "/")
        if "read" in tool_name.lower() and (
            "skill.md" in lowered or skill_path in lowered or skill.lower() in lowered
        ):
            normalized.append({"type": "skill_event", "kind": "loaded", "skill": skill})
        if skill.lower() in lowered and (tool_name.lower() not in {"read_file", "read"}):
            normalized.append({"type": "skill_event", "kind": "invoked", "skill": skill})
