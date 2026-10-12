"""Private API credentials, indexed only by normalized environment references."""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Generator, Iterable, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Literal, TypedDict

from arena.providers.config import ENV_REFERENCE


class CredentialError(ValueError):
    """Credential storage failed; messages contain no credential values."""


class CredentialStatus(TypedDict):
    configured: bool
    source: Literal["environment", "arena", "missing"]


def environment_name(reference: str) -> str:
    """Accept NAME or ${NAME}, without ever echoing rejected input."""
    name = reference[2:-1] if reference.startswith("${") and reference.endswith("}") else reference
    if not ENV_REFERENCE.fullmatch(name):
        raise CredentialError("credential reference must be an environment variable name")
    return name


class ApiCredentialStore:
    """Keep values in 0600 files under a 0700 directory, never in public metadata.

    Walk directories through file descriptors with O_NOFOLLOW, including ancestors,
    so reads, atomic replacements and removals cannot traverse a substituted link.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    @contextmanager
    def _directory(self, *, create: bool = False) -> Generator[int | None]:
        descriptor: int | None = None
        try:
            path = self.root.absolute()
            if ".." in path.parts or path == Path(path.anchor):
                raise CredentialError("invalid credential directory")
            descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for part in path.parts[1:]:
                if create:
                    with suppress(FileExistsError):
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                try:
                    child = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                    )
                except FileNotFoundError:
                    if create:
                        raise
                    os.close(descriptor)
                    descriptor = None
                    yield None
                    return
                os.close(descriptor)
                descriptor = child
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid():
                raise CredentialError("credential directory is not privately owned")
            if create:
                os.fchmod(descriptor, 0o700)
            elif stat.S_IMODE(info.st_mode) != 0o700:
                raise CredentialError("credential directory must have private permissions")
            yield descriptor
        except OSError:
            raise CredentialError("cannot access private credential store") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _check_file(directory: int, name: str) -> bool:
        try:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise CredentialError("credential file must be a private regular file")
        return True

    def save(self, reference: str, value: str) -> None:
        name = environment_name(reference)
        if not value or "\x00" in value:
            raise CredentialError("credential value must be nonempty and contain no NUL")
        try:
            content = value.encode("utf-8")
        except UnicodeError:
            raise CredentialError("credential value must be valid UTF-8") from None
        with self._directory(create=True) as directory:
            assert directory is not None
            self._check_file(directory, name)
            staging = f".credential-{secrets.token_hex(16)}"
            try:
                descriptor = os.open(
                    staging,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._check_file(directory, name)
                os.replace(staging, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(staging, dir_fd=directory)

    def load(self, reference: str) -> str | None:
        name = environment_name(reference)
        with self._directory() as directory:
            if directory is None or not self._check_file(directory, name):
                return None
            descriptor = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    raise CredentialError("credential file must be a private regular file")
                try:
                    value = stream.read().decode("utf-8")
                except UnicodeError:
                    raise CredentialError("credential file is not valid UTF-8") from None
            if not value or "\x00" in value:
                raise CredentialError("credential file has an invalid value")
            return value

    def forget(self, reference: str) -> bool:
        name = environment_name(reference)
        with self._directory() as directory:
            if directory is None or not self._check_file(directory, name):
                return False
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
            return True


def credential_environment(
    home: Path, environ: Mapping[str, str], references: Iterable[str]
) -> dict[str, str]:
    """Copy the environment and resolve only requested names; nonempty env wins."""
    resolved = dict(environ)
    store = ApiCredentialStore(home / "credentials")
    for reference in references:
        name = environment_name(reference)
        if not resolved.get(name):
            value = store.load(name)
            if value is not None:
                resolved[name] = value
    return resolved


def credential_status(home: Path, environ: Mapping[str, str], reference: str) -> CredentialStatus:
    """Public presence/source metadata, with no value, prefix or fingerprint."""
    name = environment_name(reference)
    if environ.get(name):
        return {"configured": True, "source": "environment"}
    if ApiCredentialStore(home / "credentials").load(name) is not None:
        return {"configured": True, "source": "arena"}
    return {"configured": False, "source": "missing"}
