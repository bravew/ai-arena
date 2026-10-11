"""Aider adapter for trial containers (DEV_PLAN §6.2)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from shlex import quote
from typing import Any

from arena.agents.base import (
    AgentBox,
    GatewayEndpoint,
    KitInstall,
    NativeTranscript,
    WiringCheck,
)
from arena.core.modelref import ModelRef
from arena.core.models import Call, Session, Task, Turn
from arena.kits.install import KitTarget, install_kit, kit_unapplied

VERSION = "0.86.2"
IMAGE = f"arena-aider:{VERSION}"
HOME = "/home/agent"
WORKSPACE = "/workspace"
ENV_FILE = f"{HOME}/.arena-aider-env"
CONVENTIONS = f"{WORKSPACE}/.aider.conventions.md"
TRANSCRIPT = f"{WORKSPACE}/.aider.chat.history.md"


class AiderAdapter:
    """Wire Aider's OpenAI-compatible chat client to the trial gateway."""

    id = "aider"
    protocol = "chat"
    image = IMAGE

    def __init__(self, kit_root: Path | None = None) -> None:
        self.kit_root = kit_root
        self.gateway: GatewayEndpoint | None = None
        self.model_id: str | None = None
        self.environment: dict[str, str] = {}
        self.read_instructions = False
        self.kit_unapplied = False

    def version(self, box: AgentBox) -> str:
        value = box.execute(("aider", "--version")).strip()
        if VERSION not in value:
            raise ValueError(f"expected Aider {VERSION}, got {value!r}")
        return value

    def wire(self, box: AgentBox, gw: GatewayEndpoint, model: ModelRef) -> None:
        self.gateway = gw
        self.model_id = model.model
        self.environment = {
            "OPENAI_API_BASE": f"{gw.url.rstrip('/')}/v1",
            "OPENAI_API_KEY": gw.token,
        }
        box.write_text(ENV_FILE, json.dumps(self.environment, sort_keys=True) + "\n")

    def install_kit(self, box: AgentBox, kit: Any) -> KitInstall:
        if self.kit_root is None:
            raise ValueError("Aider kit installation requires an explicit resolved kit root")
        if self.gateway is None:
            raise ValueError("Aider must be wired before installing a kit")
        target = KitTarget(agent_id=self.id, instructions_path=CONVENTIONS)
        result = install_kit(box, kit, self.kit_root, target, self.gateway)
        self.kit_unapplied = kit_unapplied(kit, result)
        self.read_instructions = CONVENTIONS in result.written
        return result

    def command(self, task: Task, settings: Mapping[str, Any]) -> list[str]:
        model_id = self.model_id or str(settings.get("model", ""))
        if model_id.startswith("openai/"):
            model_id = model_id.removeprefix("openai/")
        arguments = [
            "aider",
            "--model",
            f"openai/{model_id}",
            "--message-file",
            task.prompt_file,
            "--chat-history-file",
            TRANSCRIPT,
            "--yes-always",
            "--no-auto-commits",
            "--no-git",
        ]
        if self.read_instructions:
            arguments.extend(("--read", CONVENTIONS))
        command = " ".join(quote(argument) for argument in arguments)
        return [
            "sh",
            "-lc",
            f"export HOME={quote(HOME)}; "
            "eval \"$(python -c 'import json,shlex,sys; "
            'print("\\n".join("export %s=%s" % (k, shlex.quote(v)) '
            f"for k,v in json.load(open(sys.argv[1])).items())' {quote(ENV_FILE)})\"; "
            f"{command}",
        ]

    def collect(self, box: AgentBox) -> NativeTranscript:
        return NativeTranscript(content=box.read_text(TRANSCRIPT))

    def sessions(self, native: NativeTranscript, calls: Sequence[Call]) -> list[Session]:
        del calls  # Call association is performed by the shared session assembler.
        content = native.content
        partial = not content.strip() or _looks_truncated(content)
        prompts = [line for line in content.splitlines() if line.startswith("#### ")]
        turns = [Turn() for _ in prompts] or [Turn()]
        return [
            Session(
                id="aider-session",
                trial_id="",
                agent=self.id,
                native_session_id="aider-session",
                status="partial" if partial else "complete",
                turns=turns,
            )
        ]

    def check(self, box: AgentBox) -> WiringCheck:
        try:
            environment = json.loads(box.read_text(ENV_FILE))
            base_url = environment["OPENAI_API_BASE"]
            token = environment["OPENAI_API_KEY"]
            valid = (
                environment == self.environment
                and isinstance(base_url, str)
                and base_url.endswith("/v1")
                and bool(token)
                and bool(self.model_id)
            )
            return WiringCheck(ok=valid, detail="Aider OpenAI gateway environment validated")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return WiringCheck(ok=False, detail=f"invalid Aider environment: {error}")


def _looks_truncated(content: str) -> bool:
    """Recognize an incomplete Aider search/replace edit in the saved history."""
    return content.count("<<<<<< ORIGINAL") != content.count(">>>>>>> UPDATED")
