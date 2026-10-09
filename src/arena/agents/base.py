"""Contracts shared by agent CLI adapters and their sandbox."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from arena.core.modelref import ModelRef
from arena.core.models import Call, Session, Task

AgentProtocol = Literal["anthropic", "responses", "chat", "gemini"]


@dataclass(frozen=True)
class GatewayEndpoint:
    url: str
    token: str


@dataclass(frozen=True)
class KitInstall:
    written: tuple[str, ...]
    refused: tuple[str, ...] = ()


@dataclass(frozen=True)
class NativeTranscript:
    content: str


@dataclass(frozen=True)
class WiringCheck:
    ok: bool
    detail: str = ""


class AgentBox(Protocol):
    """Operations that an adapter may perform inside a trial container."""

    def write_text(self, path: str, content: str) -> None: ...

    def read_text(self, path: str) -> str: ...

    def execute(self, command: Sequence[str]) -> str: ...


class AgentAdapter(Protocol):
    id: str

    protocol: AgentProtocol

    image: str

    def version(self, box: AgentBox) -> str: ...

    def wire(self, box: AgentBox, gw: GatewayEndpoint, model: ModelRef) -> None: ...

    def install_kit(self, box: AgentBox, kit: Any) -> KitInstall: ...

    def command(self, task: Task, settings: Mapping[str, Any]) -> list[str]: ...

    def collect(self, box: AgentBox) -> NativeTranscript | None: ...

    def sessions(self, native: NativeTranscript, calls: Sequence[Call]) -> list[Session]: ...

    def check(self, box: AgentBox) -> WiringCheck: ...
