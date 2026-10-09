"""Take secrets out of anything the gateway logs or stores.

A secret is replaced for good by `[REDACTED:KIND]`: nothing is kept to put it back. Two things
go, wherever they appear: a value that looks like a secret (an API key, a JWT, a private key,
the password in a URL, a bearer credential), and any value in a field or header *named* for a
secret (`Authorization`, `x-api-key`, `Cookie`, `access_token`), whatever it looks like.
Token counts (`max_tokens`) are not secrets.

The request path calls `scrub_json` on the body before it is logged or stored (DEV_PLAN §5.4
step 2); `RedactingFilter` does the same for every log record the gateway writes.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

SCRUBBED = "[REDACTED]"

_TOKEN_CHARS = "A-Za-z0-9_-"
# A key inside a longer word is no key.
_START = rf"(?<![{_TOKEN_CHARS}])"


@dataclass(frozen=True, slots=True)
class _Rule:
    kind: str
    pattern: re.Pattern[str]
    # The secret is this group of the match; 0 is the whole match.
    group: int = 0
    accept: Callable[[str], bool] | None = None


def _variable_reference(value: str) -> bool:
    """`${PASS}`, `<password>`, `****`: a stand-in for a value, not a value."""
    return value[:1] in "$%{<[*" or not value.strip("*xX.")


def _real_secret(value: str) -> bool:
    return not _variable_reference(value)


_QUOTE = r"""(?:\\?["'])?"""
_SECRET_NAME = r"password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential"

_RULES: tuple[_Rule, ...] = (
    _Rule(
        "PRIVATE_KEY",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----[\s\S]+?"
            r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
        ),
    ),
    _Rule(
        "API_KEY",
        re.compile(_START + rf"sk-(?:ant-|proj-|or-|svcacct-|admin-)?[{_TOKEN_CHARS}]{{20,}}"),
    ),
    _Rule(
        "API_KEY",
        re.compile(
            _START
            + r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"
            + r"|glpat-[A-Za-z0-9_-]{20,})"
        ),
    ),
    _Rule("API_KEY", re.compile(_START + r"AIza[0-9A-Za-z_-]{35}")),
    _Rule("API_KEY", re.compile(_START + r"xox[abposr]-[0-9A-Za-z-]{10,}")),
    _Rule("API_KEY", re.compile(_START + r"(?:sk|rk)_live_[0-9A-Za-z]{20,}")),
    _Rule("API_KEY", re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])")),
    _Rule(
        "API_KEY",
        re.compile(
            _START
            + r"(?:hf_[A-Za-z0-9]{30,}|gsk_[A-Za-z0-9]{40,}|xai-[A-Za-z0-9]{40,}"
            + r"|npm_[A-Za-z0-9]{36}|pypi-[A-Za-z0-9_-]{50,}|dop_v1_[a-f0-9]{64}"
            + r"|SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43})"
        ),
    ),
    _Rule(
        "TOKEN",
        re.compile(_START + r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ),
    # `Authorization: Bearer <credential>` written into a string.
    _Rule(
        "TOKEN",
        re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{16,})"),
        group=1,
        accept=_real_secret,
    ),
    # The password of user:password@host.
    _Rule(
        "PASSWORD",
        re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s:@/?#\"'<>]+:([^\s@/?#\"'<>]{3,})@"),
        group=1,
        accept=_real_secret,
    ),
    # password = ..., API_KEY: "...", as .env files and configs have them.
    _Rule(
        "SECRET",
        re.compile(
            rf"(?i)[A-Za-z0-9_.-]*(?:{_SECRET_NAME})[A-Za-z0-9_.-]*{_QUOTE}[ \t]*[:=][ \t]*"
            rf"{_QUOTE}([A-Za-z0-9_\-./+=~!@#%^&*]{{8,}})"
        ),
        group=1,
        accept=_real_secret,
    ),
)

# A field or header whose name says its value is a secret.
_SECRET_NAME_RE = re.compile(rf"(?i){_SECRET_NAME}|authori[sz]ation|cookie|^key$")
# `max_tokens`, `prompt_tokens_details`, `token_count`: counts of tokens, not tokens.
_TOKEN_COUNT_RE = re.compile(r"tokens|token_count|token_details|token_type")


def is_secret_name(name: str) -> bool:
    """True when a field or header called `name` holds a secret."""
    lowered = name.lower()
    return bool(_SECRET_NAME_RE.search(lowered)) and not _TOKEN_COUNT_RE.search(lowered)


def scrub(text: str) -> str:
    """`text` with every value that looks like a secret replaced by `[REDACTED:KIND]`."""
    for rule in _RULES:
        text = rule.pattern.sub(lambda match, rule=rule: _replace(match, rule), text)
    return text


def _replace(match: re.Match[str], rule: _Rule) -> str:
    secret = match.group(rule.group)
    if rule.accept is not None and not rule.accept(secret):
        return match.group(0)
    start = match.start(rule.group) - match.start()
    end = match.end(rule.group) - match.start()
    whole = match.group(0)
    return f"{whole[:start]}[REDACTED:{rule.kind}]{whole[end:]}"


def scrub_header(name: str, value: str) -> str:
    """A header value with its secrets taken out: all of it when the header is named for one
    or carries a credential (`Bearer`, `Basic`), else whatever `scrub` finds in it."""
    lowered = value.strip().lower()
    if is_secret_name(name) or lowered.startswith(("bearer ", "basic ")):
        return SCRUBBED
    return scrub(value)


def scrub_headers(headers: dict[str, str]) -> dict[str, str]:
    return {name: scrub_header(name, value) for name, value in headers.items()}


def scrub_json(body: bytes) -> bytes:
    """A request or reply body with its secrets taken out.

    JSON is walked string by string, and a field named for a secret goes whole. A stream's
    `data:` lines are each handled as JSON. Anything else is scrubbed as text. A `data:` URL
    (an inlined image) is left alone.
    """
    try:
        parsed = json.loads(body)
    except ValueError:
        return _scrub_lines(body)
    return json.dumps(_walk(parsed), ensure_ascii=False, separators=(",", ":")).encode()


def _scrub_lines(body: bytes) -> bytes:
    text = body.decode("utf-8", errors="replace")
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        ending = line[len(stripped) :]
        payload = stripped.removeprefix("data:").strip() if stripped.startswith("data:") else ""
        if payload:
            try:
                out.append(f"data: {json.dumps(_walk(json.loads(payload)), ensure_ascii=False)}")
                out[-1] += ending
                continue
            except ValueError:
                pass
        out.append(scrub(stripped) + ending)
    return "".join(out).encode()


def _walk(value: Any) -> Any:
    if isinstance(value, str):
        return value if value.startswith("data:") else scrub(value)
    if isinstance(value, list):
        return [_walk(item) for item in cast(list[Any], value)]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, child in cast(dict[str, Any], value).items():
            out[key] = SCRUBBED if is_secret_name(key) and child is not None else _walk(child)
        return out
    return value


class RedactingFilter(logging.Filter):
    """Scrubs a log record's message and traceback before any handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = scrub(record.exc_text)
        record.exc_info = None
        if record.stack_info:
            record.stack_info = scrub(record.stack_info)
        return True
