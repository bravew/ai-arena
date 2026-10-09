"""Drive an agent adapter and classify whether its first step reached the gateway."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from arena.agents.base import AgentAdapter, AgentBox, AgentProtocol, GatewayEndpoint
from arena.core.modelref import ModelRef
from arena.core.models import Call, Session, Task, TrialFlags


class ReachedState(StrEnum):
    REACHED = "reached"
    UNMETERED = "unmetered"
    ERRORED = "errored"


class ReachedGateway(Protocol):
    async def wait_for_call(
        self, trial_token: str, protocol: AgentProtocol, timeout: float
    ) -> bool: ...

    async def calls(self, trial_token: str) -> list[Call]: ...


@dataclass(frozen=True)
class AgentRunResult:
    state: ReachedState
    error_class: str | None
    flags: TrialFlags
    version: str | None
    sessions: tuple[Session, ...] = ()
    transcript: object | None = None
    detail: str | None = None


class AgentRunner:
    def __init__(self, *, first_step_timeout: float) -> None:
        if first_step_timeout <= 0:
            raise ValueError("first_step_timeout must be positive")
        self.first_step_timeout = first_step_timeout

    async def run(
        self,
        *,
        adapter: AgentAdapter,
        task: Task,
        settings: Mapping[str, Any],
        box: AgentBox,
        gateway: ReachedGateway,
        endpoint: GatewayEndpoint,
        model: ModelRef,
        trial_token: str,
        kit: Any = None,
    ) -> AgentRunResult:
        version: str | None = None
        try:
            version = adapter.version(box)
            adapter.wire(box, endpoint, model)
            if kit is not None:
                adapter.install_kit(box, kit)
            # Start the CLI before waiting for the gateway's first-step observation.
            command = adapter.command(task, settings)
            execution = asyncio.create_task(asyncio.to_thread(box.execute, command))
            try:
                reached = await gateway.wait_for_call(
                    trial_token, adapter.protocol, self.first_step_timeout
                )
                if not reached:
                    wiring = adapter.check(box)
                    if not wiring.ok:
                        execution.cancel()
                        return AgentRunResult(
                            state=ReachedState.ERRORED,
                            error_class="wiring",
                            flags=TrialFlags(),
                            version=version,
                            detail=wiring.detail,
                        )
                calls = await gateway.calls(trial_token)
                native = adapter.collect(box)
                sessions = adapter.sessions(native, calls) if native is not None else []
                return AgentRunResult(
                    state=ReachedState.REACHED if reached else ReachedState.UNMETERED,
                    error_class=None,
                    flags=TrialFlags(unmetered=not reached),
                    version=version,
                    sessions=tuple(sessions),
                    transcript=native,
                )
            finally:
                if not execution.done():
                    execution.cancel()
        except Exception as error:
            return AgentRunResult(
                state=ReachedState.ERRORED,
                error_class="wiring",
                flags=TrialFlags(),
                version=version,
                detail=str(error),
            )


async def run_agent_cli(**kwargs: Any) -> AgentRunResult:
    """Convenience entry point for one agent trial."""
    first_step_timeout = kwargs.pop("first_step_timeout")
    return await AgentRunner(first_step_timeout=first_step_timeout).run(**kwargs)
