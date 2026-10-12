"""Locking and revision rules for provider/catalog edits. No network or model call."""

from __future__ import annotations

from pathlib import Path

from arena.providers.config_edit import (
    ConfigEditError,
    config_lock,
    config_locks,
    config_snapshot,
    revision,
    revision_bytes,
)

PROVIDERS = "providers:\n  - id: mock\n    kind: mock\n"
CATALOG = "price_version: test\nmodels:\n  - ref: mock/echo\n    price_per_mtok: {in: 0, out: 0}\n"


def _files(tmp_path: Path) -> tuple[Path, Path]:
    providers = tmp_path / "providers.yaml"
    providers.write_text(PROVIDERS, encoding="utf-8")
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(CATALOG, encoding="utf-8")
    return providers, catalog


def test_snapshot_bytes_are_the_bytes_its_revisions_describe(tmp_path: Path) -> None:
    providers, catalog = _files(tmp_path)
    with config_snapshot(providers, catalog) as snapshot:
        assert set(snapshot) == {providers, catalog}
        assert revision_bytes(snapshot[providers]) == revision(providers)
        assert revision_bytes(snapshot[catalog]) == revision(catalog)
        assert snapshot[providers] == providers.read_bytes()


def test_snapshot_locks_in_a_stable_order_regardless_of_argument_order(tmp_path: Path) -> None:
    providers, catalog = _files(tmp_path)
    # Both orders must succeed: the snapshot sorts by resolved path, so a second
    # snapshot cannot be waiting on a lock the first one already holds.
    with config_snapshot(providers, catalog), config_snapshot(catalog, providers) as inner:
        assert inner[providers] == PROVIDERS.encode()


def _out_of_order_error(outer: Path, inner: Path) -> str:
    """Take `outer`, then try `inner` inside it; return the guard's message, or ''."""
    with config_lock(outer):
        try:
            with config_lock(inner):
                pass
        except ConfigEditError as error:
            return str(error)
    return ""


def test_locks_taken_out_of_order_are_rejected_instead_of_deadlocking(tmp_path: Path) -> None:
    providers, catalog = _files(tmp_path)
    # Whichever path sorts first must be the outer lock; the other is a programming error.
    first, second = sorted((providers, catalog), key=lambda path: str(path.resolve()))
    assert "sorted path order" in _out_of_order_error(second, first)


def test_multi_path_locks_accept_any_order(tmp_path: Path) -> None:
    providers, catalog = _files(tmp_path)
    with config_locks(catalog, providers), config_snapshot(providers, catalog) as snapshot:
        assert snapshot[catalog] == CATALOG.encode()


def test_a_failed_lock_does_not_leave_the_order_guard_set(tmp_path: Path) -> None:
    providers, catalog = _files(tmp_path)
    first, second = sorted((providers, catalog), key=lambda path: str(path.resolve()))
    assert _out_of_order_error(second, first)
    # The rejected attempt must not have recorded `first` as held.
    with config_lock(first), config_lock(second):
        pass
