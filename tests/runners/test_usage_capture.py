"""CLI-reported usage becomes ledger rows, events and a separate subscription tally."""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from arena.agents.local import ClaudeLocalAdapter, CodexLocalAdapter, StreamSummary
from arena.catalog.config import ModelCatalog
from arena.core.store import Store
from arena.gateway.budget import Budget
from arena.obs.events import EventLog
from arena.obs.ledger import CallsLedger
from arena.runners.local_cli import FailureKind, LocalCliResult
from arena.runners.usage_capture import CaptureError, UsageCapture

FIXTURES = Path(__file__).parents[2] / "fixtures" / "cli-contracts"
DOCS = Path(__file__).parents[2] / "docs"
CLAUDE_REF = "anthropic-max/claude-opus-5-5"
CODEX_REF = "openai-plus/gpt-6.1-sol"


def _catalog() -> ModelCatalog:
    return ModelCatalog.model_validate(
        {
            "price_version": "v1",
            "models": [
                {"ref": CLAUDE_REF, "pricing": "subscription"},
                {"ref": CODEX_REF, "pricing": "subscription"},
                {"ref": "anthropic/claude-opus-5-5", "price_per_mtok": {"in": 15, "out": 75}},
            ],
        }
    )


def _summary(adapter: ClaudeLocalAdapter | CodexLocalAdapter, name: str) -> StreamSummary:
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    return adapter.parse(lines)


def _result(
    summary: StreamSummary | None,
    *,
    failure: FailureKind | None = None,
    verified: bool = True,
) -> LocalCliResult:
    return LocalCliResult(
        failure=failure,
        detail="" if failure is None else f"failed: {failure}",
        exit_code=0 if failure is None else 1,
        summary=summary,
        outputs={},
        duration_seconds=1.5,
        model_served_verified=verified,
    )


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.store = Store(tmp_path / "arena.db")
        self.store.execute(
            "INSERT INTO runs(id, config_json, status) VALUES ('r1', '{}', 'running')"
        )
        self.store.execute(
            "INSERT INTO trials(id, run_id, contestant_id, task_id, status) "
            "VALUES ('t1', 'r1', 'contestant', 'task', 'running')"
        )
        catalog = _catalog()
        self.ledger = CallsLedger(self.store, catalog)
        self.events = EventLog(tmp_path / "events")
        self.budget = Budget()
        counter: Iterator[int] = itertools.count(1)
        self.capture = UsageCapture(
            self.ledger, self.events, self.budget, catalog, lambda: next(counter)
        )

    def run(self, result: LocalCliResult, *, ref: str = CLAUDE_REF, runtime: str = "claude"):
        return asyncio.run(
            self.capture.capture(
                run_id="r1",
                trial_id="t1",
                model_ref=ref,
                runtime=runtime,
                account_id="acct-ab12",
                result=result,
            )
        )


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    instance = Harness(tmp_path)
    yield instance
    instance.store.close()


def test_a_clean_claude_run_is_reached_and_recorded_as_cli_reported(harness: Harness) -> None:
    summary = _summary(ClaudeLocalAdapter(), "claude/stream.synthetic.jsonl")
    outcome = harness.run(_result(summary))
    assert outcome.reached and outcome.reason is None and outcome.call is not None
    call = harness.ledger.get(outcome.call.id)
    assert call is not None
    assert (call.upstream, call.protocol_in, call.protocol_out) == (
        "cli_reported",
        "local-cli",
        "cli:claude",
    )
    assert call.cost_usd is None and call.price_version is None
    assert (call.tokens.in_, call.tokens.out, call.tokens.cache_read) == (12, 5, 3)
    assert (call.model_asked, call.model_served, call.status) == (
        CLAUDE_REF,
        "claude-opus-5-5",
        200,
    )
    assert call.account_id == "acct-ab12" and call.total_ms == 1500


def test_the_event_is_labeled_and_matches_what_live_reads(harness: Harness) -> None:
    summary = _summary(CodexLocalAdapter(), "codex/exec.synthetic.jsonl")
    outcome = harness.run(_result(summary), ref=CODEX_REF, runtime="codex")
    assert outcome.reached
    events = asyncio.run(harness.events.read_after("r1", 0))
    finished = [e for e in events if e.kind == "call_finished"]
    assert len(finished) == 1
    data = finished[0].data
    assert (data["source"], data["runtime"], data["cost_usd"]) == ("cli_reported", "codex", None)
    assert data["trial_id"] == "t1" and data["tokens"]["in"] == 60


