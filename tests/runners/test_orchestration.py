from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from arena.core.modelref import ModelRef
from arena.core.models import Contestant, Task
from arena.runners.completion import CompletionResult
from arena.runners.orchestration import (
    BEST_OF_N_BATCH_SIZE,
    MAX_BEST_OF_N,
    CallContext,
    OrchestrationError,
    Orchestrator,
    Span,
    prepare_contestant,
    roll_up,
)
from arena.runners.scheduler import TrialJob


def contestant(strategy: str, **options: object) -> Contestant:
    return Contestant(
        model=ModelRef.parse("mock/echo"),
        orchestration=strategy,
        params={"orchestration": options},
    )


def task() -> Task:
    return Task(id="demo", version=1, kind="content", prompt_file="prompt.md")


def test_best_of_n_judge_calls_carry_parent_span_and_roll_up_metrics() -> None:
    calls: list[tuple[Contestant, str, CallContext]] = []

    async def dispatch(
        child: Contestant, _task: Task, prompt: str, call: CallContext
    ) -> CompletionResult:
        calls.append((child, prompt, call))
        text = "sample"
        if call.name == "select":
            text = "2"
        return CompletionResult(text, cost_usd=0.25, cache_hit=call.name == "sample")

    result = asyncio.run(
        Orchestrator(dispatch).run(
            contestant("best-of-n", n=2, selector="judge"),
            task(),
            "do the task",
            trial_id="trial-1",
        )
    )

    assert result.text == "sample"
    assert result.metrics.calls == 3
    assert result.metrics.cost_usd == pytest.approx(0.75)
    assert result.metrics.cache_hits == 2
    assert result.cache_hit is False
    root = result.spans[0]
    assert root.kind == "orchestration"
    assert [span.parent_span_id for span in result.spans[1:]] == [root.id] * 3
    assert [call.parent_span_id for _, _, call in calls] == [root.id] * 3
    assert all(child.orchestration == "single" for child, _, _ in calls)
    assert [call.name for _, _, call in calls] == ["sample", "sample", "select"]
    assert calls[-1][1].count("<candidate number=") == 2


def test_best_of_n_rejects_unbounded_n_before_dispatch() -> None:
    calls = 0

    async def dispatch(
        _child: Contestant, _task: Task, _prompt: str, _call: CallContext
    ) -> CompletionResult:
        nonlocal calls
        calls += 1
        return CompletionResult("sample")

    with pytest.raises(OrchestrationError, match=f"must not exceed {MAX_BEST_OF_N}"):
        asyncio.run(
            Orchestrator(dispatch).run(
                contestant("best-of-n", n=MAX_BEST_OF_N + 1),
                task(),
                "prompt",
                trial_id="trial-unbounded",
            )
        )

    assert calls == 0


def test_best_of_n_runs_with_the_deterministic_mock_provider() -> None:
    result = asyncio.run(
        Orchestrator().run(
            contestant("best-of-n", n=2, selector="first"),
            task(),
            "do the task",
            trial_id="mock-best-of-n",
        )
    )

    assert result.text == "[mock/echo] demo: do the task"
    assert result.metrics.calls == 2


def test_best_of_n_samples_in_bounded_batches() -> None:
    active = 0
    peak = 0

    async def dispatch(
        _child: Contestant, _task: Task, _prompt: str, call: CallContext
    ) -> CompletionResult:
        nonlocal active, peak
        if call.name == "sample":
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
        return CompletionResult("sample")

    result = asyncio.run(
        Orchestrator(dispatch).run(
            contestant("best-of-n", n=BEST_OF_N_BATCH_SIZE * 2 + 1, selector="first"),
            task(),
            "prompt",
            trial_id="trial-batched",
        )
    )

    assert result.metrics.calls == BEST_OF_N_BATCH_SIZE * 2 + 1
    assert peak <= BEST_OF_N_BATCH_SIZE


def test_planner_executor_calls_plan_then_execute_with_child_spans() -> None:
    calls: list[tuple[str, str, CallContext]] = []

    async def dispatch(
        _child: Contestant, _task: Task, prompt: str, call: CallContext
    ) -> CompletionResult:
        calls.append((call.name, prompt, call))
        return CompletionResult("a short plan" if call.name == "plan" else "the answer", 0.1)

    result = asyncio.run(
        Orchestrator(dispatch).run(
            contestant("planner-executor"), task(), "make a thing", trial_id="trial-2"
        )
    )

    assert result.text == "the answer"
    assert result.metrics.calls == 2
    assert result.metrics.cost_usd == pytest.approx(0.2)
    assert [name for name, _, _ in calls] == ["plan", "execute"]
    assert "a short plan" in calls[1][1]
    assert all(call.parent_span_id == result.spans[0].id for _, _, call in calls)


