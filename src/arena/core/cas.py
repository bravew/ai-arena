"""Atomic content-addressed artifact storage."""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import suppress
from pathlib import Path


class ArtifactStore:
    """Store blobs at `artifacts/<sha256>`, deduplicating identical content."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, digest: str) -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("artifact digest must be a lowercase SHA-256 hex string")
        return self.root / digest

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        destination = self.path_for(digest)
        if destination.exists():
            return digest

        fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Hard-link publication is atomic and doesn't replace another writer's blob.
            with suppress(FileExistsError):
                os.link(temporary, destination)  # another writer may publish the same content
            return digest
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary)

    def get(self, digest: str) -> bytes:
        path = self.path_for(digest)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"artifact content does not match its digest: {digest}")
        return data

    def contains(self, digest: str) -> bool:
        return self.path_for(digest).is_file()
