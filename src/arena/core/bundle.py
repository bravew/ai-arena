"""Read, analyze and export a validated run bundle."""

# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportOptionalMemberAccess=false, reportOptionalOperand=false, reportUnknownLambdaType=false

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from arena.core.bundle_contract import validate_bundle, validate_events_jsonl
from arena.core.cas import ArtifactStore
from arena.core.modelref import ModelRef
from arena.core.models import Call, Contestant, RunProvenance, Score, Session, Trial
from arena.core.store import Store, StoreError
from arena.stats.aggregate import TrialScore, aggregate_scores
from arena.stats.kit_effect import KitTrial, SkillKind, SkillSession, compute_kit_effect
from arena.stats.pairwise import PairwiseJudgment
from arena.stats.pareto import ParetoPoint, pareto_frontier
from arena.stats.ratings import bradley_terry
from arena.stats.rundiff import diff_runs
from arena.stats.series import chart_series
from arena.stats.sessions import summarize_sessions

_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class BundleError(RuntimeError):
    """The requested run cannot be read or exported faithfully."""


@dataclass(frozen=True)
class RunRecords:
    run: dict[str, Any]
    provenance: dict[str, Any]
    contestants: tuple[Contestant, ...]
    trials: tuple[Trial, ...]
    calls: tuple[Call, ...]
    scores: tuple[Score, ...]
    sessions: tuple[Session, ...]
    artifacts: tuple[dict[str, Any], ...]
    events: str
    kit_installs: tuple[dict[str, Any], ...] = ()


def default_home() -> Path:
    """Return the arena data directory, honoring ARENA_HOME."""
    return Path(os.environ.get("ARENA_HOME", Path.home() / ".arena")).expanduser()


