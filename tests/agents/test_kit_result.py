from __future__ import annotations

import asyncio

import pytest

from arena.agents.base import AgentBox, GatewayEndpoint, KitInstall
from arena.agents.testing import ContractCall
from arena.core.modelref import ModelRef
from arena.core.models import Task
from arena.runners.agent_cli import AgentRunner, ReachedState
from tests.agents.test_contract_harness import FakeBox, FakeGateway, MockAdapter


class KitAdapter(MockAdapter):
    def __init__(self, install: KitInstall, *, valid: bool = True) -> None:
        super().__init__(valid=valid)
        self.install = install

    def install_kit(self, box: AgentBox, kit: object) -> KitInstall:
        return self.install


@pytest.mark.parametrize(
    ("reached", "valid", "state"),
    [
        (True, True, ReachedState.REACHED),
        (False, True, ReachedState.UNMETERED),
        (False, False, ReachedState.ERRORED),
    ],
)
def test_fully_refused_kit_is_retained_and_flagged(
    reached: bool,
    valid: bool,
    state: ReachedState,
) -> None:
    install = KitInstall(written=(), refused=("skills: unsupported", "mcp: unsupported"))
    result = asyncio.run(
        AgentRunner(first_step_timeout=0.01).run(
            adapter=KitAdapter(install, valid=valid),
            task=Task(id="task", version=1, kind="agentic-code", prompt_file="prompt.txt"),
            settings={},
            box=FakeBox(),
            gateway=FakeGateway(
                [ContractCall(token="trial", protocol="anthropic")] if reached else []
            ),
            endpoint=GatewayEndpoint("http://gateway", "trial"),
            model=ModelRef.parse("mock/model"),
            trial_token="trial",
            kit=object(),
        )
    )
    assert result.state is state
    assert result.flags.kit_unapplied is True
    assert result.kit_install == install
    assert result.flags.unmetered is (state is ReachedState.UNMETERED)


@pytest.mark.parametrize(
    "install",
    [
        KitInstall(written=()),
        KitInstall(written=("instructions.md",), refused=("skills: unsupported",)),
    ],
)
def test_empty_or_partially_applied_kit_is_not_flagged(install: KitInstall) -> None:
    result = asyncio.run(
        AgentRunner(first_step_timeout=0.01).run(
            adapter=KitAdapter(install),
            task=Task(id="task", version=1, kind="agentic-code", prompt_file="prompt.txt"),
            settings={},
            box=FakeBox(),
            gateway=FakeGateway(),
            endpoint=GatewayEndpoint("http://gateway", "trial"),
            model=ModelRef.parse("mock/model"),
            trial_token="trial",
            kit=object(),
        )
    )
    assert result.flags.kit_unapplied is False
    assert result.kit_install == install


@pytest.mark.parametrize("fail_during_install", [False, True])
def test_install_evidence_survives_later_errors_but_failed_install_is_unknown(
    fail_during_install: bool,
) -> None:
    install = KitInstall(written=(), refused=("skills: unsupported",))

    class BrokenAdapter(KitAdapter):
        def install_kit(self, box: AgentBox, kit: object) -> KitInstall:
            if fail_during_install:
                raise ValueError("install failed")
            return super().install_kit(box, kit)

        def collect(self, box: AgentBox) -> None:
            raise ValueError("transcript failed")

    result = asyncio.run(
        AgentRunner(first_step_timeout=0.01).run(
            adapter=BrokenAdapter(install),
            task=Task(id="task", version=1, kind="agentic-code", prompt_file="prompt.txt"),
            settings={},
            box=FakeBox(),
            gateway=FakeGateway(),
            endpoint=GatewayEndpoint("http://gateway", "trial"),
            model=ModelRef.parse("mock/model"),
            trial_token="trial",
            kit=object(),
        )
    )
    assert result.state is ReachedState.ERRORED
    assert result.kit_install == (None if fail_during_install else install)
    assert result.flags.kit_unapplied is (not fail_during_install)
    assert result.detail == ("install failed" if fail_during_install else "transcript failed")
