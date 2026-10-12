"""Revisioned, atomic edits of provider and catalog configuration."""

from __future__ import annotations

import fcntl
import hashlib
import io
import os
import re
import tempfile
import threading
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import yaml
from pydantic import ValidationError

from arena.catalog.config import CatalogModel, ModelCatalog
from arena.core.modelref import ModelRef
from arena.providers.config import (
    ConfigLoader,
    Provider,
    ProviderConfig,
    parse_yaml,
    reject_plaintext_secrets,
)

_LOCK = threading.RLock()
_STATE = threading.local()
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
PROTOCOLS = frozenset({"anthropic", "chat", "responses"})


def _held_paths() -> set[str]:
    held: set[str] | None = getattr(_STATE, "paths", None)
    if held is None:
        held = set()
        _STATE.paths = held
    return held


class ConfigEditError(ValueError):
    """A sanitized configuration error."""


class RevisionConflict(ConfigEditError):
    """The file changed since the editor loaded it."""


def revision_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def revision(path: Path) -> str:
    return revision_bytes(path.read_bytes())


@contextmanager
def config_lock(path: Path) -> Generator[None]:
    """Hold one configuration's lock, in the canonical path order.

    Locks are always taken sorted by resolved path, so two writers (or a writer and
    `config_snapshot`) cannot deadlock. Taking them out of order in one thread is a
    programming error and raises, rather than waiting on a lock that can never come.
    Re-entering a lock this thread already holds is allowed and does not re-lock.
    """
    lock_path = path.with_name(path.name + ".lock")
    key = str(path.resolve())
    held = _held_paths()
    if key in held:
        yield
        return
    if any(key < other for other in held):
        raise ConfigEditError("configuration locks must be taken in sorted path order")
    with _LOCK:
        if path.is_symlink() or lock_path.is_symlink():
            raise ConfigEditError("configuration paths must not be symbolic links")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        held.add(key)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            held.discard(key)
            os.close(fd)


@contextmanager
def config_locks(*paths: Path) -> Generator[None]:
    """Hold several configuration locks in the canonical order, for a multi-file edit."""
    ordered = sorted(dict.fromkeys(paths), key=lambda path: str(path.resolve()))
    with ExitStack() as stack:
        for path in ordered:
            stack.enter_context(config_lock(path))
        yield


@contextmanager
def config_snapshot(*paths: Path) -> Generator[dict[Path, bytes]]:
    """Read several configuration files under their locks as one consistent snapshot.

    Locks are taken in a stable path order so two readers cannot deadlock, and the
    returned bytes are the same bytes their revisions are derived from. Parse the
    result outside the lock; a slow read would otherwise block every writer.
    """
    ordered = sorted(dict.fromkeys(paths), key=lambda path: str(path.resolve()))
    with ExitStack() as stack:
        for path in ordered:
            stack.enter_context(config_lock(path))
        yield {path: path.read_bytes() for path in ordered}


def parse_bytes(data: bytes) -> Any:
    """Parse one YAML document from already-read bytes, keeping dates as strings."""
    value = yaml.load(io.BytesIO(data), Loader=ConfigLoader)
    if value is None:
        raise ConfigEditError("configuration is empty")
    return value


def _replace(path: Path, data: dict[str, Any]) -> None:
    contents = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def edit_config(
    path: Path,
    expected: str | None,
    change: Callable[[dict[str, Any]], None],
    validate: Callable[[dict[str, Any]], object],
) -> str:
    with config_lock(path):
        if expected is not None and revision(path) != expected:
            raise RevisionConflict("settings changed; reload before saving")
        raw = parse_yaml(path)
        if not isinstance(raw, dict):
            raise ConfigEditError("configuration must be an object")
        data = cast(dict[str, Any], raw)
        change(data)
        try:
            validate(data)
        except ValidationError as error:
            raise ConfigEditError("configuration fields are invalid") from error
        _replace(path, data)
        return revision(path)


