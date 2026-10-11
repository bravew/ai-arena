from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from arena.agents.aider import AiderAdapter
from arena.agents.base import AgentAdapter, NativeTranscript, WiringCheck
from arena.agents.claude_code import ClaudeCodeAdapter
from arena.agents.codex_cli import CodexCliAdapter
from arena.agents.opencode import OpenCodeAdapter
from arena.agents.pi import PiAdapter
from arena.core.cas import ArtifactStore
from arena.core.ids import content_id
from arena.core.modelref import ModelRef
from arena.core.models import Call, Contestant, Scaffold, Task
from arena.core.store import Store
from arena.providers.config import parse_yaml
from arena.runners import agent_trial
from arena.runners.agent_trial import AgentTrialError, adapter_for, execute_agent_trial
from arena.runners.completion import CompletionResult
from arena.runners.metered_gateway import RunGateway
from arena.runners.scheduler import TrialJob
from arena.sandbox.docker import DockerSandbox, SandboxContainer, SandboxSpec


@pytest.mark.parametrize(
    ("scaffold_id", "adapter_type"),
    [
        ("aider", AiderAdapter),
        ("claude-code", ClaudeCodeAdapter),
        ("codex-cli", CodexCliAdapter),
        ("opencode", OpenCodeAdapter),
        ("pi", PiAdapter),
    ],
)
def test_adapter_for_selects_configured_scaffold(scaffold_id: str, adapter_type: type) -> None:
    contestant = Contestant(
        model=ModelRef.parse("anthropic/claude-opus-5-5"),
        scaffold=Scaffold(id=scaffold_id, version="pinned"),
    )

    assert isinstance(adapter_for(contestant), adapter_type)


def test_adapter_for_rejects_missing_or_unsupported_scaffold() -> None:
    no_scaffold = Contestant(model=ModelRef.parse("anthropic/claude-opus-5-5"))
    unsupported = Contestant(
        model=ModelRef.parse("anthropic/claude-opus-5-5"),
        scaffold=Scaffold(id="unknown", version="pinned"),
    )

    with pytest.raises(ValueError, match="requires a configured scaffold"):
        adapter_for(no_scaffold)
    with pytest.raises(ValueError, match="unsupported agent scaffold"):
        adapter_for(unsupported)


class _FakeAdapter:
    id = "fake"
    protocol = "anthropic"
    image = "arena-agent:test"

    def version(self, box: object) -> str:
        return "1.0"

    def wire(self, box: object, gw: object, model: object) -> None:
        return None

    def install_kit(self, box: object, kit: object) -> object:
        raise AssertionError("kits are not installed")

    def command(self, task: object, settings: object) -> list[str]:
        return ["agent-run"]

    def collect(self, box: object) -> NativeTranscript | None:
        return NativeTranscript(content="transcript")

    def sessions(self, native: object, calls: object) -> list[object]:
        return []

    def check(self, box: object) -> WiringCheck:
        return WiringCheck(ok=True)


class _FakeSandbox:
    @asynccontextmanager
    async def start(
        self, spec: SandboxSpec, limits: object = None
    ) -> AsyncGenerator[SandboxContainer, None]:
        assert spec.gateway_token.startswith("arena-")
        assert spec.gateway_container == "gateway-container"
        yield SandboxContainer(id="container-1", network="net-1")


def _trial_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    task_path = Path(__file__).parents[2] / "suites/smoke/tasks/add-two/task.yaml"
    task = Task.model_validate(parse_yaml(task_path))
    contestant = Contestant(
        model=ModelRef.parse("anthropic/claude-opus-5-5"),
        scaffold=Scaffold(id="aider", version="pinned"),
    )
    job = TrialJob(contestant, task, 1)
    run_id = "run-1"
    trial_id = content_id({"run_id": run_id, "job_id": job.id})
    store = Store(tmp_path / "arena.db")
    store.execute(
        "INSERT INTO runs(id, config_json, status) VALUES (?, '{}', 'running')", (run_id,)
    )
    store.execute(
        "INSERT INTO trials(id, run_id, contestant_id, task_id, status, attempt) "
        "VALUES (?, ?, ?, ?, 'running', 1)",
        (trial_id, run_id, contestant.id, task.id),
    )
    real_runner = agent_trial.AgentRunner
    monkeypatch.setattr(
        agent_trial, "AgentRunner", lambda **kwargs: real_runner(first_step_timeout=0.3)
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("do the task", encoding="utf-8")
    return {
        "job": job,
        "run_id": run_id,
        "trial_id": trial_id,
        "store": store,
        "db": tmp_path / "arena.db",
        "prompt": prompt,
        "events": tmp_path / "events",
    }


def _record_call(
    fixture: dict[str, Any],
    *,
    call_id: str = "call-1",
    status: int = 200,
    cost: float | None = 0.01,
    model: str = "anthropic/claude-opus-5-5",
    trial_id: str | None = None,
    event: bool = True,
) -> None:
    owner = trial_id or fixture["trial_id"]
    call = Call(
        id=call_id,
        seq=1,
        run_id=fixture["run_id"],
        trial_id=owner,
        protocol_in="anthropic",
        protocol_out="anthropic",
        provider="anthropic",
        account_id="account-hash",
        model_asked=model,
        status=status,
        cost_usd=cost,
    )
    # The gateway is a separate process with its own connection, so the fake agent opens one too.
    with Store(fixture["db"]) as gateway_store:
        gateway_store.execute(
            "INSERT INTO calls(id, seq, run_id, trial_id, provider, account_id, model_asked, "
            "model_served, status, error_class, tokens_json, cost_usd, details_json) "
            "VALUES (?, 1, ?, ?, 'anthropic', 'account-hash', ?, NULL, ?, NULL, '{}', ?, ?)",
            (call_id, fixture["run_id"], owner, model, status, cost, call.model_dump_json()),
        )
    if event:
        path = fixture["events"] / fixture["run_id"] / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "seq": 1,
                        "run_id": fixture["run_id"],
                        "kind": "call_finished",
                        "ref": call_id,
                        "data": {"trial_id": owner},
                    }
                )
                + "\n"
            )


