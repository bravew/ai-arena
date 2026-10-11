"""Gateway tokens: who is calling, and for what purpose.

Every request carries a token, loopback included. The token is how a call is attributed:

- `arena-<trial_id>`: a trial's own model calls;
- `arena-judge-<run_id>`: a judge scoring a run;
- `arena-ops`: the arena's own calls (`arena providers test`, doctor).

A trial id is therefore never `ops` and never starts with `judge-`; `token_for_trial` refuses
such ids so one token can't read as two callers.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

TOKEN_PREFIX = "arena-"
OPS_TOKEN = "arena-ops"
_JUDGE_PREFIX = "judge-"
_OPS_NAME = "ops"
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_BEARER = re.compile(r"bearer\s+(\S+)", re.IGNORECASE)

# Header names a client puts its key in: OpenAI-style, Anthropic-style, Gemini-style.
_KEY_HEADERS = ("x-api-key", "x-goog-api-key")


class Purpose(StrEnum):
    TRIAL = "trial"
    JUDGE = "judge"
    OPS = "ops"


@dataclass(frozen=True, slots=True)
class Caller:
    """Who a token names. `trial_id` is set for trials, `run_id` for judges."""

    purpose: Purpose
    trial_id: str | None = None
    run_id: str | None = None

    @property
    def token(self) -> str:
        match self.purpose:
            case Purpose.TRIAL:
                return f"{TOKEN_PREFIX}{self.trial_id}"
            case Purpose.JUDGE:
                return f"{TOKEN_PREFIX}{_JUDGE_PREFIX}{self.run_id}"
            case Purpose.OPS:
                return OPS_TOKEN

    @property
    def label(self) -> str:
        """A short name for logs: `trial:<id>`, `judge:<run>`, or `ops`."""
        match self.purpose:
            case Purpose.TRIAL:
                return f"trial:{self.trial_id}"
            case Purpose.JUDGE:
                return f"judge:{self.run_id}"
            case Purpose.OPS:
                return "ops"


def token_for_trial(trial_id: str) -> str:
    if (
        not _IDENTITY.fullmatch(trial_id)
        or trial_id == _OPS_NAME
        or trial_id.startswith(_JUDGE_PREFIX)
    ):
        raise ValueError(f"not a usable trial id: {trial_id!r}")
    return f"{TOKEN_PREFIX}{trial_id}"


def token_for_judge(run_id: str) -> str:
    if not _IDENTITY.fullmatch(run_id):
        raise ValueError(f"not a usable run id: {run_id!r}")
    return f"{TOKEN_PREFIX}{_JUDGE_PREFIX}{run_id}"


def parse_token(token: str) -> Caller | None:
    """The caller a token names, or None when it is not a well-formed arena token."""
    if token == OPS_TOKEN:
        return Caller(Purpose.OPS)
    name = token.removeprefix(TOKEN_PREFIX)
    if name == token:
        return None
    if name.startswith(_JUDGE_PREFIX):
        run_id = name.removeprefix(_JUDGE_PREFIX)
        return Caller(Purpose.JUDGE, run_id=run_id) if _IDENTITY.fullmatch(run_id) else None
    if name == _OPS_NAME or not _IDENTITY.fullmatch(name):
        return None
    return Caller(Purpose.TRIAL, trial_id=name)


def tokens_in_request(headers: Mapping[str, str], query: Mapping[str, str]) -> list[str]:
    """Every credential the request carries, in the order clients prefer to send them.

    `Authorization: Bearer`, then the key headers of the Anthropic and Gemini clients, then
    Gemini's `?key=`.
    """
    found: list[str] = []
    match = _BEARER.fullmatch(headers.get("authorization", "").strip())
    if match:
        found.append(match.group(1))
    for name in _KEY_HEADERS:
        value = headers.get(name, "").strip()
        if value:
            found.append(value)
    value = query.get("key", "").strip()
    if value:
        found.append(value)
    return found


def authenticate(headers: Mapping[str, str], query: Mapping[str, str]) -> Caller | None:
    """The caller of the first credential that is an arena token; None when there is none."""
    for token in tokens_in_request(headers, query):
        caller = parse_token(token)
        if caller is not None:
            return caller
    return None
