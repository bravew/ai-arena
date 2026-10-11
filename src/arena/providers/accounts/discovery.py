"""Find vendor CLI sign-ins that already exist, read-only (CP8 decision D3).

Discovery lists what it finds by redacted id. It copies nothing and never touches the macOS
Keychain, which only an explicit `arena providers import claude --keychain` reads.
`ARENA_DISCOVER_SIGNINS=0` turns discovery off.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from arena.agents.local.signin import SignInError, claude_sign_in, codex_sign_in
from arena.agents.local.types import SignIn
from arena.providers.accounts.store import account_label

DISCOVERY_SWITCH = "ARENA_DISCOVER_SIGNINS"
_OFF = frozenset({"0", "false", "off", "no"})

# vendor -> the reader for the CLI's own sign-in file.
_READERS = {"claude": claude_sign_in, "chatgpt": codex_sign_in}


@dataclass(frozen=True)
class FoundSignIn:
    vendor: str
    account_id: str
    location: str


def discovery_enabled(environ: Mapping[str, str]) -> bool:
    return environ.get(DISCOVERY_SWITCH, "").strip().lower() not in _OFF


def read_existing(vendor: str, environ: Mapping[str, str]) -> SignIn:
    """The vendor's own sign-in file, read-only. Raises `SignInError` when there is none."""
    try:
        reader = _READERS[vendor]
    except KeyError as error:
        raise SignInError(f"no local CLI for vendor {vendor!r}") from error
    return reader(environ)


def _location(vendor: str, sign_in: SignIn, environ: Mapping[str, str]) -> str:
    relative = next(iter(sign_in.files))
    home = environ.get("HOME")
    if vendor == "chatgpt" and environ.get("CODEX_HOME"):
        return str(Path(environ["CODEX_HOME"]) / "auth.json").replace(home or "\0", "~")
    return f"~/{relative}"


def discover(environ: Mapping[str, str]) -> list[FoundSignIn]:
    if not discovery_enabled(environ):
        return []
    found: list[FoundSignIn] = []
    for vendor in _READERS:
        try:
            sign_in = read_existing(vendor, environ)
        except SignInError:
            continue
        found.append(
            FoundSignIn(
                vendor=vendor,
                account_id=account_label(vendor, dict(sign_in.files)),
                location=_location(vendor, sign_in, environ),
            )
        )
    return found
