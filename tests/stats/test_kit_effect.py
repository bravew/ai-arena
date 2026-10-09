from __future__ import annotations

import random

import pytest

from arena.stats.kit_effect import SkillSession, compute_kit_effect


def row(
    trial_id: str,
    arm: str,
    task_id: str,
    attempt: int,
    score: float,
    **extra: object,
) -> dict[str, object]:
    return {
        "trial_id": trial_id,
        "arm": arm,
        "task_id": task_id,
        "attempt": attempt,
        "score": score,
        **extra,
    }


def test_hand_calculated_paired_effect_task_weighting_and_cost() -> None:
    result = compute_kit_effect(
        [
            row("k-a1", "kit", "a", 1, 0.7, cost_usd=0.20),
            row("b-a1", "baseline", "a", 1, 0.5, cost_usd=0.10),
            row("k-a2", "kit", "a", 2, 0.9, cost_usd=0.30),
            row("b-a2", "baseline", "a", 2, 0.5, cost_usd=0.10),
            row("k-b1", "kit", "b", 1, 0.6, cost_usd=0.15),
            row("b-b1", "baseline", "b", 1, 0.4, cost_usd=0.10),
            row("k-b2", "kit", "b", 2, 0.4, cost_usd=0.10),
            row("b-b2", "baseline", "b", 2, 0.4, cost_usd=0.10),
        ],
        [],
        bootstrap_samples=200,
        seed=17,
    )

    assert result.difference is not None
    assert result.difference.estimate == pytest.approx(0.2)
    assert result.difference.low <= result.difference.estimate <= result.difference.high
    assert (result.wins, result.ties, result.losses) == (2, 0, 0)
    assert (result.paired_tasks, result.paired_repeats) == (2, 4)
    assert result.cost_delta_usd == pytest.approx(0.0875)
    assert result.cost_pairs == 4


def test_incomplete_and_excluded_pairs_are_reported_and_excluded_from_estimate() -> None:
    result = compute_kit_effect(
        [
            row("k-ok", "kit", "ok", 1, 0.8),
            row("b-ok", "baseline", "ok", 1, 0.5),
            row("k-missing", "kit", "missing", 1, 1.0),
            row("b-excluded", "baseline", "excluded", 1, 0.9, swapped=True),
            row("k-excluded", "kit", "excluded", 1, 0.7),
        ],
        [],
    )

    assert result.difference is not None and result.difference.estimate == pytest.approx(0.3)
    assert result.incomplete_pairs == 2
    assert result.unmatched_pairs == 1
    assert result.excluded_pairs == 1
    assert result.excluded.swapped == 1
    assert result.excluded.total == 1
    assert result.paired_repeats == 1


def test_uptake_and_observational_split_are_explicit_with_zero_uptake() -> None:
    trials = [
        row("k1", "kit", "t1", 1, 0.5, cost_usd=0.20),
        row("b1", "baseline", "t1", 1, 0.5, cost_usd=0.10),
        row("k2", "kit", "t2", 1, 0.4, cost_usd=0.20),
        row("b2", "baseline", "t2", 1, 0.4, cost_usd=0.10),
    ]
    sessions = [
        SkillSession("k1", "s1", {"skill-a": frozenset({"listed", "loaded"})}),
        SkillSession("k2", "s2", {"skill-a": frozenset({"listed"})}),
    ]

    result = compute_kit_effect(trials, sessions)

    assert result.status == "kit_installed_skills_not_used"
    assert result.difference is not None and result.difference.estimate == 0.0
    uptake = result.uptake["skill-a"]
    assert (uptake.sessions, uptake.listed, uptake.loaded, uptake.invoked) == (2, 2, 1, 0)
    assert (uptake.listed_share, uptake.loaded_share, uptake.invoked_share) == (1.0, 0.5, 0.0)
    split = result.observational["skill-a"]
    assert split.label == "observational"
    assert split.invoked is None
    assert split.not_invoked is not None
    assert split.score_difference is None
    assert result.cost_delta_usd == pytest.approx(0.1)


def test_partial_session_telemetry_does_not_claim_skills_were_not_used() -> None:
    trials = [
        row("k-observed", "kit", "observed", 1, 0.5),
        row("b-observed", "baseline", "observed", 1, 0.5),
        row("k-unobserved", "kit", "unobserved", 1, 0.5),
        row("b-unobserved", "baseline", "unobserved", 1, 0.5),
    ]
    sessions = [
        SkillSession("k-observed", "s1", {"skill-a": frozenset({"listed"})}),
    ]

    result = compute_kit_effect(trials, sessions)

    assert result.status == "incomplete_telemetry"


