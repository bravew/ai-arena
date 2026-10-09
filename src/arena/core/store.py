"""SQLite WAL store with numbered, plain-SQL migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MIGRATIONS = Path(__file__).parent / "migrations"


class StoreError(RuntimeError):
    """The store exists but cannot be read or upgraded safely."""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        if existed:
            self._verify_existing()
        self._conn = self._connect()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.DatabaseError as exc:
            raise StoreError(f"cannot open store at {self.path}: {exc}") from exc

    def _verify_existing(self) -> None:
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as conn:
                result = conn.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                raise StoreError(f"store integrity check failed at {self.path}: {result}")
        except sqlite3.DatabaseError as exc:
            raise StoreError(f"cannot read store at {self.path}: {exc}") from exc

    def _migrate(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = {row[0] for row in self._conn.execute("SELECT version FROM schema_migrations")}
        for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(migration.name.split("_", 1)[0])
            if version in applied:
                continue
            script = migration.read_text(encoding="utf-8")
            try:
                self._conn.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + script
                    + "\nINSERT INTO schema_migrations(version, name) "
                    + f"VALUES ({version}, '{migration.stem}');\nCOMMIT;"
                )
            except sqlite3.DatabaseError as exc:
                self._conn.rollback()
                raise StoreError(f"migration {migration.name} failed: {exc}") from exc

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        try:
            return self._conn.execute(sql, parameters)
        except sqlite3.DatabaseError as exc:
            raise StoreError(f"store query failed: {exc}") from exc

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
