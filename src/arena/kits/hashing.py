"""Content identity for kits and their environment-variable references."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from arena.core.ids import canonical_json
from arena.core.models import Kit

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def normalize_env_references(value: Any) -> Any:
    """Replace environment references with their names, independent of resolved secrets."""
    if isinstance(value, str):
        return _ENV_REFERENCE.sub(lambda match: "${" + match.group(1) + "}", value)
    if isinstance(value, Mapping):
        entries = cast(Mapping[Any, Any], value)
        normalized: dict[str, Any] = {}
        for raw_key, item in entries.items():
            key = str(raw_key)
            normalized[key] = normalize_env_references(item)
        return normalized
    if isinstance(value, list):
        return [normalize_env_references(item) for item in cast(list[Any], value)]
    if isinstance(value, tuple):
        return [normalize_env_references(item) for item in cast(tuple[Any, ...], value)]
    return value


def _files(root: Path) -> list[tuple[str, bytes]]:
    try:
        paths = sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink())
        return [(path.relative_to(root).as_posix(), path.read_bytes()) for path in paths]
    except OSError as exc:
        raise ValueError(f"cannot read kit files under {root}: {exc}") from exc


def hash_kit(
    kit: Kit,
    root: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Hash every regular file plus canonical MCP/settings, ignoring environment values.

    `env` allows callers to pass already-resolved values; only the variable names
    in the config participate in identity.
    """
    del env  # values are intentionally irrelevant to kit identity
    digest = hashlib.sha256()
    for name, data in _files(root):
        name_bytes = name.encode("utf-8")
        digest.update(len(name_bytes).to_bytes(8, "big"))
        digest.update(name_bytes)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)

    config = {
        "mcp": normalize_env_references([server.model_dump(mode="json") for server in kit.mcp]),
        "settings": normalize_env_references(kit.settings),
    }
    config_bytes = canonical_json(config).encode("utf-8")
    digest.update(len(config_bytes).to_bytes(8, "big"))
    digest.update(config_bytes)
    return digest.hexdigest()
