from datetime import UTC, datetime

from arena.core.models import (
    Call,
    FileTouched,
    PromptPart,
    Session,
    Tokens,
    ToolCall,
    Trial,
    Turn,
)
from arena.stats.aggregate import TrialScore, aggregate_scores
from arena.stats.judge_calibration import PairJudgment, calibration_report
from arena.stats.rundiff import diff_runs
from arena.stats.series import chart_series
from arena.stats.sessions import summarize_sessions


def _call(call_id: str, seq: int, trial_id: str | None, **changes: object) -> Call:
    fields: dict[str, object] = {
        "id": call_id,
        "seq": seq,
        "run_id": "run",
        "trial_id": trial_id,
        "protocol_in": "openai",
        "protocol_out": "openai",
        "provider": "mock",
        "account_id": "acct-hash",
        "model_asked": "model",
    }
    return Call.model_validate(fields | changes)


def test_usage_latency_and_prompt_series_are_hand_computed() -> None:
    trials = [Trial(id="t1", run_id="r", contestant_id="c", task_id="task")]
    calls = [
        _call(
            "c1",
            1,
            "t1",
            tokens=Tokens.model_validate({"in": 10, "out": 2}),
            cost_usd=0.1,
            total_ms=100,
            prompt_parts=[PromptPart(kind="system", tokens=8)],
        ),
        _call(
            "c2",
            2,
            "t1",
            tokens=Tokens.model_validate({"in": 5, "out": 3}),
            cost_usd=0.2,
            total_ms=300,
            prompt_parts=[PromptPart(kind="conversation", tokens=6)],
        ),
        _call("orphan", 3, None, total_ms=999),
    ]

    usage, latency, prompt = chart_series(calls, trials)

    assert [(point.seq, point.tokens_in, point.tokens_out, point.cost_usd) for point in usage] == [
        (1, 10, 2, 0.1),
        (2, 15, 5, 0.30000000000000004),
    ]
    assert latency[0].latencies_ms == (100, 300)
    assert (latency[0].p50_ms, latency[0].p95_ms) == (200.0, 290.0)
    assert [(point.call_id, point.parts) for point in prompt] == [
        ("c1", (("system", 8),)),
        ("c2", (("conversation", 6),)),
    ]


def test_session_summary_marks_missing_call_data_partial_and_preserves_unknowns() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    session = Session(
        id="s1",
        trial_id="t1",
        agent="agent",
        status="complete",
        started_at=start,
        ended_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        turns=[
            Turn(
                call_ids=["c1", "missing"],
                tool_calls=[ToolCall(name="test", args_digest="x", exit_status=1)],
                files=[FileTouched(path="a.py", added=3)],
            )
        ],
    )
    trials = [Trial(id="t1", run_id="r", contestant_id="c", task_id="task", status="succeeded")]
    summaries, groups = summarize_sessions(
        [session],
        [_call("c1", 1, "t1", tokens=Tokens.model_validate({"in": 7, "out": 4}), cost_usd=0.3)],
        trials,
        complete_trial_ids={"t1"},
        contestant_dimensions={"c": {"model": "m1", "kit": "k1"}},
    )

    assert summaries[0].partial is True
    assert summaries[0].tokens_in is None and summaries[0].tokens_out is None
    assert summaries[0].cost_usd is None
    assert (
        summaries[0].wall_time_ms,
        summaries[0].tool_calls,
        summaries[0].tool_errors,
        summaries[0].files_touched,
    ) == (2000, 1, 1, 1)
    group_map = {(group.dimension, group.group): group for group in groups}
    assert group_map[("model", "m1")].partial_sessions == 1
    assert group_map[("kit", "k1")].tokens_in is None


def test_session_without_known_trial_is_always_partial() -> None:
    session = Session(
        id="orphan-session", trial_id="missing-trial", agent="agent", status="complete"
    )
    summaries, _ = summarize_sessions([session], [], [], complete_trial_ids={"missing-trial"})
    assert summaries[0].partial is True
    assert summaries[0].contestant_id is None
    assert summaries[0].tokens_in is None