def test_best_of_n_tests_selector_uses_first_passing_sample() -> None:
    answers = iter(("no", "yes", "unused"))

    async def dispatch(
        _child: Contestant, _task: Task, _prompt: str, _call: CallContext
    ) -> CompletionResult:
        return CompletionResult(next(answers))

    checked: list[str] = []

    async def check(_task: Task, answer: str) -> bool:
        checked.append(answer)
        return answer == "yes"

    result = asyncio.run(
        Orchestrator(dispatch, check=check).run(
            contestant("best-of-n", n=3, selector="tests"),
            task(),
            "prompt",
            trial_id="trial-3",
        )
    )

    assert result.text == "yes"
    assert result.metrics.calls == 3  # all N candidates are sampled before selection
    assert checked == ["no", "yes"]


def test_custom_strategy_loads_user_module_and_records_digest(tmp_path: Path) -> None:
    module = tmp_path / "strategy.py"
    module.write_text(
        "async def run(context):\n"
        "    result = await context.call('custom-step', context.prompt + '!')\n"
        "    return result.text.upper()\n",
        encoding="utf-8",
    )
    calls: list[CallContext] = []

    async def dispatch(
        _child: Contestant, _task: Task, prompt: str, call: CallContext
    ) -> CompletionResult:
        calls.append(call)
        return CompletionResult(prompt)

    result = asyncio.run(
        Orchestrator(dispatch, base_dir=tmp_path).run(
            contestant("custom:strategy.py"), task(), "hello", trial_id="trial-4"
        )
    )

    assert result.text == "HELLO!"
    assert result.strategy_digest is not None
    assert calls[0].name == "custom-step"
    assert calls[0].parent_span_id == result.spans[0].id


def test_custom_source_changes_contestant_identity_before_cache_planning(tmp_path: Path) -> None:
    module = tmp_path / "strategy.py"
    module.write_text("async def run(context): return 'first'\n", encoding="utf-8")
    original = contestant("custom:strategy.py")
    prepared_first = prepare_contestant(original, tmp_path)
    module.write_text("async def run(context): return 'second'\n", encoding="utf-8")
    prepared_second = prepare_contestant(original, tmp_path)

    assert prepared_first.id != original.id
    assert prepared_second.id != prepared_first.id
    assert (
        prepared_first.params["orchestration_source_sha256"]
        != prepared_second.params["orchestration_source_sha256"]
    )


def test_custom_source_change_after_planning_fails_before_dispatch(tmp_path: Path) -> None:
    module = tmp_path / "strategy.py"
    module.write_text("async def run(context): return 'first'\n", encoding="utf-8")
    prepared = prepare_contestant(contestant("custom:strategy.py"), tmp_path)
    module.write_text("async def run(context): return 'second'\n", encoding="utf-8")
    calls = 0

    async def dispatch(
        _child: Contestant, _task: Task, _prompt: str, _call: CallContext
    ) -> CompletionResult:
        nonlocal calls
        calls += 1
        return CompletionResult("unexpected")

    async def execute() -> object:
        return await Orchestrator(dispatch, base_dir=tmp_path).executor(
            lambda _job: "prompt", run_id="run"
        )(TrialJob(prepared, task(), 1))

    with pytest.raises(OrchestrationError, match="source changed after trial planning"):
        asyncio.run(execute())
    assert calls == 0


def test_unknown_strategy_options_fail_closed() -> None:
    with pytest.raises(OrchestrationError, match="unknown option"):
        asyncio.run(
            Orchestrator().run(
                contestant("planner-executor", secret_option=True),
                task(),
                "prompt",
                trial_id="trial-5",
            )
        )


def test_roll_up_preserves_unknown_prices_and_counts_each_call() -> None:
    metrics = roll_up(
        [
            Span("root", None, "best-of-n", "orchestration", "mock/echo"),
            Span("one", "root", "sample", "call", "mock/echo", 0.2, True),
            Span("two", "root", "sample", "call", "mock/echo"),
        ]
    )

    assert metrics.calls == 2
    assert metrics.cost_usd == pytest.approx(0.2)
    assert metrics.unpriced_calls == 1
    assert metrics.cache_hits == 1
