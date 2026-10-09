import pytest
from pydantic import ValidationError

from arena.core.modelref import ModelRef
from arena.core.models import (
    RUN_EVENT_KINDS,
    Artifact,
    Call,
    Contestant,
    RunEvent,
    RunProvenance,
    Scaffold,
    Score,
    Tokens,
    Trial,
)


def test_modelref_with_effort_round_trips() -> None:
    ref = ModelRef.parse("openrouter/qwen/qwen3-coder:high")
    assert (ref.provider, ref.model, ref.effort) == ("openrouter", "qwen/qwen3-coder", "high")
    assert str(ref) == "openrouter/qwen/qwen3-coder:high"
    assert ModelRef.parse(str(ref)) == ref


def test_modelref_without_effort() -> None:
    ref = ModelRef.parse("anthropic/claude-opus-5-5")
    assert ref.effort is None
    assert str(ref) == "anthropic/claude-opus-5-5"


def test_a_colon_that_is_not_an_effort_stays_in_the_model_name() -> None:
    ref = ModelRef.parse("openrouter/qwen/qwen3-coder:free")
    assert ref.model == "qwen/qwen3-coder:free"
    assert ref.effort is None


@pytest.mark.parametrize("bad", ["claude", "/model", "anthropic/", ""])
def test_modelref_needs_provider_and_model(bad: str) -> None:
    with pytest.raises(ValueError):
        ModelRef.parse(bad)


def make(**changes: object) -> Contestant:
    base: dict[str, object] = {
        "model": ModelRef.parse("anthropic/claude-opus-5-5:high"),
        "params": {"temperature": 0.2, "max_tokens": 16000},
        "scaffold": Scaffold(id="claude-code", version="2.4.1"),
        "kit_hash": "abc123",
    }
    return Contestant.model_validate(base | changes)


def test_same_config_same_id() -> None:
    assert make().id == make().id
    assert len(make().id) == 12


def test_reordered_params_keep_the_id() -> None:
    a = make(params={"temperature": 0.2, "max_tokens": 16000})
    b = make(params={"max_tokens": 16000, "temperature": 0.2})
    assert a.id == b.id


def test_label_is_not_identity() -> None:
    assert make(label="nice name").id == make().id


@pytest.mark.parametrize(
    "changes",
    [
        {"params": {"temperature": 0.3, "max_tokens": 16000}},
        {"scaffold": Scaffold(id="claude-code", version="2.4.2")},
        {"scaffold_prompt": "pinned"},
        {"kit_hash": "def456"},
        {"orchestration": "best-of-n"},
        {"prompt_version": "coder-v2"},
        {"hooks": ["redact"]},
        {"model": ModelRef.parse("anthropic/claude-opus-5-5:low")},
    ],
)
def test_changing_any_behavior_changes_the_id(changes: dict[str, object]) -> None:
    assert make(**changes).id != make().id


def test_call_records_account_and_nullable_cost() -> None:
    call = Call(
        id="c1",
        seq=1,
        run_id="r1",
        protocol_in="anthropic",
        protocol_out="anthropic",
        provider="anthropic-max",
        account_id="acct-9f",
        model_asked="claude-opus-5-5",
        tokens=Tokens.model_validate({"in": 10, "out": 5}),
    )
    assert call.cost_usd is None
    assert call.tries == [] and call.prompt_parts == []
    assert call.tokens.in_ == 10


def test_trial_flags_default_off() -> None:
    flags = Trial(id="t", run_id="r", contestant_id="c", task_id="k").flags
    assert not any([flags.unmetered, flags.swapped, flags.subscription_served, flags.kit_unapplied])


def test_run_event_kinds_match_the_plan() -> None:
    assert RUN_EVENT_KINDS == (
        "run_started", "trial_queued", "trial_started", "kit_installed", "call_queued",
        "call_try", "call_finished", "session_turn", "skill_event", "lane_changed",
        "trial_finished", "score_added", "budget", "run_finished",
    )  # fmt: skip
    with pytest.raises(ValidationError):
        RunEvent.model_validate(
            {"seq": 1, "ts": "2026-10-08T00:00:00Z", "run_id": "r", "kind": "x"}
        )


def test_artifact_can_link_to_trial_and_provenance_defaults_native_verified() -> None:
    artifact = Artifact(
        trial_id="trial-1",
        sha256="a" * 64,
        path="answer.txt",
        mime="text/plain",
        render_hint="code",
    )
    assert artifact.trial_id == "trial-1"
    assert RunProvenance().model_dump() == {
        "origin": "native",
        "verification": "verified",
        "importer": None,
        "importer_version": None,
        "source_ref": None,
    }
    imported = RunProvenance(origin="imported", verification="unverified", importer="inspect")
    assert imported.origin == "imported" and imported.verification == "unverified"
    with pytest.raises(ValidationError, match="verification must match provenance origin"):
        RunProvenance(origin="native", verification="unverified")
    with pytest.raises(ValidationError, match="verification must match provenance origin"):
        RunProvenance(origin="imported", verification="verified")


def test_score_is_normalized_to_unit_range() -> None:
    with pytest.raises(ValidationError):
        Score(trial_id="t", scorer_id="s", scorer_version="1", value=3, normalized=1.2)


def test_contestant_identity_cannot_change_through_nested_mutation() -> None:
    contestant = make(
        params={"stop": ["a"], "extra": {"k": 1}},
        scaffold=Scaffold(id="claude-code", version="2.4.1", settings={"flags": ["x"]}),
        hooks=["pre"],
    )
    before = contestant.id
    with pytest.raises(TypeError):
        contestant.params["temperature"] = 1.0
    with pytest.raises(TypeError):
        contestant.params["extra"]["k"] = 2
    assert contestant.scaffold is not None
    with pytest.raises(TypeError):
        contestant.scaffold.settings.update({"flags": []})
    assert isinstance(contestant.hooks, tuple)
    assert contestant.id == before


def test_contestant_does_not_alias_the_callers_params() -> None:
    params: dict[str, object] = {"temperature": 0.2}
    contestant = make(params=params)
    before = contestant.id
    params["temperature"] = 0.9
    assert contestant.id == before


def test_frozen_params_still_dump_as_plain_json() -> None:
    contestant = make(params={"stop": ["a", "b"]}, hooks=["pre"])
    config = contestant.resolved_config()
    assert config["params"] == {"stop": ["a", "b"]}
    assert config["hooks"] == ["pre"]


def test_tokens_serialize_with_the_schema_name_by_default() -> None:
    tokens = Tokens.model_validate({"in": 5, "out": 2})
    assert tokens.in_ == 5
    assert tokens.model_dump()["in"] == 5
    assert "in_" not in tokens.model_dump(mode="json")


@pytest.mark.parametrize("bad", ["  /model", "anthropic/   ", " /x", "anthropic/ model"])
def test_modelref_rejects_blank_or_padded_parts(bad: str) -> None:
    with pytest.raises(ValueError):
        ModelRef.parse(bad)