def test_invoked_not_invoked_scores_are_observational_and_task_clustered() -> None:
    trials = [
        row("k1", "kit", "task-a", 1, 1.0),
        row("b1", "baseline", "task-a", 1, 0.0),
        row("k2", "kit", "task-b", 1, 0.0),
        row("b2", "baseline", "task-b", 1, 0.0),
    ]
    sessions = [
        SkillSession("k1", "s1", {"skill-a": frozenset({"invoked"})}),
        SkillSession("k2", "s2", {"skill-a": frozenset({"loaded"})}),
    ]

    split = compute_kit_effect(trials, sessions).observational["skill-a"]

    assert split.label == "observational"
    assert split.invoked is not None and split.invoked.estimate == 1.0
    assert split.not_invoked is not None and split.not_invoked.estimate == 0.0
    assert split.score_difference == 1.0


def test_observational_split_omits_kit_trials_without_session_telemetry() -> None:
    trials = [
        row("k-invoked", "kit", "invoked", 1, 1.0),
        row("b-invoked", "baseline", "invoked", 1, 0.0),
        row("k-not-invoked", "kit", "not-invoked", 1, 0.0),
        row("b-not-invoked", "baseline", "not-invoked", 1, 0.0),
        row("k-no-session", "kit", "no-session", 1, 1.0),
        row("b-no-session", "baseline", "no-session", 1, 0.0),
    ]
    sessions = [
        SkillSession("k-invoked", "s1", {"skill-a": frozenset({"invoked"})}),
        SkillSession("k-not-invoked", "s2", {"skill-a": frozenset({"listed"})}),
    ]

    split = compute_kit_effect(trials, sessions).observational["skill-a"]

    assert split.invoked is not None and split.invoked.estimate == 1.0
    assert split.not_invoked is not None and split.not_invoked.estimate == 0.0
    assert split.score_difference == 1.0


def test_planted_lift_and_zero_are_recovered_in_seeded_simulations() -> None:
    positive_lift_recovered = 0
    zero_lift_undetected = 0
    for seed in range(200):
        rng = random.Random(seed)
        lift_trials: list[dict[str, object]] = []
        zero_trials: list[dict[str, object]] = []
        for task in range(16):
            lift_baseline_mean = rng.uniform(0.35, 0.50)
            zero_mean = rng.uniform(0.35, 0.65)
            for attempt in range(3):
                lift_baseline = rng.uniform(lift_baseline_mean - 0.25, lift_baseline_mean + 0.25)
                lift_kit = rng.uniform(
                    lift_baseline_mean + 0.15 - 0.25,
                    lift_baseline_mean + 0.15 + 0.25,
                )
                lift_trials.extend(
                    [
                        row(f"kl-{task}-{attempt}", "kit", str(task), attempt + 1, lift_kit),
                        row(
                            f"bl-{task}-{attempt}",
                            "baseline",
                            str(task),
                            attempt + 1,
                            lift_baseline,
                        ),
                    ]
                )
                zero_kit = rng.uniform(zero_mean - 0.30, zero_mean + 0.30)
                zero_baseline = rng.uniform(zero_mean - 0.30, zero_mean + 0.30)
                zero_trials.extend(
                    [
                        row(f"kz-{task}-{attempt}", "kit", str(task), attempt + 1, zero_kit),
                        row(
                            f"bz-{task}-{attempt}",
                            "baseline",
                            str(task),
                            attempt + 1,
                            zero_baseline,
                        ),
                    ]
                )
        positive = compute_kit_effect(lift_trials, [], bootstrap_samples=300, seed=seed)
        if (
            positive.difference is not None
            and positive.difference.low <= 0.15 <= positive.difference.high
        ):
            positive_lift_recovered += 1
        zero = compute_kit_effect(zero_trials, [], bootstrap_samples=300, seed=seed)
        if zero.difference is not None and zero.difference.contains_zero:
            zero_lift_undetected += 1

    assert positive_lift_recovered >= 190
    assert zero_lift_undetected >= 190


def test_no_eligible_complete_pairs_have_no_interval() -> None:
    result = compute_kit_effect(
        [
            row("k", "kit", "t", 1, 0.8, unmetered=True),
            row("b", "baseline", "t", 1, 0.5),
        ],
        [],
    )

    assert result.difference is None
    assert result.status == "no_complete_pairs"
    assert result.excluded.unmetered == 1
    assert result.excluded.total == 1


def test_invalid_and_duplicate_inputs_fail_explicitly() -> None:
    with pytest.raises(ValueError, match="duplicate kit trial"):
        compute_kit_effect([row("k1", "kit", "t", 1, 0.4), row("k2", "kit", "t", 1, 0.5)], [])
    with pytest.raises(ValueError, match="unknown skill session trial"):
        compute_kit_effect(
            [row("k", "kit", "t", 1, 0.4)],
            [SkillSession("unknown", "s", {})],
        )
