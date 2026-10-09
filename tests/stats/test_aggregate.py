from __future__ import annotations

import pytest

from arena.stats.aggregate import aggregate_scores
from arena.stats.bootstrap import cluster_bootstrap_ci


def test_aggregation_excludes_unreliable_trials_and_counts_them() -> None:
    rows = [
        {"contestant_id": "a", "task_id": "task-1", "attempt": 1, "score": 1.0, "passed": True},
        {"contestant_id": "a", "task_id": "task-1", "attempt": 2, "score": 0.0, "passed": False},
        {
            "contestant_id": "a",
            "task_id": "task-2",
            "attempt": 1,
            "score": 0.2,
            "passed": False,
            "swapped": True,
        },
        {
            "contestant_id": "a",
            "task_id": "task-2",
            "attempt": 2,
            "score": 0.2,
            "passed": False,
            "unmetered": True,
        },
        {
            "contestant_id": "a",
            "task_id": "task-2",
            "attempt": 3,
            "score": 0.2,
            "passed": False,
            "kit_unapplied": True,
        },
    ]

    result = aggregate_scores(rows, bootstrap_samples=200, seed=7)

    contestant = result.contestants["a"]
    assert contestant.suite is not None
    assert contestant.suite.estimate == 0.5
    assert contestant.tasks["task-1"].mean == 0.5
    assert contestant.tasks["task-1"].pass_at_k == 1.0
    assert contestant.tasks["task-1"].pass_power_k == 0.25
    assert contestant.excluded.swapped == 1
    assert contestant.excluded.unmetered == 1
    assert contestant.excluded.kit_unapplied == 1
    assert contestant.excluded.total == 3


def test_suite_weights_tasks_equally_and_pass_at_k_uses_unbiased_estimator() -> None:
    result = aggregate_scores(
        [
            {"contestant_id": "a", "task_id": "many", "attempt": i, "score": 0.0, "passed": i == 1}
            for i in range(1, 10)
        ]
        + [{"contestant_id": "a", "task_id": "one", "attempt": 1, "score": 1.0, "passed": True}],
        bootstrap_samples=200,
        seed=5,
    )
    contestant = result.contestants["a"]

    assert contestant.suite is not None
    assert contestant.suite.estimate == 0.5
    assert contestant.tasks["many"].pass_at_k == 1.0
    assert contestant.tasks["many"].pass_power_k == pytest.approx((1 / 9) ** 9)


def test_excluded_reason_counts_count_overlapping_flags_once_in_total() -> None:
    result = aggregate_scores(
        [
            {
                "contestant_id": "a",
                "task_id": "t",
                "attempt": 1,
                "score": 0.4,
                "swapped": True,
                "unmetered": True,
            }
        ]
    )

    assert result.contestants["a"].excluded == type(result.contestants["a"].excluded)(
        swapped=1, unmetered=1, kit_unapplied=0, total=1
    )
    assert result.contestants["a"].suite is None


def test_mapping_input_is_validated_before_trial_construction() -> None:
    with pytest.raises(ValueError, match="unknown trial score fields"):
        aggregate_scores(
            [{"contestant_id": "a", "task_id": "t", "attempt": 1, "score": 0.2, "oops": True}]
        )
    with pytest.raises(ValueError, match="attempt must be an integer"):
        aggregate_scores([{"contestant_id": "a", "task_id": "t", "attempt": True, "score": 0.2}])


def test_missing_pass_labels_make_pass_metrics_unavailable() -> None:
    task = (
        aggregate_scores([{"contestant_id": "a", "task_id": "t", "attempt": 1, "score": 0.2}])
        .contestants["a"]
        .tasks["t"]
    )

    assert task.pass_at_k is None
    assert task.pass_power_k is None


def test_cluster_bootstrap_resamples_tasks_then_repeats() -> None:
    result = cluster_bootstrap_ci([[0.0, 0.0], [1.0, 1.0]], samples=200, seed=3)

    assert result.estimate == 0.5
    assert result.low == 0.0
    assert result.high == 1.0


def test_head_to_head_resamples_paired_repeats_within_tasks() -> None:
    rows = [
        {
            "contestant_id": contestant,
            "task_id": task,
            "attempt": attempt,
            "score": score,
            "passed": score > 0.5,
        }
        for task, scores in {
            "task-1": {"a": [1.0, 0.0], "b": [0.0, 0.0]},
            "task-2": {"a": [0.0, 1.0], "b": [0.0, 0.0]},
        }.items()
        for contestant, attempts in scores.items()
        for attempt, score in enumerate(attempts, start=1)
    ]
    result = aggregate_scores(rows, bootstrap_samples=200, seed=8).compare("a", "b")

    assert result.difference.estimate == 0.5
    assert result.wins == 2
    assert result.ties == 0
    assert result.losses == 0


def test_identical_contestants_are_not_detectably_different() -> None:
    no_detectable_difference = 0
    for seed in range(200):
        samples = [[float((seed + task + repeat) % 2) for repeat in range(4)] for task in range(12)]
        result = aggregate_scores(
            [
                {
                    "contestant_id": contestant,
                    "task_id": f"task-{task}",
                    "attempt": repeat + 1,
                    "score": score,
                    "passed": score == 1.0,
                }
                for contestant in ("a", "b")
                for task, task_scores in enumerate(samples)
                for repeat, score in enumerate(task_scores)
            ],
            bootstrap_samples=200,
            seed=seed,
        )
        if result.compare("a", "b").no_detectable_difference:
            no_detectable_difference += 1

    assert no_detectable_difference >= 190
