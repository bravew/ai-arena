from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from arena.providers.credentials import (
    ApiCredentialStore,
    CredentialError,
    credential_environment,
    credential_status,
)


def test_store_normalizes_references_and_replaces_privately(tmp_path: Path) -> None:
    root = tmp_path / "arena" / "credentials"
    store = ApiCredentialStore(root)
    store.save("${TEST_KEY}", "dummy-first")
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "TEST_KEY").stat().st_mode) == 0o600
    store.save("TEST_KEY", "dummy-replacement")
    assert store.load("${TEST_KEY}") == "dummy-replacement"
    assert sorted(path.name for path in root.iterdir()) == ["TEST_KEY"]
    assert "dummy" not in repr(store)
    assert store.forget("${TEST_KEY}")
    assert not store.forget("TEST_KEY")
    assert store.load("TEST_KEY") is None


def test_environment_precedence_and_public_metadata(tmp_path: Path) -> None:
    store = ApiCredentialStore(tmp_path / "credentials")
    store.save("KEY", "dummy-local")
    store.save("UNSELECTED", "dummy-unselected")
    source = {"KEY": "dummy-environment", "PATH": "/dummy/bin"}
    assert credential_environment(tmp_path, source, ["${KEY}"]) == source
    assert credential_environment(tmp_path, {"KEY": ""}, ["${KEY}"]) == {"KEY": "dummy-local"}
    assert "UNSELECTED" not in credential_environment(tmp_path, {}, ["KEY"])
    assert credential_status(tmp_path, source, "${KEY}") == {
        "configured": True,
        "source": "environment",
    }
    assert credential_status(tmp_path, {"KEY": ""}, "KEY") == {
        "configured": True,
        "source": "arena",
    }
    assert credential_status(tmp_path, {}, "MISSING") == {"configured": False, "source": "missing"}
    assert source == {"KEY": "dummy-environment", "PATH": "/dummy/bin"}


def test_absent_store_read_and_forget_do_not_create_it(tmp_path: Path) -> None:
    store = ApiCredentialStore(tmp_path / "credentials")
    assert store.load("KEY") is None
    assert not store.forget("KEY")
    assert not store.root.exists()


@pytest.mark.parametrize("reference", ["../KEY", "${KEY", "Bearer ${KEY}", "dummy-key-value"])
def test_invalid_reference_is_rejected_without_echoing_input(
    tmp_path: Path, reference: str
) -> None:
    store = ApiCredentialStore(tmp_path / "credentials")
    for operation in (
        lambda: store.save(reference, "dummy-secret"),
        lambda: store.load(reference),
        lambda: store.forget(reference),
    ):
        with pytest.raises(CredentialError) as error:
            operation()
        assert reference not in str(error.value)
        assert "dummy-secret" not in str(error.value)
    assert not store.root.exists()


@pytest.mark.parametrize("location", ["parent", "root", "key"])
def test_symlinks_are_rejected_for_all_operations(tmp_path: Path, location: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / "KEY").write_text("dummy-outside", encoding="utf-8")
    os.chmod(outside / "KEY", 0o600)
    root = tmp_path / "arena" / "credentials"
    if location == "parent":
        (tmp_path / "arena").symlink_to(outside, target_is_directory=True)
    elif location == "root":
        root.parent.mkdir()
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir(parents=True, mode=0o700)
        (root / "KEY").symlink_to(outside / "KEY")
    store = ApiCredentialStore(root)
    for operation in (
        lambda: store.save("KEY", "dummy-new"),
        lambda: store.load("KEY"),
        lambda: store.forget("KEY"),
    ):
        with pytest.raises(CredentialError):
            operation()
    assert (outside / "KEY").read_text(encoding="utf-8") == "dummy-outside"


def test_failed_replace_leaves_old_value_and_removes_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ApiCredentialStore(tmp_path / "credentials")
    store.save("KEY", "dummy-old")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("dummy-secret-in-low-level-error")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(CredentialError) as error:
        store.save("KEY", "dummy-new")
    assert "dummy-secret" not in str(error.value)
    assert store.load("KEY") == "dummy-old"
    assert [path.name for path in store.root.iterdir()] == ["KEY"]


def test_corrupt_or_public_file_is_an_error_not_missing(tmp_path: Path) -> None:
    store = ApiCredentialStore(tmp_path / "credentials")
    store.save("KEY", "dummy")
    path = store.root / "KEY"
    path.write_bytes(b"\xff")
    with pytest.raises(CredentialError):
        credential_status(tmp_path, {}, "KEY")
    path.write_text("dummy", encoding="utf-8")
    os.chmod(path, 0o644)
    with pytest.raises(CredentialError):
        store.load("KEY")


def test_environment_value_does_not_open_local_store(tmp_path: Path) -> None:
    (tmp_path / "credentials").symlink_to(tmp_path / "absent")
    assert credential_environment(tmp_path, {"KEY": "dummy-environment"}, ["${KEY}"]) == {
        "KEY": "dummy-environment"
    }
    assert credential_status(tmp_path, {"KEY": "dummy-environment"}, "KEY")["configured"]