def _run_trial(
    tmp_path: Path, fixture: dict[str, Any], agent: Callable[[], None] | None
) -> CompletionResult:
    def docker_exec(args: Sequence[str], stdin: bytes | None) -> bytes:
        if "agent-run" in args and agent is not None:
            agent()
        return b""

    store: Store = fixture["store"]
    gateway = RunGateway(fixture["run_id"], store, fixture["events"])
    return asyncio.run(
        execute_agent_trial(
            fixture["job"],
            fixture["run_id"],
            task_file=fixture["prompt"],
            workspace_root=tmp_path / "workspaces",
            store=store,
            artifacts=ArtifactStore(tmp_path / "artifacts"),
            gateway=gateway,
            gateway_url="http://gateway:8765",
            gateway_container="gateway-container",
            budget_usd=1.0,
            sandbox=cast(DockerSandbox, _FakeSandbox()),
            docker_exec=docker_exec,
            adapter=cast(AgentAdapter, _FakeAdapter()),
        )
    )


def test_agent_trial_succeeds_only_with_matching_priced_persisted_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _trial_fixture(tmp_path, monkeypatch)
    workspace = tmp_path / "workspaces" / fixture["trial_id"]

    def agent() -> None:
        _record_call(fixture)
        (workspace / "index.html").write_text("<p>done</p>", encoding="utf-8")

    result = _run_trial(tmp_path, fixture, agent)

    store: Store = fixture["store"]
    assert result.cost_usd == pytest.approx(0.01)
    paths = {
        row[0]
        for row in store.execute(
            "SELECT path FROM trial_artifacts WHERE trial_id=?", (fixture["trial_id"],)
        )
    }
    assert paths == {"index.html", "transcript.txt"}


@pytest.mark.parametrize(
    ("label", "kwargs", "message"),
    [
        ("status", {"status": 500}, "unsuccessful call"),
        ("unpriced", {"cost": None}, "unpriced"),
        ("model", {"model": "anthropic/other"}, "model does not match"),
        ("event", {"event": False}, "completion events do not match"),
        ("owner", {"trial_id": "someone-else"}, "did not complete a metered gateway call"),
        ("over-budget", {"cost": 2.0}, "exceeded the run budget"),
    ],
)
def test_agent_trial_rejects_unverified_gateway_accounting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    kwargs: dict[str, Any],
    message: str,
) -> None:
    fixture = _trial_fixture(tmp_path, monkeypatch)
    if "trial_id" in kwargs:
        fixture["store"].execute(
            "INSERT INTO trials(id, run_id, contestant_id, task_id, status, attempt) "
            "VALUES ('someone-else', 'run-1', 'c', 't', 'running', 1)"
        )

    with pytest.raises(AgentTrialError, match=message):
        _run_trial(tmp_path, fixture, lambda: _record_call(fixture, **kwargs))
    count = (
        fixture["store"]
        .execute("SELECT COUNT(*) FROM trial_artifacts WHERE trial_id=?", (fixture["trial_id"],))
        .fetchone()[0]
    )
    assert count == 0, label


def test_agent_trial_without_a_gateway_call_is_not_a_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _trial_fixture(tmp_path, monkeypatch)

    with pytest.raises(AgentTrialError):
        _run_trial(tmp_path, fixture, None)


def test_agent_trial_command_failure_is_not_a_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _trial_fixture(tmp_path, monkeypatch)

    def agent() -> None:
        _record_call(fixture)
        raise RuntimeError("agent crashed")

    with pytest.raises(AgentTrialError, match="agent command failed"):
        _run_trial(tmp_path, fixture, agent)
