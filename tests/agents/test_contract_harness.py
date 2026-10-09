from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from arena.agents.base import (
    AgentBox,
    AgentProtocol,
    GatewayEndpoint,
    KitInstall,
    NativeTranscript,
    WiringCheck,
)
from arena.agents.testing import ContractCall, ContractHarness, MockProvider
from arena.core.modelref import ModelRef
from arena.core.models import Call, Session, Task
from arena.runners.agent_cli import AgentRunner, ReachedGateway, ReachedState


class FakeBox:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.executed: list[tuple[str, ...]] = []

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content

    def read_text(self, path: str) -> str:
        return self.files[path]

    def execute(self, command: Sequence[str]) -> str:
        self.executed.append(tuple(command))
        return "mock-agent 1.2.3"


class MockAdapter:
    id = "mock-cli"
    protocol: AgentProtocol = "anthropic"
    image = "mock-cli:1.2.3"

    def __init__(self, *, valid: bool = True) -> None:
        self.valid = valid

    def version(self, box: AgentBox) -> str:
        return box.execute(("mock-agent", "--version"))

    def wire(self, box: AgentBox, gw: GatewayEndpoint, model: ModelRef) -> None:
        box.write_text("/home/agent/config", f"{gw.url}|{gw.token}|{model}")

    def install_kit(self, box: AgentBox, kit: Any) -> KitInstall:
        return KitInstall(written=())

    def command(self, task: Task, settings: Mapping[str, Any]) -> list[str]:
        return ["mock-agent", "run", task.prompt_file]

    def collect(self, box: AgentBox) -> NativeTranscript | None:
        return None

    def sessions(self, native: NativeTranscript, calls: Sequence[Call]) -> list[Session]:
        return []

    def check(self, box: AgentBox) -> WiringCheck:
        return WiringCheck(ok=self.valid, detail="config checked")


class FakeGateway(ReachedGateway):
    def __init__(self, calls: Sequence[ContractCall] = ()) -> None:
        self._calls = list(calls)

    async def wait_for_call(
        self, trial_token: str, protocol: AgentProtocol, timeout: float
    ) -> bool:
        del timeout
        return any(call.token == trial_token and call.protocol == protocol for call in self._calls)

    async def calls(self, trial_token: str) -> list[Call]:
        del trial_token
        return []


@pytest.fixture
def task(tmp_path: Path) -> Task:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Do the task", encoding="utf-8")
    return Task(id="task-1", version=1, kind="agentic-code", prompt_file=str(prompt))


def test_contract_harness_pinned_cli_sends_trial_token_and_protocol() -> None:
    async def scenario() -> None:
        provider = MockProvider()
        harness = ContractHarness(
            provider=provider, image="mock-cli:1.2.3", command=("mock-agent", "run")
        )
        result = await harness.run()
        assert result.version == "1.2.3"
        assert result.first_call.token == "arena-contract"
        assert result.first_call.protocol == "anthropic"

    asyncio.run(scenario())


def test_missing_reached_call_with_bad_wiring_is_errored(task: Task) -> None:
    async def scenario() -> None:
        runner = AgentRunner(first_step_timeout=0.01)
        result = await runner.run(
            adapter=MockAdapter(valid=False),
            task=task,
            settings={},
            box=FakeBox(),
            gateway=FakeGateway(),
            endpoint=GatewayEndpoint(url="http://gateway:7400", token="trial-1"),
            model=ModelRef.parse("mock/model"),
            trial_token="trial-1",
        )
        assert result.state is ReachedState.ERRORED
        assert result.error_class == "wiring"
        assert result.flags.unmetered is False

    asyncio.run(scenario())


def test_missing_reached_call_with_valid_wiring_is_unmetered(task: Task) -> None:
    async def scenario() -> None:
        result = await AgentRunner(first_step_timeout=0.01).run(
            adapter=MockAdapter(),
            task=task,
            settings={},
            box=FakeBox(),
            gateway=FakeGateway(),
            endpoint=GatewayEndpoint(url="http://gateway:7400", token="trial-1"),
            model=ModelRef.parse("mock/model"),
            trial_token="trial-1",
        )
        assert result.state is ReachedState.UNMETERED
        assert result.flags.unmetered is True

    asyncio.run(scenario())


def test_reached_call_is_metered(task: Task) -> None:
    async def scenario() -> None:
        gateway = FakeGateway([ContractCall(token="trial-1", protocol="anthropic")])
        result = await AgentRunner(first_step_timeout=0.01).run(
            adapter=MockAdapter(),
            task=task,
            settings={},
            box=FakeBox(),
            gateway=gateway,
            endpoint=GatewayEndpoint(url="http://gateway:7400", token="trial-1"),
            model=ModelRef.parse("mock/model"),
            trial_token="trial-1",
        )
        assert result.state is ReachedState.REACHED
        assert result.flags.unmetered is False

    asyncio.run(scenario())


def test_adapter_exception_is_classified_as_wiring_error(task: Task) -> None:
    class BrokenAdapter(MockAdapter):
        def wire(self, box: AgentBox, gw: GatewayEndpoint, model: ModelRef) -> None:
            raise RuntimeError("broken adapter")

    async def scenario() -> None:
        result = await AgentRunner(first_step_timeout=0.01).run(
            adapter=BrokenAdapter(),
            task=task,
            settings={},
            box=FakeBox(),
            gateway=FakeGateway(),
            endpoint=GatewayEndpoint(url="http://gateway:7400", token="trial-1"),
            model=ModelRef.parse("mock/model"),
            trial_token="trial-1",
        )
        assert result.state is ReachedState.ERRORED
        assert result.error_class == "wiring"
        assert result.error_class != "failed"

    asyncio.run(scenario())
