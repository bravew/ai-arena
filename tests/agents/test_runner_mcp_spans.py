from __future__ import annotations

import asyncio
from collections.abc import Sequence

from arena.agents.base import AgentBox, GatewayEndpoint, NativeTranscript
from arena.agents.testing import ContractCall
from arena.core.modelref import ModelRef
from arena.core.models import Call, Session, Task, Turn
from arena.gateway.mcp_proxy import McpSpan, McpSpanLog
from arena.runners.agent_cli import AgentRunner
from tests.agents.test_contract_harness import FakeBox, FakeGateway, MockAdapter


class SessionAdapter(MockAdapter):
    """Reports one session per trial id, each with an empty turn."""

    def __init__(self, trial_ids: Sequence[str]) -> None:
        super().__init__()
        self.trial_ids = trial_ids

    def collect(self, box: AgentBox) -> NativeTranscript | None:
        return NativeTranscript("{}")

    def sessions(self, native: NativeTranscript, calls: Sequence[Call]) -> list[Session]:
        return [
            Session(id=f"s-{t}", trial_id=t, agent="mock", turns=[Turn(), Turn()])
            for t in self.trial_ids
        ]


def _run(adapter: SessionAdapter, log: McpSpanLog | None) -> list[Session]:
    result = asyncio.run(
        AgentRunner(first_step_timeout=0.01).run(
            adapter=adapter,
            task=Task(id="task", version=1, kind="agentic-code", prompt_file="prompt.txt"),
            settings={},
            box=FakeBox(),
            gateway=FakeGateway([ContractCall(token="trial", protocol="anthropic")]),
            endpoint=GatewayEndpoint("http://gateway", "trial"),
            model=ModelRef.parse("mock/model"),
            trial_token="trial",
            mcp_spans=log,
        )
    )
    return list(result.sessions)


def test_each_session_gets_only_its_own_trials_mcp_spans() -> None:
    log = McpSpanLog()
    log(McpSpan("t1", "docs", "search", 5, "ok"))
    log(McpSpan("t2", "docs", "leak", 1, "ok"))

    one, two = _run(SessionAdapter(["t1", "t2"]), log)

    assert [m.tool for m in one.turns[-1].mcp_calls] == ["search"]
    assert [m.tool for m in two.turns[-1].mcp_calls] == ["leak"]
    assert one.turns[0].mcp_calls == [] and two.turns[0].mcp_calls == []


def test_without_a_span_log_sessions_are_untouched() -> None:
    (session,) = _run(SessionAdapter(["t1"]), None)

    assert session.turns[-1].mcp_calls == []