def test_run_diff_reports_added_removed_cost_and_ci_uncertainty() -> None:
    before = aggregate_scores(
        [
            {"contestant_id": "c", "task_id": "t", "attempt": 1, "score": 0.2},
            {"contestant_id": "c", "task_id": "gone", "attempt": 1, "score": 0.7},
        ],
        bootstrap_samples=50,
        seed=4,
    )
    after = aggregate_scores(
        [
            {"contestant_id": "c", "task_id": "t", "attempt": 1, "score": 0.8},
            {"contestant_id": "c", "task_id": "new", "attempt": 1, "score": 0.5},
        ],
        bootstrap_samples=50,
        seed=4,
    )

    before_records = [TrialScore("c", "t", 1, 0.2), TrialScore("c", "gone", 1, 0.7)]
    after_records = [TrialScore("c", "t", 1, 0.8), TrialScore("c", "new", 1, 0.5)]
    report = diff_runs(
        before,
        after,
        before_cost_usd={"c": 1.0},
        after_cost_usd={"c": 1.5},
        before_records=before_records,
        after_records=after_records,
    )[0]

    assert [(task.task_id, task.status, task.difference) for task in report.tasks] == [
        ("gone", "removed", None),
        ("new", "added", None),
        ("t", "uncertain", 0.6000000000000001),
    ]
    assert report.cost_delta_usd == 0.5


def test_run_diff_uses_paired_mean_and_does_not_call_one_repeat_conclusive() -> None:
    before = aggregate_scores(
        [{"contestant_id": "c", "task_id": "t", "attempt": 1, "score": 0.1}],
        bootstrap_samples=100,
        seed=0,
    )
    after = aggregate_scores(
        [{"contestant_id": "c", "task_id": "t", "attempt": 1, "score": 0.9}],
        bootstrap_samples=100,
        seed=0,
    )
    diff = diff_runs(
        before,
        after,
        before_records=[TrialScore("c", "t", 1, 0.1)],
        after_records=[TrialScore("c", "t", 1, 0.9)],
    )[0].tasks[0]
    assert diff.difference == 0.8
    assert diff.status == "uncertain"


def test_run_diff_delta_and_ci_use_same_paired_repeats_under_unequal_data() -> None:
    before = aggregate_scores(
        [
            {"contestant_id": "c", "task_id": "t", "attempt": 1, "score": 0.0},
            {"contestant_id": "c", "task_id": "t", "attempt": 2, "score": 1.0},
            {"contestant_id": "c", "task_id": "t", "attempt": 3, "score": 0.2, "swapped": True},
        ],
        bootstrap_samples=100,
        seed=2,
    )
    after = aggregate_scores(
        [
            {"contestant_id": "c", "task_id": "t", "attempt": 1, "score": 0.4},
            {"contestant_id": "c", "task_id": "t", "attempt": 2, "score": 0.6},
            {"contestant_id": "c", "task_id": "t", "attempt": 4, "score": 0.9},
        ],
        bootstrap_samples=100,
        seed=2,
    )
    diff = diff_runs(
        before,
        after,
        before_records=[
            TrialScore("c", "t", 1, 0.0),
            TrialScore("c", "t", 2, 1.0),
            TrialScore("c", "t", 3, 0.2, swapped=True),
        ],
        after_records=[
            TrialScore("c", "t", 1, 0.4),
            TrialScore("c", "t", 2, 0.6),
            TrialScore("c", "t", 4, 0.9),
        ],
    )[0].tasks[0]
    assert diff.difference == 0.0
    assert diff.status == "unchanged"


def test_judge_calibration_metrics_are_hand_computed_and_missing_is_explicit() -> None:
    rows = [
        PairJudgment("1", "j", "a", "a", "b", 10, 5, "family-j", "family-x", "family-j"),
        PairJudgment("2", "j", "b", "a", "b", 5, 10, "family-x", "family-j", "family-j"),
        PairJudgment("3", "j", "tie", "tie", "tie", 8, 8, "family-x", "family-y", "family-j"),
        PairJudgment("4", "j", None, None),
    ]
    report = calibration_report(
        "j",
        rows,
        [
            _call("j1", 1, None, purpose="judge", model_asked="judge-model", cost_usd=0.8),
            _call(
                "other-config", 2, None, purpose="judge", model_asked="judge-model", cost_usd=9.0
            ),
        ],
        judge_call_ids={"j1"},
    )

    assert report.judgments == 4 and report.human_labeled == 3
    assert report.agreement == 2 / 3
    assert report.cohens_kappa is not None and abs(report.cohens_kappa - 0.5) < 1e-12
    assert report.position_bias_rate == 1 / 3
    assert report.length_correlation == 1.0
    assert report.self_preference_rate == 2 / 3
    assert report.cost_per_judgment_usd == 0.2
    assert (
        report.missing_verdicts,
        report.missing_human_labels,
        report.missing_order_swaps,
        report.missing_lengths,
        report.missing_costs,
    ) == (1, 1, 1, 1, 0)


def test_judge_report_without_data_raises_instead_of_reporting_empty() -> None:
    try:
        calibration_report("j", [], [])
    except ValueError as error:
        assert str(error) == "no judgments for judge j"
    else:
        raise AssertionError("empty judge input must not look like a valid report")
