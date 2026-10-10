from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from arena.core.store import Store, StoreError
from arena.judges.judgment_store import get_judgment, list_judgments, save_judgments
from arena.judges.pairwise import Judgment


def judgment(
    winner: Literal["trial_a", "trial_b", "tie"] = "trial_a", bias: bool = False
) -> Judgment:
    first, swapped = ("a", "b") if winner == "trial_a" else ("a", "a")
    if winner == "tie":
        first, swapped = "a", "a"
    return Judgment(
        trial_a_id="a",
        trial_b_id="b",
        task_id="task",
        judge_id="pairwise",
        judge_version="1",
        winner=winner,
        rationale="both orders considered",
        position_bias=bias,
        evidence={
            "first_order": {"winner": first, "rationale": "first"},
            "swapped_order": {"winner": swapped, "rationale": "swapped"},
            "position_bias": bias,
        },
    )  # type: ignore[arg-type]


def seed_trials(store: Store) -> None:
    store.execute("INSERT INTO runs(id, config_json, status) VALUES ('r', '{}', 'queued')")
    store.execute(
        "INSERT INTO trials(id, run_id, contestant_id, task_id, status) VALUES "
        "('a', 'r', 'ca', 'task', 'succeeded'), ('b', 'r', 'cb', 'task', 'succeeded')"
    )


def test_migration_applies_to_fresh_store_and_previous_version(tmp_path: Path) -> None:
    with Store(tmp_path / "fresh.db") as store:
        migrations = store.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row[0] for row in migrations] == [1, 2, 3]

    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    source = Path(__file__).parents[2] / "src/arena/core/migrations"
    for version in ("001_initial.sql", "002_trial_artifacts.sql"):
        shutil.copy(source / version, old_migrations / version)
    from arena.core import store as store_module

    original = store_module.MIGRATIONS
    store_module.MIGRATIONS = old_migrations
    previous_db = tmp_path / "previous.db"
    try:
        with Store(previous_db):
            pass
    finally:
        store_module.MIGRATIONS = original
    with Store(previous_db) as store:
        versions = store.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row[0] for row in versions] == [1, 2, 3]
        assert (
            store.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='judgments'"
            ).fetchone()
            is not None
        )


def test_judgment_writer_reader_upsert_and_round_trip(tmp_path: Path) -> None:
    with Store(tmp_path / "arena.db") as store:
        seed_trials(store)
        item = judgment()
        save_judgments(store, [item])
        save_judgments(store, [item])
        assert get_judgment(store, "a", "b", "task", "pairwise", "1") == item
        assert list_judgments(store, "task") == [item]
        assert store.execute("SELECT COUNT(*) FROM judgments").fetchone()[0] == 1
        revised = judgment("tie", True)
        save_judgments(store, [revised])
        assert get_judgment(store, "a", "b", "task", "pairwise", "1") == revised
        assert store.execute("SELECT COUNT(*) FROM judgments").fetchone()[0] == 1


def test_judgment_write_is_atomic_and_checks_trial_task(tmp_path: Path) -> None:
    with Store(tmp_path / "arena.db") as store:
        seed_trials(store)
        item = judgment()
        save_judgments(store, [item])
        invalid = replace(item, trial_b_id="missing")
        with pytest.raises(StoreError, match="not a trial"):
            save_judgments(store, [judgment("tie"), invalid])
        assert get_judgment(store, "a", "b", "task", "pairwise", "1") == item
