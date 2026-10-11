"""Seed an Arena data home with two contestants answering N tasks, for the browser vote test.

Usage: python -I tests/server/seed_vote_home.py <home> [tasks]
"""

from __future__ import annotations

import sys
from pathlib import Path

from arena.core.cas import ArtifactStore
from arena.core.store import Store


def seed(home: Path, tasks: int) -> None:
    cas = ArtifactStore(home / "artifacts")
    with Store(home / "arena.db") as store:
        store.execute("INSERT INTO runs (id, config_json, status) VALUES ('r', '{}', 'succeeded')")
        for task in range(1, tasks + 1):
            for contestant in ("alpha", "beta"):
                trial = f"{contestant}-{task}"
                store.execute(
                    "INSERT INTO trials (id, run_id, contestant_id, task_id, attempt, status) "
                    "VALUES (?, 'r', ?, ?, 1, 'succeeded')",
                    (trial, contestant, f"task-{task}"),
                )
                store.execute(
                    "INSERT INTO trial_artifacts(trial_id, path, sha256, mime, render_hint) "
                    "VALUES (?, 'answer.md', ?, 'text/markdown', 'text')",
                    (trial, cas.put(f"Answer {task} from side {len(contestant)}".encode())),
                )


if __name__ == "__main__":
    seed(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 20)