def open_run(home: Path, run_id: str) -> RunRecords:
    """Load a complete run snapshot; missing or corrupt data is always an error."""
    if not _RUN_ID.fullmatch(run_id):
        raise BundleError(f"invalid run id: {run_id!r}")
    db_path = home / "arena.db"
    if not db_path.is_file():
        raise BundleError(f"no arena.db at {db_path}")
    try:
        with Store(db_path) as store:
            row = store.execute(
                "SELECT config_json, status, created_at FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise BundleError(f"run not found: {run_id}")
            config = _json_object(row[0], f"run {run_id} config_json")
            contestant_rows = config.get("contestants")
            if not isinstance(contestant_rows, list) or not contestant_rows:
                raise BundleError(f"run {run_id} config has no contestant snapshot")
            contestants = tuple(_contestant(item, run_id) for item in contestant_rows)
            contestant_ids = {item.id for item in contestants}
            raw_trials = store.execute(
                "SELECT id, run_id, contestant_id, task_id, attempt, status, flags_json "
                "FROM trials WHERE run_id = ? ORDER BY contestant_id, task_id, attempt",
                (run_id,),
            ).fetchall()
            trials: list[Trial] = []
            for item in raw_trials:
                flags = _json_object(item["flags_json"], f"trial {item['id']} flags_json")
                trials.append(
                    Trial.model_validate(
                        {
                            "id": item["id"],
                            "run_id": item["run_id"],
                            "contestant_id": item["contestant_id"],
                            "task_id": item["task_id"],
                            "attempt": item["attempt"],
                            "status": item["status"],
                            "flags": flags,
                        }
                    )
                )
            unknown = {trial.contestant_id for trial in trials} - contestant_ids
            if unknown:
                raise BundleError(
                    f"run {run_id} trials refer to unknown contestants: {sorted(unknown)}"
                )
            calls: list[Call] = []
            for item in store.execute(
                "SELECT id, details_json FROM calls WHERE run_id = ? ORDER BY seq, id", (run_id,)
            ):
                try:
                    calls.append(Call.model_validate(json.loads(item["details_json"])))
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    raise BundleError(f"invalid call {item['id']} details_json: {exc}") from exc
            scores = [
                Score.model_validate(
                    {
                        "trial_id": item["trial_id"],
                        "scorer_id": item["scorer_id"],
                        "scorer_version": item["scorer_version"],
                        "value": item["value"],
                        "normalized": item["normalized"],
                        "passed": bool(item["passed"]) if item["passed"] is not None else None,
                        "rationale": item["rationale"],
                        "evidence": _json_object(item["evidence_json"], "score evidence_json"),
                    }
                )
                for item in store.execute(
                    "SELECT s.trial_id, s.scorer_id, s.scorer_version, s.value, s.normalized, "
                    "s.passed, s.rationale, s.evidence_json FROM scores s JOIN trials t "
                    "ON t.id = s.trial_id WHERE t.run_id = ? ORDER BY s.trial_id, s.scorer_id, "
                    "s.scorer_version",
                    (run_id,),
                )
            ]
            artifact_rows = store.execute(
                "SELECT a.trial_id, a.sha256, a.path, a.mime, a.render_hint "
                "FROM trial_artifacts a JOIN trials t ON t.id = a.trial_id "
                "WHERE t.run_id = ? ORDER BY a.trial_id, a.path",
                (run_id,),
            ).fetchall()
            artifacts = [dict(item) for item in artifact_rows]
            event_rows = _event_rows(home, run_id)
            kit_installs = [item["data"] for item in event_rows if item["kind"] == "kit_installed"]
            session_rows, session_errors = _sessions(event_rows, calls, trials, contestants)
            if session_errors:
                raise BundleError(session_errors[0])
            started = str(config.get("started_at") or row["created_at"]).replace(" ", "T")
            if not started.endswith("Z") and "+" not in started:
                started += "Z"
            run = {
                "id": run_id,
                "suite_id": str(config.get("suite_id", "unknown")),
                "started_at": started,
                "ended_at": config.get("ended_at"),
                "status": row["status"],
            }
            provenance = RunProvenance.model_validate(config.get("provenance", {})).model_dump(
                mode="json"
            )
        events = _read_events(home, run_id)
        validate_events_jsonl(events)
    except BundleError:
        raise
    except (StoreError, OSError, ValueError, TypeError, KeyError) as exc:
        raise BundleError(f"cannot read run {run_id}: {exc}") from exc
    return RunRecords(
        run,
        provenance,
        contestants,
        tuple(trials),
        tuple(calls),
        tuple(scores),
        tuple(session_rows),
        tuple(artifacts),
        events,
        tuple(kit_installs),
    )


def _event_rows(home: Path, run_id: str) -> list[dict[str, Any]]:
    events = _read_events(home, run_id)
    validate_events_jsonl(events)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(events.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BundleError(f"invalid events.jsonl line {line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise BundleError(f"invalid events.jsonl line {line_number}: expected an object")
        rows.append(cast(dict[str, Any], value))
    return rows


def _read_events(home: Path, run_id: str) -> str:
    path = home / "runs" / run_id / "events.jsonl"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BundleError(f"cannot read events.jsonl for run {run_id}: {exc}") from exc


def _json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BundleError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise BundleError(f"invalid {label}: expected an object")
    return cast(dict[str, Any], value)


def _contestant(raw: Any, run_id: str) -> Contestant:
    if not isinstance(raw, dict):
        raise BundleError(f"run {run_id} has invalid contestant snapshot")
    data = dict(raw)
    identity = data.pop("id", None)
    data["model"] = ModelRef.parse(str(data.get("model", "")))
    try:
        contestant = Contestant.model_validate(data)
    except ValueError as exc:
        raise BundleError(f"run {run_id} has invalid contestant: {exc}") from exc
    if identity != contestant.id:
        raise BundleError(f"run {run_id} contestant identity does not match its config")
    return contestant


def _agent_for_trial(
    trial_id: Any, trials: dict[str, Trial], contestants: dict[str, Contestant]
) -> str:
    trial = trials.get(trial_id) if isinstance(trial_id, str) else None
    contestant = contestants.get(trial.contestant_id) if trial else None
    return contestant.scaffold.id if contestant and contestant.scaffold else "unknown"


def _sessions(
    events: list[dict[str, Any]],
    calls: list[Call],
    trials: list[Trial],
    contestants: tuple[Contestant, ...],
) -> tuple[list[Session], list[str]]:
    by_id: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    trial_by_id = {trial.id: trial for trial in trials}
    contestant_by_id = {contestant.id: contestant for contestant in contestants}
    for event in events:
        kind, data = event.get("kind"), event.get("data", {})
        if not isinstance(data, dict):
            continue
        session_id = data.get("session_id")
        trial_id = data.get("trial_id")
        trial = trial_by_id.get(trial_id) if isinstance(trial_id, str) else None
        session_status = trial.status if trial is not None else "unknown"
        if kind == "session_turn" and isinstance(session_id, str):
            row = by_id.setdefault(
                session_id,
                {
                    "id": session_id,
                    "trial_id": data.get("trial_id"),
                    "agent": _agent_for_trial(data.get("trial_id"), trial_by_id, contestant_by_id),
                    "status": session_status,
                    "turns": [],
                },
            )
            row["turns"].append(data.get("turn", {}))
        elif kind == "skill_event" and isinstance(session_id, str):
            row = by_id.setdefault(
                session_id,
                {
                    "id": session_id,
                    "trial_id": data.get("trial_id"),
                    "agent": _agent_for_trial(data.get("trial_id"), trial_by_id, contestant_by_id),
                    "status": session_status,
                    "turns": [],
                },
            )
            row["turns"].append(
                {
                    "skill_events": [data.get("skill_event")],
                    "call_ids": [],
                    "tool_calls": [],
                    "mcp_calls": [],
                    "files": [],
                    "unmetered": False,
                }
            )
    call_ids = {call.id for call in calls}
    trial_ids = set(trial_by_id)
    for row in by_id.values():
        if row["trial_id"] not in trial_ids:
            errors.append(f"session {row['id']} refers to unknown trial {row['trial_id']}")
            continue
        for turn in row["turns"]:
            if set(turn.get("call_ids", [])) - call_ids:
                errors.append(f"session {row['id']} refers to an unknown call")
    try:
        return [Session.model_validate(row) for row in by_id.values()], errors
    except ValueError as exc:
        errors.append(f"invalid session event data: {exc}")
        return [], errors


def build_bundle(records: RunRecords) -> dict[str, Any]:
    """Create the schema-v2 metadata bundle from a loaded run."""
    trials = [row.model_dump(mode="json", exclude_none=False) for row in records.trials]
    for row in trials:
        # Empty flag maps in older stores still serialize to the full flag contract.
        row["flags"] = {
            key: row["flags"].get(key, False)
            for key in ("unmetered", "swapped", "subscription_served", "kit_unapplied")
        }
    bundle: dict[str, Any] = {
        "bundle_version": 2,
        "run": records.run,
        "provenance": records.provenance,
        "contestants": [
            {
                "id": item.id,
                **item.model_dump(mode="json", exclude={"model", "label"}),
                "label": item.label,
                "model": str(item.model),
            }
            for item in records.contestants
        ],
        "trials": trials,
        "calls": [item.model_dump(mode="json") for item in records.calls],
        "scores": [item.model_dump(mode="json") for item in records.scores],
        "sessions": [item.model_dump(mode="json") for item in records.sessions],
        "kit_installs": list(records.kit_installs),
        "artifacts": list(records.artifacts),
    }
    validate_bundle(bundle)
    return bundle


def _trial_scores(records: RunRecords) -> list[TrialScore]:
    by_trial: dict[str, list[Score]] = defaultdict(list)
    versions_by_scorer: dict[str, set[str]] = defaultdict(set)
    for score in records.scores:
        by_trial[score.trial_id].append(score)
        versions_by_scorer[score.scorer_id].add(score.scorer_version)
    if any(len(versions) > 1 for versions in versions_by_scorer.values()):
        raise BundleError("run has mixed scorer_version values for a scorer")
    result: list[TrialScore] = []
    trials = {trial.id: trial for trial in records.trials}
    for trial_id, score_rows in by_trial.items():
        versions: dict[str, set[str]] = defaultdict(set)
        for score in score_rows:
            versions[score.scorer_id].add(score.scorer_version)
        if any(len(items) > 1 for items in versions.values()):
            raise BundleError(f"trial {trial_id} has mixed scorer_version values")
        # A trial can have multiple scorers. The stable headline is their mean normalized score.
        trial = trials[trial_id]
        result.append(
            TrialScore(
                trial.contestant_id,
                trial.task_id,
                trial.attempt,
                sum(score.normalized for score in score_rows) / len(score_rows),
                all(score.passed is True for score in score_rows)
                if all(score.passed is not None for score in score_rows)
                else None,
                trial.flags.swapped,
                trial.flags.unmetered,
                trial.flags.kit_unapplied,
            )
        )
    return result


def _call_summary(calls: tuple[Call, ...]) -> dict[str, Any]:
    purposes: dict[str, dict[str, Any]] = {}
    for call in calls:
        row = purposes.setdefault(
            call.purpose,
            {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "unpriced_calls": 0},
        )
        row["calls"] += 1
        row["tokens_in"] += call.tokens.in_
        row["tokens_out"] += call.tokens.out
        if call.cost_usd is None:
            row["unpriced_calls"] += 1
        else:
            row["cost_usd"] += call.cost_usd
    return {
        "calls": len(calls),
        "tokens_in": sum(call.tokens.in_ for call in calls),
        "tokens_out": sum(call.tokens.out for call in calls),
        "cost_usd": sum(call.cost_usd or 0 for call in calls),
        "unpriced_calls": sum(call.cost_usd is None for call in calls),
        "price_versions": sorted({call.price_version for call in calls if call.price_version}),
        "by_purpose": purposes,
    }


def _plain(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _plain(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def compute_stats(
    records: RunRecords,
    *,
    judgments: list[PairwiseJudgment] | None = None,
    baseline: RunRecords | None = None,
) -> dict[str, Any]:
    """Derive deterministic leaderboard, ratings, Pareto, run-diff, sessions and series."""
    scores = _trial_scores(records)
    aggregation = aggregate_scores(scores)
    trial_by_contestant: dict[str, list[Trial]] = defaultdict(list)
    for trial in records.trials:
        trial_by_contestant[trial.contestant_id].append(trial)
    summary = _call_summary(records.calls)
    calls_by_trial: dict[str, list[Call]] = defaultdict(list)
    for call in records.calls:
        if call.trial_id:
            calls_by_trial[call.trial_id].append(call)
    costs: dict[str, float | None] = {}
    tokens: dict[str, float] = {}
    for contestant in records.contestants:
        owner_trials = trial_by_contestant[contestant.id]
        eligible_trial_ids = {
            trial.id
            for trial in owner_trials
            if not (trial.flags.swapped or trial.flags.unmetered or trial.flags.kit_unapplied)
        }
        owner_calls = [call for call in records.calls if call.trial_id in eligible_trial_ids]
        costs[contestant.id] = (
            sum(call.cost_usd for call in owner_calls if call.cost_usd is not None)
            if owner_calls and any(call.cost_usd is not None for call in owner_calls)
            else None
        )
        tokens[contestant.id] = sum(
            call.tokens.in_ + call.tokens.out for call in owner_calls
        ) / max(1, len(owner_trials))
    leaderboard = []
    top_id = max(
        (key for key, value in aggregation.contestants.items() if value.suite is not None),
        key=lambda key: (aggregation.contestants[key].suite.estimate, key),
        default=None,
    )
    for contestant in records.contestants:
        row = aggregation.contestants.get(contestant.id)
        suite = row.suite if row else None
        compare = None
        if (
            top_id is not None
            and contestant.id != top_id
            and contestant.id in aggregation.contestants
        ):
            try:
                compare = _plain(aggregation.compare(top_id, contestant.id))
                compare["no_detectable_difference"] = aggregation.compare(
                    top_id, contestant.id
                ).no_detectable_difference
            except ValueError:
                pass
        leaderboard.append(
            {
                "contestant_id": contestant.id,
                "label": contestant.label or contestant.id,
                "suite": _plain(suite),
                "tasks": {key: _plain(value) for key, value in (row.tasks.items() if row else [])},
                "excluded": _plain(row.excluded)
                if row
                else {"swapped": 0, "unmetered": 0, "kit_unapplied": 0, "total": 0},
                "vs_top": compare,
                "cost_usd_per_task": costs[contestant.id]
                / max(1, len({t.task_id for t in trial_by_contestant[contestant.id]}))
                if costs[contestant.id] is not None
                else None,
                "tokens_per_task": tokens[contestant.id],
            }
        )
    leaderboard.sort(
        key=lambda item: (
            -(item["suite"]["estimate"] if item["suite"] else -1),
            item["contestant_id"],
        )
    )
    points = [
        ParetoPoint(
            contestant.id,
            aggregation.contestants[contestant.id].suite.estimate
            if contestant.id in aggregation.contestants
            and aggregation.contestants[contestant.id].suite
            else 0,
            tokens[contestant.id],
            costs[contestant.id]
            / max(1, len({t.task_id for t in trial_by_contestant[contestant.id]}))
            if costs[contestant.id] is not None
            else None,
            any(t.flags.subscription_served for t in trial_by_contestant[contestant.id]),
        )
        for contestant in records.contestants
    ]
    pareto = pareto_frontier(points)
    ratings: dict[str, Any] = {}
    for judge in ("model", "human"):
        selected = [item for item in judgments or [] if item.judge == judge]
        if selected:
            result = bradley_terry(selected, judge=cast(Any, judge))
            ratings[judge] = {
                "leaderboards": [
                    [_plain(item) for item in group] for group in result.leaderboard()
                ],
                "connected_components": result.connected_components,
            }
    kit_effects = []
    try:
        kit_pairs = _kit_pairs(records)
        for baseline_contestant, treatment_contestant in kit_pairs:
            ids = {baseline_contestant.id, treatment_contestant.id}
            trial_ids = {trial.id for trial in records.trials if trial.contestant_id in ids}
            trials = []
            for trial in records.trials:
                if trial.contestant_id not in ids:
                    continue
                owner = (
                    baseline_contestant
                    if trial.contestant_id == baseline_contestant.id
                    else treatment_contestant
                )
                calls = calls_by_trial[trial.id]
                cost = (
                    sum(call.cost_usd for call in calls if call.cost_usd is not None)
                    if calls and any(call.cost_usd is not None for call in calls)
                    else None
                )
                trial_score = next(
                    (
                        record
                        for record in scores
                        if record.contestant_id == trial.contestant_id
                        and record.task_id == trial.task_id
                        and record.attempt == trial.attempt
                    ),
                    None,
                )
                if trial_score:
                    trials.append(
                        KitTrial(
                            trial.id,
                            "baseline" if owner.id == baseline_contestant.id else "kit",
                            trial.task_id,
                            trial.attempt,
                            trial_score.score,
                            cost,
                            trial.flags.swapped,
                            trial.flags.unmetered,
                            trial.flags.kit_unapplied,
                        )
                    )
            sessions = []
            for session in records.sessions:
                if session.trial_id not in trial_ids:
                    continue
                skill_events: dict[str, set[SkillKind]] = defaultdict(set)
                for turn in session.turns:
                    for event in turn.skill_events:
                        skill_events[event.skill].add(event.kind)
                sessions.append(
                    SkillSession(
                        session.trial_id,
                        session.id,
                        {key: frozenset(value) for key, value in skill_events.items()},
                        session.status in {"succeeded", "complete", "completed"},
                    )
                )
            result = compute_kit_effect(trials, sessions)
            kit_effects.append(
                {
                    "baseline_id": baseline_contestant.id,
                    "treatment_id": treatment_contestant.id,
                    **_plain(result),
                }
            )
    except ValueError as exc:
        raise BundleError(f"cannot compute kit effect: {exc}") from exc
    session_summary, group_summary = summarize_sessions(
        records.sessions, records.calls, records.trials
    )
    usage, latency, prompt = chart_series(records.calls, records.trials)
    run_diff = None
    if baseline is not None:
        before_stats = compute_stats(baseline)
        before_agg = aggregate_scores(_trial_scores(baseline))
        after_agg = aggregation
        before_cost = {
            key: value for key, value in _cost_by_contestant(baseline).items() if value is not None
        }
        after_cost = {
            key: value for key, value in _cost_by_contestant(records).items() if value is not None
        }
        run_diff = _plain(
            diff_runs(
                before_agg,
                after_agg,
                before_cost_usd=before_cost,
                after_cost_usd=after_cost,
                before_records=_trial_scores(baseline),
                after_records=scores,
            )
        )
        del before_stats
    return {
        "leaderboard": leaderboard,
        "pareto": {
            "cost_axis": pareto.cost_axis,
            "frontier": [point.contestant_id for point in pareto.frontier],
        },
        "ratings": ratings,
        "kit_effects": kit_effects,
        "sessions": {"individual": _plain(session_summary), "groups": _plain(group_summary)},
        "series": {
            "usage": _plain(usage),
            "latency": _plain(latency),
            "prompt_composition": _plain(prompt),
        },
        "calls": summary,
        "price_versions": summary["price_versions"],
        "run_diff": {"contestants": run_diff} if run_diff is not None else None,
    }


def _kit_pairs(records: RunRecords) -> list[tuple[Contestant, Contestant]]:
    baseline_by_config = {
        json.dumps(
            item.resolved_config() | {"kit_hash": "none"}, sort_keys=True, separators=(",", ":")
        ): item
        for item in records.contestants
        if item.kit_hash == "none"
    }
    pairs = []
    for treatment in records.contestants:
        if treatment.kit_hash == "none":
            continue
        identity = treatment.resolved_config() | {"kit_hash": "none"}
        key = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        baseline = baseline_by_config.get(key)
        if baseline is not None:
            pairs.append((baseline, treatment))
    return pairs


def _cost_by_contestant(records: RunRecords) -> dict[str, float | None]:
    result = {}
    for contestant in records.contestants:
        trials = {trial.id for trial in records.trials if trial.contestant_id == contestant.id}
        calls = [call for call in records.calls if call.trial_id in trials]
        result[contestant.id] = (
            sum(call.cost_usd or 0 for call in calls)
            if calls and all(call.cost_usd is not None for call in calls)
            else None
        )
    return result


def export_bundle(records: RunRecords, stats: dict[str, Any], out: Path, home: Path) -> None:
    """Write all bundle files into a new directory, then atomically publish it."""
    if out.exists():
        raise BundleError(f"output already exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    try:
        bundle = build_bundle(records)
        files: dict[str, bytes] = {
            "bundle.json": _json_bytes(bundle),
            "calls-summary.json": _json_bytes(_call_summary(records.calls)),
            "events.jsonl": records.events.encode("utf-8"),
            "series.json": _json_bytes(stats["series"]),
            "stats.json": _json_bytes(stats),
        }
        blobs = ArtifactStore(home / "artifacts")
        artifact_dir = staging / "artifacts"
        for artifact in records.artifacts:
            try:
                data = blobs.get(artifact["sha256"])
            except (OSError, ValueError) as exc:
                raise BundleError(f"cannot read artifact blob {artifact['sha256']}: {exc}") from exc
            artifact_dir.mkdir(exist_ok=True)
            (artifact_dir / artifact["sha256"]).write_bytes(data)
        manifest = {
            "manifest_version": 1,
            "bundle_version": 2,
            "event_version": 1,
            "run_id": records.run["id"],
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            "artifacts": sorted({row["sha256"] for row in records.artifacts}),
        }
        files["manifest.json"] = _json_bytes(manifest)
        for name, data in files.items():
            (staging / name).write_bytes(data)
        staging.rename(out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def render_report(records: RunRecords, stats: dict[str, Any]) -> str:
    """Render a portable Markdown report with pricing metadata and exclusions."""
    versions = stats["price_versions"]
    price_version = ", ".join(versions) if versions else "unknown"
    lines = [
        f"# Arena report: {records.run['id']}",
        "",
        f"- Suite: `{records.run['suite_id']}`",
        f"- Status: `{records.run['status']}`",
        f"- Provenance: {records.provenance['origin']} ({records.provenance['verification']})",
        f"- price_version: {price_version}",
        "",
        "## Leaderboard",
        "",
        "| Rank | Contestant | Score | 95% CI | Cost / task | Tokens / task |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    excluded: list[str] = []
    for rank, row in enumerate(stats["leaderboard"], 1):
        suite = row["suite"]
        label = row["label"]
        flags = row["excluded"]
        if flags["total"]:
            label += "†"
            excluded.append(
                f"† {row['label']}: {flags['swapped']} swapped, "
                f"{flags['unmetered']} unmetered, {flags['kit_unapplied']} kit_unapplied "
                f"({flags['total']} trials)"
            )
        ci = f"[{suite['low']:.3f}, {suite['high']:.3f}]" if suite else "—"
        score = f"{suite['estimate']:.3f}" if suite else "—"
        cost = (
            f"${row['cost_usd_per_task']:.4f}"
            if row["cost_usd_per_task"] is not None
            else "unknown / flat"
        )
        lines.append(
            f"| {rank} | {label} | {score} | {ci} | {cost} | {row['tokens_per_task']:.0f} |"
        )
    if excluded:
        lines.extend(["", "## Excluded from headline numbers", "", *excluded])
    lines.extend(
        [
            "",
            "## Calls",
            "",
            f"{stats['calls']['calls']} calls · {stats['calls']['tokens_in']} input tokens · "
            f"{stats['calls']['tokens_out']} output tokens · "
            f"{stats['calls']['unpriced_calls']} unpriced calls.",
            "",
        ]
    )
    if stats["run_diff"] is not None:
        lines.extend(
            [
                "## Run diff",
                "",
                "| Contestant | Task | Change | Status |",
                "| --- | --- | ---: | --- |",
            ]
        )
        for contestant in stats["run_diff"]["contestants"]:
            for task in contestant["tasks"]:
                difference = task["difference"] if task["difference"] is not None else "—"
                lines.append(
                    f"| {contestant['contestant_id']} | {task['task_id']} | "
                    f"{difference} | {task['status']} |"
                )
        lines.append("")
    return "\n".join(lines)
