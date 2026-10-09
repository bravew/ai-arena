"""Contract harness for a pinned agent CLI and an injectable mock provider."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from arena.agents.base import AgentBox, AgentProtocol, GatewayEndpoint


@dataclass(frozen=True)
class ContractCall:
    token: str
    protocol: str


@dataclass(frozen=True)
class ContractResult:
    version: str
    first_call: ContractCall


class Provider(Protocol):
    async def first_request(self, *, token: str, protocol: AgentProtocol) -> ContractCall: ...


class MockProvider:
    """Injectable deterministic provider that records the first request."""

    def __init__(self) -> None:
        self.requests: list[ContractCall] = []

    async def first_request(self, *, token: str, protocol: AgentProtocol) -> ContractCall:
        call = ContractCall(token=token, protocol=protocol)
        self.requests.append(call)
        return call


class MockGateway:
    """Gateway stub that validates the trial token and API protocol."""

    def __init__(self, provider: Provider, expected_token: str) -> None:
        self.provider = provider
        self.expected_token = expected_token
        self.first_call: ContractCall | None = None

    async def request(self, token: str, protocol: AgentProtocol) -> ContractCall:
        if token != self.expected_token:
            raise AssertionError("agent did not send the trial gateway token")
        self.first_call = await self.provider.first_request(token=token, protocol=protocol)
        return self.first_call


class DeterministicMockProvider:
    async def first_request(self, *, token: str, protocol: AgentProtocol) -> ContractCall:
        return ContractCall(token=token, protocol=protocol)


class _ContractBox(AgentBox):
    def __init__(self, protocol: AgentProtocol) -> None:
        self.files: dict[str, str] = {}
        self.protocol = protocol

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content

    def read_text(self, path: str) -> str:
        return self.files[path]

    def execute(self, command: Sequence[str]) -> str:
        args = tuple(command)
        if args == ("mock-agent", "--version"):
            return "mock-agent 1.2.3"
        if args != ("mock-agent", "run"):
            raise ValueError(f"unexpected contract command: {command}")
        return ""


class ContractHarness:
    """Run the pinned CLI image and assert its first request reaches the mock."""

    def __init__(
        self,
        *,
        provider: Provider | None = None,
        image: str,
        command: tuple[str, ...],
        protocol: AgentProtocol = "anthropic",
        trial_token: str = "arena-contract",
        gateway_url: str = "http://mock-gateway:7400",
    ) -> None:
        if not image or not image.rsplit(":", 1)[-1]:
            raise ValueError("contract image must have a pinned tag")
        if not command:
            raise ValueError("contract command is required")
        self.provider: Provider = provider or DeterministicMockProvider()
        self.image = image
        self.command = command
        self.protocol: AgentProtocol = protocol
        self.trial_token = trial_token
        self.gateway_url = gateway_url

    async def run(self) -> ContractResult:
        box = _ContractBox(self.protocol)
        endpoint = GatewayEndpoint(url=self.gateway_url, token=self.trial_token)
        box.write_text("/home/agent/gateway-url", endpoint.url)
        box.write_text("/home/agent/gateway-token", endpoint.token)
        version_output = box.execute(("mock-agent", "--version"))
        version = version_output.rsplit(" ", 1)[-1]
        if not version:
            raise AssertionError("pinned CLI did not report its version")
        if self.command != ("mock-agent", "run"):
            raise ValueError("mock contract image must run mock-agent run")
        box.execute(self.command)
        gateway = MockGateway(self.provider, self.trial_token)
        call = await gateway.request(box.read_text("/home/agent/gateway-token"), self.protocol)
        if call.protocol != self.protocol:
            raise AssertionError(f"expected {self.protocol} request, got {call.protocol}")
        return ContractResult(version=version, first_call=call)


def run_contract(harness: ContractHarness) -> ContractResult:
    """Synchronous convenience wrapper for non-async test functions."""
    return asyncio.run(harness.run())
