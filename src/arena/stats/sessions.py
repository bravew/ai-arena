"""Per-session and grouped session summaries with explicit partial-data accounting."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from arena.core.models import Call, Session, Trial


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    trial_id: str
    contestant_id: str | None
    agent: str
    status: str
    partial: bool
    turns: int
    tool_calls: int
    tool_errors: int
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    wall_time_ms: int | None
    files_touched: int


@dataclass(frozen=True)
class SessionGroupSummary:
    group: str
    sessions: int
    partial_sessions: int
    turns: int
    tool_calls: int
    tool_errors: int
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    wall_time_ms: int | None
    files_touched: int


def summarize_sessions(
    sessions: Iterable[Session],
    calls: Iterable[Call],
    trials: Iterable[Trial],
    *,
    complete_trial_ids: set[str] | None = None,
) -> tuple[tuple[SessionSummary, ...], tuple[SessionGroupSummary, ...]]:
    """Summarize session turns and linked calls; missing linkage yields unknown metrics.

    Sessions are partial when their trial is incomplete, status is not successful, or
    one or more call IDs cannot be resolved. Token/cost totals are None for an entire
    session if at least one referenced call is absent; individual flat-fee costs are
    unknown if any resolved call has null cost.
    """
    call_map = {call.id: call for call in calls}
    trial_map = {trial.id: trial for trial in trials}
    summaries: list[SessionSummary] = []
    for session in sorted(sessions, key=lambda value: value.id):
        trial = trial_map.get(session.trial_id)
        referenced = [call_id for turn in session.turns for call_id in turn.call_ids]
        resolved = [call_map[call_id] for call_id in referenced if call_id in call_map]
        missing = len(resolved) != len(referenced)
        complete = complete_trial_ids is None or session.trial_id in complete_trial_ids
        partial = (
            missing or not complete or session.status not in {"succeeded", "complete", "completed"}
        )
        tokens_in = sum(call.tokens.in_ for call in resolved) if not missing else None
        tokens_out = sum(call.tokens.out for call in resolved) if not missing else None
        cost = (
            sum(call.cost_usd for call in resolved if call.cost_usd is not None)
            if resolved and not missing and all(call.cost_usd is not None for call in resolved)
            else (0.0 if not resolved and not missing else None)
        )
        wall_ms = (
            int((session.ended_at - session.started_at).total_seconds() * 1000)
            if session.started_at is not None and session.ended_at is not None
            else None
        )
        tool_calls = [tool for turn in session.turns for tool in turn.tool_calls]
        summaries.append(
            SessionSummary(
                session.id,
                session.trial_id,
                trial.contestant_id if trial else None,
                session.agent,
                session.status,
                partial,
                len(session.turns),
                len(tool_calls),
                sum(tool.exit_status not in (None, 0) for tool in tool_calls),
                tokens_in,
                tokens_out,
                cost,
                wall_ms,
                sum(len(turn.files) for turn in session.turns),
            )
        )
    groups: dict[str, list[SessionSummary]] = defaultdict(list)
    for summary in summaries:
        groups[summary.contestant_id or "unknown"].append(summary)
    grouped: list[SessionGroupSummary] = []
    for key in sorted(groups):
        rows = groups[key]
        grouped.append(
            SessionGroupSummary(
                key,
                len(rows),
                sum(row.partial for row in rows),
                sum(row.turns for row in rows),
                sum(row.tool_calls for row in rows),
                sum(row.tool_errors for row in rows),
                _sum_known(row.tokens_in for row in rows),
                _sum_known(row.tokens_out for row in rows),
                _sum_known_cost(row.cost_usd for row in rows),
                _sum_known(row.wall_time_ms for row in rows),
                sum(row.files_touched for row in rows),
            )
        )
    return tuple(summaries), tuple(grouped)


def _sum_known(values: Iterable[int | None]) -> int | None:
    materialized = list(values)
    if any(value is None for value in materialized):
        return None
    return sum(value for value in materialized if value is not None)


def _sum_known_cost(values: Iterable[float | None]) -> float | None:
    materialized = list(values)
    if any(value is None for value in materialized):
        return None
    return sum(value for value in materialized if value is not None)