def test_subscription_tokens_never_touch_the_metered_cap(harness: Harness) -> None:
    summary = _summary(ClaudeLocalAdapter(), "claude/stream.synthetic.jsonl")
    harness.run(_result(summary))
    report = harness.budget.report()
    assert report.metered_spend_usd == 0 and report.remaining_usd == report.cap_usd
    assert report.subscription_tokens == 12 + 5 + 3 + 2  # input, output, cache read, cache write


def test_a_row_and_event_validate_against_the_published_schemas(harness: Harness) -> None:
    summary = _summary(ClaudeLocalAdapter(), "claude/stream.synthetic.jsonl")
    outcome = harness.run(_result(summary))
    assert outcome.call is not None
    bundle_schema = json.loads((DOCS / "bundle-schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator({"$ref": "#/$defs/call", "$defs": bundle_schema["$defs"]})
    validator.validate(outcome.call.model_dump(mode="json"))
    # EventLog.append validated the event against event-schema.json when it wrote it.
    assert len(asyncio.run(harness.events.read_after("r1", 0))) >= 1


@pytest.mark.parametrize(
    ("failure", "status", "error_class"),
    [
        (FailureKind.LIMIT_REACHED, 429, "subscription_limit"),
        (FailureKind.AUTH_REQUIRED, 401, "auth"),
        (FailureKind.TIMEOUT, None, "timeout"),
        (FailureKind.MODEL_MISMATCH, 200, "model_refused"),
        (FailureKind.EXIT_CODE, 500, "upstream"),
    ],
)
def test_failures_are_recorded_and_never_reached(
    harness: Harness, failure: FailureKind, status: int | None, error_class: str
) -> None:
    outcome = harness.run(_result(None, failure=failure))
    assert not outcome.reached and outcome.call is not None
    assert (outcome.call.status, outcome.call.error_class) == (status, error_class)
    assert outcome.call.tokens.in_ == 0 and outcome.call.model_served is None


def test_a_spawn_failure_makes_no_call_record(harness: Harness) -> None:
    outcome = harness.run(_result(None, failure=FailureKind.SPAWN))
    assert not outcome.reached and outcome.call is None
    assert harness.ledger.list_for_run("r1") == []


def test_an_unverified_served_model_is_recorded_but_not_reached(harness: Harness) -> None:
    summary = _summary(CodexLocalAdapter(), "codex/exec.synthetic.jsonl")
    outcome = harness.run(_result(summary, verified=False), ref=CODEX_REF, runtime="codex")
    assert not outcome.reached and "verified" in (outcome.reason or "")
    assert outcome.call is not None and outcome.call.model_served is None
    assert outcome.call.tokens.in_ == 60


def test_missing_usage_is_not_reached(harness: Harness) -> None:
    summary = StreamSummary(
        events=2, model_served="claude-opus-5-5", usage=None, final_text="", transcript=""
    )
    outcome = harness.run(_result(summary))
    assert not outcome.reached and "no usage" in (outcome.reason or "")


def test_calls_get_unique_ids_and_sequence_numbers(harness: Harness) -> None:
    summary = _summary(ClaudeLocalAdapter(), "claude/stream.synthetic.jsonl")
    first, second = harness.run(_result(summary)), harness.run(_result(summary))
    assert first.call is not None and second.call is not None
    assert (first.call.id, first.call.seq) != (second.call.id, second.call.seq)
    assert [c.seq for c in harness.ledger.list_for_run("r1")] == [1, 2]


def test_only_a_catalogued_subscription_model_can_be_captured(harness: Harness) -> None:
    summary = _summary(ClaudeLocalAdapter(), "claude/stream.synthetic.jsonl")
    with pytest.raises(CaptureError, match="not a subscription model"):
        harness.run(_result(summary), ref="anthropic/claude-opus-5-5")
    with pytest.raises(CaptureError):
        harness.run(_result(summary), ref="anthropic-max/not-listed")
    assert harness.ledger.list_for_run("r1") == []
