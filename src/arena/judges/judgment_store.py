"""Persist pairwise `Judgment` rows in the ``judgments`` table."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any, Literal, cast, get_args

from arena.core.store import Store, StoreError
from arena.judges.pairwise import Judgment

_Position = Literal["a", "b", "tie"]
_POSITIONS: tuple[str, ...] = get_args(_Position)
_COLUMNS = (
    "trial_a_id, trial_b_id, task_id, judge_id, judge_version, winner, first_order_winner, "
    "swapped_order_winner, position_bias, rationale, evidence_json"
)


def save_judgments(store: Store, judgments: Sequence[Judgment]) -> None:
    """Upsert judgments in one transaction.

    A row for the same ``(trial A, trial B, task, judge id, judge version)`` is replaced, so
    saving twice leaves one row. Every judgment is checked before the first write; a bad one
    raises `StoreError` and nothing is written.
    """
    rows = [_to_row(store, judgment) for judgment in judgments]
    try:
        with store.transaction() as conn:
            for row in rows:
                conn.execute(
                    f"INSERT INTO judgments ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(trial_a_id, trial_b_id, task_id, judge_id, judge_version) "
                    "DO UPDATE SET winner=excluded.winner, "
                    "first_order_winner=excluded.first_order_winner, "
                    "swapped_order_winner=excluded.swapped_order_winner, "
                    "position_bias=excluded.position_bias, rationale=excluded.rationale, "
                    "evidence_json=excluded.evidence_json",
                    row,
                )
    except sqlite3.DatabaseError as exc:
        raise StoreError(f"cannot write judgments: {exc}") from exc


def get_judgment(
    store: Store,
    trial_a_id: str,
    trial_b_id: str,
    task_id: str,
    judge_id: str,
    judge_version: str,
) -> Judgment | None:
    """The stored judgment for this exact key, or None when there is none."""
    row = store.execute(
        f"SELECT {_COLUMNS} FROM judgments WHERE trial_a_id = ? AND trial_b_id = ? "
        "AND task_id = ? AND judge_id = ? AND judge_version = ?",
        (trial_a_id, trial_b_id, task_id, judge_id, judge_version),
    ).fetchone()
    return None if row is None else _from_row(row)


def list_judgments(store: Store, task_id: str) -> list[Judgment]:
    """Every stored judgment for a task, ordered by its key."""
    rows = store.execute(
        f"SELECT {_COLUMNS} FROM judgments WHERE task_id = ? "
        "ORDER BY trial_a_id, trial_b_id, judge_id, judge_version",
        (task_id,),
    ).fetchall()
    return [_from_row(row) for row in rows]


def _to_row(store: Store, judgment: Judgment) -> tuple[Any, ...]:
    evidence = judgment.evidence
    first = _verdict(evidence, "first_order")
    swapped = _verdict(evidence, "swapped_order")
    if judgment.trial_a_id == judgment.trial_b_id:
        raise StoreError(f"judgment compares trial {judgment.trial_a_id} with itself")
    found = {
        str(row["id"])
        for row in store.execute(
            "SELECT id FROM trials WHERE task_id = ? AND id IN (?, ?)",
            (judgment.task_id, judgment.trial_a_id, judgment.trial_b_id),
        )
    }
    for trial_id in (judgment.trial_a_id, judgment.trial_b_id):
        if trial_id not in found:
            raise StoreError(
                f"judgment names trial {trial_id} that is not a trial of task {judgment.task_id!r}"
            )
    return (
        judgment.trial_a_id,
        judgment.trial_b_id,
        judgment.task_id,
        judgment.judge_id,
        judgment.judge_version,
        judgment.winner,
        first,
        swapped,
        int(judgment.position_bias),
        judgment.rationale,
        json.dumps(evidence, sort_keys=True, separators=(",", ":")),
    )


def _verdict(evidence: dict[str, Any], key: str) -> str:
    entry = evidence.get(key)
    winner = cast(dict[str, Any], entry).get("winner") if isinstance(entry, dict) else None
    if winner not in _POSITIONS:
        raise StoreError(f"judgment evidence {key!r} has no 'a', 'b' or 'tie' winner")
    return cast(str, winner)


def _from_row(row: sqlite3.Row) -> Judgment:
    try:
        evidence: object = json.loads(row["evidence_json"])
        if not isinstance(evidence, dict):
            raise ValueError("evidence is not an object")
        return Judgment(
            trial_a_id=row["trial_a_id"],
            trial_b_id=row["trial_b_id"],
            task_id=row["task_id"],
            judge_id=row["judge_id"],
            judge_version=row["judge_version"],
            winner=row["winner"],
            rationale=row["rationale"],
            position_bias=bool(row["position_bias"]),
            evidence=cast(dict[str, Any], evidence),
        )
    except ValueError as exc:
        raise StoreError(
            f"unreadable judgment row ({row['trial_a_id']}, {row['trial_b_id']}, "
            f"{row['task_id']}, {row['judge_id']}@{row['judge_version']}): {exc}"
        ) from exc
