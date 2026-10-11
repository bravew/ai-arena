"""The arena's private copy of a vendor CLI sign-in, kept for `local-cli` trials.

Layout: `<root>/<vendor>/files/<relative path>` holds the sign-in bytes and
`<root>/<vendor>/meta.json` holds no secret. Directories are 0700 and files 0600. Nothing here
reads or writes the vendor CLI's own config.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from arena.agents.local.types import SignIn
from arena.providers.config import LOCAL_CLI_VENDORS


class AccountError(Exception):
    """An account could not be stored or found. The message never holds a token."""


@dataclass(frozen=True)
class StoredAccount:
    vendor: str
    account_id: str
    source: str
    saved_at: str


def redact_id(raw: str | bytes) -> str:
    """A short, stable label for an account. It is a hash, so it cannot be read back."""
    data = raw.encode() if isinstance(raw, str) else raw
    return hashlib.sha256(data).hexdigest()[:8]


def account_label(vendor: str, files: dict[str, bytes]) -> str:
    """The redacted id: the provider's account id when the sign-in file names one, else a
    hash of the sign-in itself (which changes when the token is refreshed)."""
    for content in files.values():
        try:
            data: Any = json.loads(content)
        except json.JSONDecodeError:
            continue
        tokens = cast(dict[str, Any], data).get("tokens") if isinstance(data, dict) else None
        account = (
            cast(dict[str, Any], tokens).get("account_id") if isinstance(tokens, dict) else None
        )
        if isinstance(account, str) and account:
            return redact_id(f"{vendor}:{account}")
    return redact_id(b"".join(content for _, content in sorted(files.items())))


class AccountStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _vendor_dir(self, vendor: str) -> Path:
        if vendor not in LOCAL_CLI_VENDORS:
            raise AccountError(f"unknown vendor {vendor!r}; use one of {sorted(LOCAL_CLI_VENDORS)}")
        return self.root / vendor

    def save(self, vendor: str, sign_in: SignIn, source: str) -> StoredAccount:
        target = self._vendor_dir(vendor)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        staging = Path(tempfile.mkdtemp(prefix=f".{vendor}-", dir=self.root))
        try:
            os.chmod(staging, 0o700)
            files = staging / "files"
            files.mkdir(mode=0o700)
            for relative, content in sign_in.files.items():
                path = PurePosixPath(relative)
                if path.is_absolute() or ".." in path.parts:
                    raise AccountError("sign-in file path leaves the account directory")
                destination = files / path
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
            account = StoredAccount(
                vendor=vendor,
                account_id=account_label(vendor, dict(sign_in.files)),
                source=source,
                saved_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            meta = staging / "meta.json"
            meta.write_text(json.dumps(account.__dict__, sort_keys=True), encoding="utf-8")
            os.chmod(meta, 0o600)
            if target.exists():
                shutil.rmtree(target)
            os.replace(staging, target)
            return account
        finally:
            with suppress(FileNotFoundError):
                shutil.rmtree(staging)

    def load(self, vendor: str) -> SignIn | None:
        files = self._vendor_dir(vendor) / "files"
        if not files.is_dir():
            return None
        return SignIn(
            {
                path.relative_to(files).as_posix(): path.read_bytes()
                for path in sorted(files.rglob("*"))
                if path.is_file() and not path.is_symlink()
            }
        )

    def get(self, vendor: str) -> StoredAccount | None:
        meta = self._vendor_dir(vendor) / "meta.json"
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            return StoredAccount(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def list(self) -> list[StoredAccount]:
        found = (self.get(vendor) for vendor in sorted(LOCAL_CLI_VENDORS))
        return [account for account in found if account is not None]

    def forget(self, vendor: str) -> bool:
        target = self._vendor_dir(vendor)
        if not target.exists():
            return False
        shutil.rmtree(target)
        return True
