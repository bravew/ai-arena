from __future__ import annotations

from pathlib import Path

import pytest

from arena.core.cas import ArtifactStore
from arena.core.store import Store, StoreError


def test_store_uses_wal_and_applies_numbered_migration(tmp_path: Path) -> None:
    db = tmp_path / "arena.db"
    with Store(db) as store:
        assert store.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        migrations = store.execute("SELECT version, name FROM schema_migrations").fetchall()
        assert [(row[0], row[1]) for row in migrations] == [
            (1, "001_initial"),
            (2, "002_trial_artifacts"),
            (3, "003_judgments"),
        ]
        tables = {
            row[0] for row in store.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"runs", "trials", "calls", "scores", "trial_artifacts", "judgments"} <= tables


def test_store_reopens_without_reapplying_migrations(tmp_path: Path) -> None:
    db = tmp_path / "arena.db"
    with Store(db) as store:
        store.execute(
            "INSERT INTO runs(id, config_json, status) VALUES (?, ?, ?)", ("r", "{}", "queued")
        )
    with Store(db) as store:
        assert store.execute("SELECT id FROM runs").fetchone()[0] == "r"
        assert store.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3


def test_trial_artifacts_can_share_a_cas_digest(tmp_path: Path) -> None:
    with Store(tmp_path / "arena.db") as store:
        store.execute("INSERT INTO runs(id, config_json, status) VALUES ('r', '{}', 'queued')")
        store.execute(
            "INSERT INTO trials(id, run_id, contestant_id, task_id, status) "
            "VALUES ('t1', 'r', 'c1', 'task', 'succeeded'), "
            "('t2', 'r', 'c2', 'task', 'succeeded')"
        )
        digest = "a" * 64
        store.execute(
            "INSERT INTO trial_artifacts(trial_id, path, sha256, mime, render_hint) "
            "VALUES ('t1', 'answer.txt', ?, 'text/plain', 'code'), "
            "('t2', 'answer.txt', ?, 'text/plain', 'code')",
            (digest, digest),
        )

        rows = store.execute(
            "SELECT trial_id, path, sha256 FROM trial_artifacts ORDER BY trial_id"
        ).fetchall()

    assert [(row[0], row[1], row[2]) for row in rows] == [
        ("t1", "answer.txt", digest),
        ("t2", "answer.txt", digest),
    ]


def test_corrupt_store_raises_instead_of_looking_empty(tmp_path: Path) -> None:
    db = tmp_path / "arena.db"
    db.write_bytes(b"not an sqlite database")
    with pytest.raises(StoreError, match="cannot read store"):
        Store(db)


def test_store_transaction_rolls_back(tmp_path: Path) -> None:
    with Store(tmp_path / "arena.db") as store:
        with pytest.raises(RuntimeError), store.transaction() as conn:
            conn.execute("INSERT INTO runs(id, config_json, status) VALUES ('r', '{}', 'queued')")
            raise RuntimeError("abort")
        assert store.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_artifacts_are_atomic_deduplicated_and_content_addressed(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    one = store.put(b"same artifact")
    two = store.put(b"same artifact")
    assert one == two
    assert store.path_for(one).name == one
    assert list((tmp_path / "artifacts").iterdir()) == [store.path_for(one)]
    assert store.get(one) == b"same artifact"
    with pytest.raises(FileNotFoundError):
        store.get("0" * 64)


def test_artifact_read_detects_corruption(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    digest = store.put(b"expected")
    store.path_for(digest).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="does not match"):
        store.get(digest)
