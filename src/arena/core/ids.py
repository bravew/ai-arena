"""Canonical JSON and the content-hash ids built on it."""

from __future__ import annotations

import hashlib
import json
from typing import Any

ID_LENGTH = 12


def canonical_json(value: Any) -> str:
    """Same value, same text: sorted keys, no spaces, UTF-8 as is, no NaN or Infinity."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def sha256_hex(data: str | bytes) -> str:
    raw = data.encode() if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def content_id(value: Any, length: int = ID_LENGTH) -> str:
    """`sha256(canonical_json(value))[:length]`, the identity rule for contestants and kits."""
    return sha256_hex(canonical_json(value))[:length]
