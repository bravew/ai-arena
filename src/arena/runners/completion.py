"""Gateway-compatible completion dispatch interface and deterministic mock adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from arena.core.models import Contestant, Task


@dataclass(frozen=True)
class CompletionResult:
    text: str
    cost_usd: float | None = 0.0
    cache_hit: bool = False


Dispatcher = Callable[[Contestant, Task, str], Awaitable[CompletionResult]]


async def mock_completion(contestant: Contestant, task: Task, prompt: str) -> CompletionResult:
    """Deterministic local adapter used until the gateway dispatcher is wired in."""
    return CompletionResult(text=f"[{contestant.model}] {task.id}: {prompt}")


async def run_completion(
    contestant: Contestant, task: Task, prompt: str, dispatcher: Dispatcher | None = None
) -> CompletionResult:
    """Dispatch one completion; callers inject the gateway or a test adapter."""
    return await (dispatcher or mock_completion)(contestant, task, prompt)
