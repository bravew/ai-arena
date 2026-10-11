"""Blind comparison pairs built from the text artifacts of stored trials."""

from __future__ import annotations

import hashlib
from itertools import combinations
from pathlib import Path

from arena.core.cas import ArtifactStore
from arena.core.store import Store
from arena.server.votes import Pair

_TEXT_MIMES = ("text/plain", "text/markdown")
_MAX_CHARS = 20_000
_MAX_PAIRS_PER_TASK = 200


def build_pairs(home: Path) -> list[Pair]:
    """Pair succeeded trials of different contestants that answered the same task in a run.

    Each trial is represented by its first text artifact (by path). Trials without readable text
    are skipped, so a home with no such artifacts yields no pairs rather than an error.
    """
    db = home / "arena.db"
    if not db.is_file():
        return []
    artifacts = ArtifactStore(home / "artifacts")
    placeholders = ", ".join("?" for _ in _TEXT_MIMES)
    with Store(db) as store:
        rows = store.execute(
            "SELECT t.id, t.run_id, t.task_id, t.contestant_id, a.sha256 FROM trials t "
            "JOIN trial_artifacts a ON a.trial_id = t.id "
            f"WHERE t.status = 'succeeded' AND a.mime IN ({placeholders}) "
            "ORDER BY t.run_id, t.task_id, t.id, a.path",
            _TEXT_MIMES,
        ).fetchall()
    first: dict[str, tuple[str, str, str, str]] = {}
    for row in rows:
        first.setdefault(
            str(row["id"]),
            (
                str(row["run_id"]),
                str(row["task_id"]),
                str(row["contestant_id"]),
                str(row["sha256"]),
            ),
        )
    groups: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for trial_id, (run_id, task_id, contestant, digest) in first.items():
        groups.setdefault((run_id, task_id), []).append((trial_id, contestant, digest))
    pairs: list[Pair] = []
    for (run_id, task_id), trials in sorted(groups.items()):
        taken = 0
        for (id_a, who_a, digest_a), (id_b, who_b, digest_b) in combinations(sorted(trials), 2):
            if who_a == who_b or taken >= _MAX_PAIRS_PER_TASK:
                continue
            try:
                text_a = artifacts.get(digest_a).decode("utf-8")
                text_b = artifacts.get(digest_b).decode("utf-8")
            except (OSError, ValueError):
                continue
            pair_id = hashlib.sha256(f"{id_a}\0{id_b}".encode()).hexdigest()[:16]
            pairs.append(
                Pair(
                    pair_id=pair_id,
                    task_id=f"{run_id}/{task_id}",
                    trial_a_id=id_a,
                    contestant_a_id=who_a,
                    content_a=text_a[:_MAX_CHARS],
                    trial_b_id=id_b,
                    contestant_b_id=who_b,
                    content_b=text_b[:_MAX_CHARS],
                )
            )
            taken += 1
    return pairs
