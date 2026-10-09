"""Paired kit ablation effects and skill telemetry summaries."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, TypedDict, cast

from arena.stats.aggregate import ExcludedTrials
from arena.stats.bootstrap import (
    DEFAULT_SAMPLES,
    DEFAULT_SEED,
    ConfidenceInterval,
    cluster_bootstrap_ci,
)

SkillKind = Literal["listed", "loaded", "invoked"]
Arm = Literal["kit", "baseline"]


class KitTrialData(TypedDict, total=False):
    """Mapping input accepted for one kit or baseline trial."""

    trial_id: str
    arm: Arm
    task_id: str
    attempt: int
    score: float
    cost_usd: float | None
    swapped: bool
    unmetered: bool
    kit_unapplied: bool


@dataclass(frozen=True)
class KitTrial:
    trial_id: str
    arm: Arm
    task_id: str
    attempt: int
    score: float
    cost_usd: float | None = None
    swapped: bool = False
    unmetered: bool = False
    kit_unapplied: bool = False


@dataclass(frozen=True)
class SkillSession:
    """Skill telemetry for one session belonging to a kit-arm trial."""

    trial_id: str
    session_id: str
    events: Mapping[str, frozenset[SkillKind]]
    complete: bool = True


@dataclass(frozen=True)
class Uptake:
    sessions: int
    listed: int
    loaded: int
    invoked: int

    @property
    def listed_share(self) -> float | None:
        return self.listed / self.sessions if self.sessions else None

    @property
    def loaded_share(self) -> float | None:
        return self.loaded / self.sessions if self.sessions else None

    @property
    def invoked_share(self) -> float | None:
        return self.invoked / self.sessions if self.sessions else None


@dataclass(frozen=True)
class ObservationalSplit:
    label: Literal["observational"]
    invoked: ConfidenceInterval | None
    not_invoked: ConfidenceInterval | None
    score_difference: float | None


@dataclass(frozen=True)
class KitEffect:
    difference: ConfidenceInterval | None
    wins: int
    ties: int
    losses: int
    paired_tasks: int
    paired_repeats: int
    incomplete_pairs: int
    unmatched_pairs: int
    excluded_pairs: int
    excluded: ExcludedTrials
    uptake: Mapping[str, Uptake]
    observational: Mapping[str, ObservationalSplit]
    cost_delta_usd: float | None
    cost_pairs: int
    status: Literal[
        "estimated",
        "no_complete_pairs",
        "kit_installed_skills_not_used",
        "incomplete_telemetry",
    ]


def compute_kit_effect(
    trials: Iterable[KitTrial | Mapping[str, object]],
    sessions: Iterable[SkillSession],
    *,
    bootstrap_samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> KitEffect:
    """Compute paired kit-minus-baseline score effect and telemetry summaries.

    Pairs are exact ``(task_id, attempt)`` matches. A pair is eligible only when
    both trials are eligible under the aggregate exclusion flags. Incomplete or
    excluded pairs are omitted from the estimate and reported explicitly. The
    headline weights tasks equally and uses the aggregation task-cluster bootstrap.
    """
    if bootstrap_samples < 1:
        raise ValueError("bootstrap_samples must be positive")
    records = [_parse_trial(item) if not isinstance(item, KitTrial) else item for item in trials]
    seen_trials: set[str] = set()
    by_arm: dict[Arm, dict[tuple[str, int], KitTrial]] = {
        "kit": {},
        "baseline": {},
    }
    excluded_reasons = {"swapped": 0, "unmetered": 0, "kit_unapplied": 0, "total": 0}
    for record in records:
        _validate_trial(record)
        if record.trial_id in seen_trials:
            raise ValueError(f"duplicate trial_id: {record.trial_id}")
        seen_trials.add(record.trial_id)
        key = (record.task_id, record.attempt)
        if key in by_arm[record.arm]:
            raise ValueError(f"duplicate {record.arm} trial for task/attempt: {key}")
        by_arm[record.arm][key] = record
        if record.swapped or record.unmetered or record.kit_unapplied:
            if record.swapped:
                excluded_reasons["swapped"] += 1
            if record.unmetered:
                excluded_reasons["unmetered"] += 1
            if record.kit_unapplied:
                excluded_reasons["kit_unapplied"] += 1
            excluded_reasons["total"] += 1

    eligible_keys = {
        arm: {
            key
            for key, record in by_arm[arm].items()
            if not (record.swapped or record.unmetered or record.kit_unapplied)
        }
        for arm in ("kit", "baseline")
    }
    all_keys = by_arm["kit"].keys() | by_arm["baseline"].keys()
    complete_keys = eligible_keys["kit"] & eligible_keys["baseline"]
    incomplete_pairs = len(all_keys - complete_keys)
    excluded_pairs = sum(
        key in by_arm["kit"] and key in by_arm["baseline"] and key not in complete_keys
        for key in all_keys
    )
    unmatched_pairs = incomplete_pairs - excluded_pairs
    differences_by_task: dict[str, list[float]] = defaultdict(list)
    pair_records: list[tuple[KitTrial, KitTrial]] = []
    for task_id, attempt in sorted(complete_keys):
        kit = by_arm["kit"][(task_id, attempt)]
        baseline = by_arm["baseline"][(task_id, attempt)]
        differences_by_task[task_id].append(kit.score - baseline.score)
        pair_records.append((kit, baseline))

    interval = (
        cluster_bootstrap_ci(
            [differences_by_task[task] for task in sorted(differences_by_task)],
            samples=bootstrap_samples,
            seed=seed,
        )
        if differences_by_task
        else None
    )
    task_differences = [math.fsum(values) / len(values) for values in differences_by_task.values()]
    wins = sum(value > 0 for value in task_differences)
    losses = sum(value < 0 for value in task_differences)

    session_rows = list(sessions)
    _validate_sessions(session_rows, seen_trials)
    kit_trial_ids = {record.trial_id for record in by_arm["kit"].values()}
    kit_sessions = [session for session in session_rows if session.trial_id in kit_trial_ids]
    incomplete_trial_ids = {session.trial_id for session in kit_sessions if not session.complete}
    usable_sessions = [
        session
        for session in kit_sessions
        if session.complete and session.trial_id not in incomplete_trial_ids
    ]
    uptake = _compute_uptake(usable_sessions)
    observational = _compute_observational(usable_sessions, by_arm["kit"], bootstrap_samples, seed)
    cost_values = [
        kit.cost_usd - baseline.cost_usd
        for kit, baseline in pair_records
        if kit.cost_usd is not None and baseline.cost_usd is not None
    ]
    cost_delta = math.fsum(cost_values) / len(cost_values) if cost_values else None
    any_invocation = any(data.invoked > 0 for data in uptake.values())
    observed_kit_trial_ids = {session.trial_id for session in kit_sessions}
    partial_telemetry = bool(incomplete_trial_ids)
    missing_telemetry = any(
        not (record.swapped or record.unmetered or record.kit_unapplied)
        and record.trial_id not in observed_kit_trial_ids
        for record in by_arm["kit"].values()
    )
    status: Literal[
        "estimated",
        "no_complete_pairs",
        "kit_installed_skills_not_used",
        "incomplete_telemetry",
    ]
    if not pair_records:
        status = "no_complete_pairs"
    elif missing_telemetry or partial_telemetry:
        status = "incomplete_telemetry"
    elif uptake and not any_invocation:
        status = "kit_installed_skills_not_used"
    else:
        status = "estimated"

    return KitEffect(
        difference=interval,
        wins=wins,
        ties=len(task_differences) - wins - losses,
        losses=losses,
        paired_tasks=len(differences_by_task),
        paired_repeats=len(pair_records),
        incomplete_pairs=incomplete_pairs,
        unmatched_pairs=unmatched_pairs,
        excluded_pairs=excluded_pairs,
        excluded=ExcludedTrials(**excluded_reasons),
        uptake=uptake,
        observational=observational,
        cost_delta_usd=cost_delta,
        cost_pairs=len(cost_values),
        status=status,
    )


def _parse_trial(item: Mapping[str, object]) -> KitTrial:
    allowed = {
        "trial_id",
        "arm",
        "task_id",
        "attempt",
        "score",
        "cost_usd",
        "swapped",
        "unmetered",
        "kit_unapplied",
    }
    extra = item.keys() - allowed
    if extra:
        raise ValueError(f"unknown kit trial fields: {', '.join(sorted(extra))}")
    required = ("trial_id", "arm", "task_id", "attempt", "score")
    missing = [key for key in required if key not in item]
    if missing:
        raise ValueError(f"missing kit trial fields: {', '.join(missing)}")
    trial_id, arm, task_id = item["trial_id"], item["arm"], item["task_id"]
    attempt, score, cost = item["attempt"], item["score"], item.get("cost_usd")
    flags = {key: item.get(key, False) for key in ("swapped", "unmetered", "kit_unapplied")}
    if not isinstance(trial_id, str) or not trial_id:
        raise ValueError("trial_id must be a non-empty string")
    if arm not in ("kit", "baseline"):
        raise ValueError("arm must be 'kit' or 'baseline'")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id must be a non-empty string")
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise ValueError("attempt must be an integer")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("score must be numeric")
    if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float))):
        raise ValueError("cost_usd must be numeric or None")
    if any(not isinstance(flag, bool) for flag in flags.values()):
        raise ValueError("trial exclusion flags must be booleans")
    return KitTrial(
        trial_id=trial_id,
        arm=arm,
        task_id=task_id,
        attempt=attempt,
        score=float(score),
        cost_usd=float(cost) if cost is not None else None,
        **cast(dict[str, bool], flags),
    )


def _validate_trial(record: KitTrial) -> None:
    if not record.trial_id or not record.task_id:
        raise ValueError("trial_id and task_id must be non-empty")
    if record.attempt < 1:
        raise ValueError("attempt must be a positive integer")
    if not math.isfinite(record.score) or not 0 <= record.score <= 1:
        raise ValueError("trial score must be finite and between 0 and 1")
    if record.cost_usd is not None and (not math.isfinite(record.cost_usd) or record.cost_usd < 0):
        raise ValueError("cost_usd must be finite and non-negative")


def _validate_sessions(sessions: list[SkillSession], trial_ids: set[str]) -> None:
    seen: set[str] = set()
    for session in sessions:
        if not session.session_id or not session.trial_id:
            raise ValueError("session_id and trial_id must be non-empty")
        if session.session_id in seen:
            raise ValueError(f"duplicate session_id: {session.session_id}")
        if session.trial_id not in trial_ids:
            raise ValueError(f"unknown skill session trial: {session.trial_id}")
        seen.add(session.session_id)
        for skill, kinds in session.events.items():
            if not skill or not kinds or not kinds <= {"listed", "loaded", "invoked"}:
                raise ValueError("skill events require a skill and known event kinds")


def _compute_uptake(sessions: list[SkillSession]) -> dict[str, Uptake]:
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"listed": 0, "loaded": 0, "invoked": 0}
    )
    for session in sessions:
        for skill, kinds in session.events.items():
            for kind in kinds:
                counts[skill][kind] += 1
    return {
        skill: Uptake(len(sessions), values["listed"], values["loaded"], values["invoked"])
        for skill, values in counts.items()
    }


def _compute_observational(
    sessions: list[SkillSession],
    kit_trials: Mapping[tuple[str, int], KitTrial],
    samples: int,
    seed: int,
) -> dict[str, ObservationalSplit]:
    invoked_by_trial: dict[str, set[str]] = defaultdict(set)
    for session in sessions:
        for skill, kinds in session.events.items():
            if "invoked" in kinds:
                invoked_by_trial[session.trial_id].add(skill)
    skills: set[str] = {skill for session in sessions for skill in session.events}
    observed_trial_ids = {session.trial_id for session in sessions}
    result: dict[str, ObservationalSplit] = {}
    for skill in sorted(skills):
        groups: dict[bool, dict[str, list[float]]] = {
            True: defaultdict(list),
            False: defaultdict(list),
        }
        for (task_id, _), trial in kit_trials.items():
            if (
                trial.trial_id not in observed_trial_ids
                or trial.swapped
                or trial.unmetered
                or trial.kit_unapplied
            ):
                continue
            groups[skill in invoked_by_trial[trial.trial_id]][task_id].append(trial.score)
        intervals: dict[bool, ConfidenceInterval | None] = {}
        for invoked in (True, False):
            clusters = groups[invoked]
            intervals[invoked] = (
                cluster_bootstrap_ci(
                    [clusters[task] for task in sorted(clusters)],
                    samples=samples,
                    seed=seed + int(invoked),
                )
                if clusters
                else None
            )
        invoked_interval, not_invoked_interval = intervals[True], intervals[False]
        delta = (
            invoked_interval.estimate - not_invoked_interval.estimate
            if invoked_interval is not None and not_invoked_interval is not None
            else None
        )
        result[skill] = ObservationalSplit(
            "observational", invoked_interval, not_invoked_interval, delta
        )
    return result