def validate_provider(data: dict[str, Any]) -> Provider:
    if reject_plaintext_secrets(data, Path("providers.yaml")):
        raise ConfigEditError("secrets must use environment references")
    try:
        provider = Provider.model_validate(data)
    except ValidationError as error:
        raise ConfigEditError("provider fields or credential endpoint are invalid") from error
    if not _IDENTIFIER.fullmatch(provider.id):
        raise ConfigEditError("provider id must use letters, digits, dots, underscores or dashes")
    keys = [key.id for key in provider.keys]
    if len(keys) != len(set(keys)) or any(not _IDENTIFIER.fullmatch(key) for key in keys):
        raise ConfigEditError("key ids must be unique valid identifiers")
    extra = provider.model_extra or {}
    apis = extra.get("apis", {})
    if provider.subscription is None and provider.kind is None and not apis:
        raise ConfigEditError("an API provider needs at least one endpoint")
    if not isinstance(apis, dict) or any(
        key not in PROTOCOLS or not isinstance(value, str)
        for key, value in cast(dict[str, Any], apis).items()
    ):
        raise ConfigEditError("endpoints must use supported protocols")
    limits = extra.get("limits", {})
    if not isinstance(limits, dict):
        raise ConfigEditError("limits must be an object")
    concurrency = cast(dict[str, Any], limits).get("concurrency", 1)
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
        raise ConfigEditError("concurrency must be a positive integer")
    if provider.subscription is not None and provider.subscription.runtime != "local-cli":
        raise ConfigEditError("new subscription settings require the vendor's local CLI")
    return provider


def save_provider(path: Path, expected: str, value: dict[str, Any], *, create: bool) -> str:
    provider = validate_provider(value)

    def change(data: dict[str, Any]) -> None:
        rows = data.get("providers")
        if not isinstance(rows, list):
            raise ConfigEditError("provider list is invalid")
        rows = cast(list[dict[str, Any]], rows)
        found = next((i for i, row in enumerate(rows) if row.get("id") == provider.id), None)
        if create:
            if found is not None:
                raise ConfigEditError("provider id already exists")
            rows.append(value)
        else:
            if found is None:
                raise ConfigEditError("provider does not exist")
            previous = rows[found]
            rows[found] = {**previous, **value}
        if reject_plaintext_secrets(data, path):
            raise ConfigEditError("secrets must use environment references")

    return edit_config(path, expected, change, ProviderConfig.model_validate)


def validate_model(value: dict[str, Any], provider: Provider) -> CatalogModel:
    try:
        model = CatalogModel.model_validate(value)
        ref = ModelRef.parse(model.ref)
    except ValidationError as error:
        raise ConfigEditError("model fields or prices are invalid") from error
    if ref.provider != provider.id or ref.effort is not None:
        raise ConfigEditError("model ref must name this provider without an effort suffix")
    extra = model.model_extra or {}
    protocols = extra.get("protocols")
    if (
        not isinstance(protocols, list)
        or not protocols
        or any(
            not isinstance(item, str) or item not in PROTOCOLS
            for item in cast(list[Any], protocols)
        )
    ):
        raise ConfigEditError("model needs supported protocols")
    if provider.subscription is not None:
        if model.pricing != "subscription":
            raise ConfigEditError("subscription models need subscription pricing")
        supported = {"anthropic"} if provider.subscription.vendor == "claude" else {"responses"}
    else:
        if model.pricing == "subscription":
            raise ConfigEditError("API models need explicit prices")
        if model.price_per_mtok is None or not {"in", "out"} <= model.price_per_mtok.keys():
            raise ConfigEditError("API models need input and output prices")
        source = extra.get("source")
        if not isinstance(source, str) or urlsplit(source).scheme != "https":
            raise ConfigEditError("API prices need an HTTPS source URL")
        if not urlsplit(source).hostname or urlsplit(source).username is not None:
            raise ConfigEditError("API prices need an HTTPS source URL")
        supported = set((provider.model_extra or {}).get("apis", {}))
    if not set(cast(list[str], protocols)) <= supported:
        raise ConfigEditError("model protocol is not configured for this provider")
    if reject_plaintext_secrets(value, Path("models.yaml")):
        raise ConfigEditError("model metadata must not contain secrets")
    return model


def save_model(path: Path, expected: str, value: dict[str, Any], provider: Provider) -> str:
    model = validate_model(value, provider)

    def change(data: dict[str, Any]) -> None:
        rows = data.get("models")
        if not isinstance(rows, list):
            raise ConfigEditError("model list is invalid")
        rows = cast(list[dict[str, Any]], rows)
        found = next((i for i, row in enumerate(rows) if row.get("ref") == model.ref), None)
        if found is None:
            rows.append(value)
        else:
            rows[found] = {**rows[found], **value}
        data["price_version"] = datetime.now(UTC).isoformat()

    return edit_config(path, expected, change, ModelCatalog.model_validate)
