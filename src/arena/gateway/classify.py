"""Narrow provider error classification (DEV_PLAN §5.5)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast


class FailureClass(StrEnum):
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    CREDIT = "credit"
    SUBSCRIPTION_LIMIT = "subscription_limit"
    AUTH = "auth"
    AUTH_REFRESH = "auth_refresh"
    MODEL_REFUSED = "model_refused"
    BAD_REPLY = "bad_reply"
    UPSTREAM = "upstream"
    TIMEOUT = "timeout"
    CONTENT_REFUSAL = "content_refusal"


@dataclass(frozen=True, slots=True)
class ReplyClassification:
    """Reply disposition; swapped is a trial flag, not a provider failure class."""

    failure: FailureClass | None = None
    swapped: bool = False


class SwappedModelError(ValueError):
    """A provider returned a different model when the provider requires an exact match."""


_QUOTA = re.compile(
    r"quota|usage.?limit|out of budget|budget (?:exceeded|exhausted)|"
    r"limit.{0,24}resets|(?:per|daily|weekly|monthly).{0,8}(?:day|week|month)|"
    r"套餐|用量|额度|限额已用完",
    re.I,
)
_CREDIT = re.compile(
    r"insufficient.? (?:balance|credit|fund)|balance|credit|billing|payment|余额不足|欠费|请充值",
    re.I,
)
_RATE = re.compile(
    r"rate.?limit|too many requests|per.?(?:second|sec|minute|min)\b|\b[rt]pm\b|限流|频率", re.I
)
_MODEL = re.compile(
    r"model.{0,80}(?:not (?:supported|accessible|available|found|enabled|allowed)|unsupported|"
    r"does ?n[o']t exist|unknown|invalid)|(?:no such|unknown|invalid|unsupported) model|"
    r"model_not_found|模型.{0,12}(?:不存在|不支持|无权|未开通)",
    re.I,
)
_AUTH_REFRESH = re.compile(
    r"oauth (?:session expired|token revoked|access token.*revoked)|"
    r"session (?:has )?expired|invalid_grant",
    re.I,
)
_REFUSAL_CODES = {"content_filter", "content_policy_violation", "bio_policy", "cyber_policy"}


def classify_failure(status: int, body: bytes | str) -> FailureClass | None:
    """Classify a recorded provider error body without conflating neighboring classes."""
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    message = _error_text(text)
    if status == 429:
        if "insufficient_quota" in message.casefold():
            return FailureClass.QUOTA
        if _CREDIT.search(message) and re.search(
            r"insufficient.? (?:balance|credit|fund)|余额不足|欠费|请充值", message, re.I
        ):
            return FailureClass.CREDIT
        if _QUOTA.search(message):
            return (
                FailureClass.SUBSCRIPTION_LIMIT
                if re.search(r"weekly|usage limit|plan limit|套餐", message, re.I)
                else FailureClass.QUOTA
            )
        return FailureClass.RATE_LIMIT
    if status in (402, 403) and _CREDIT.search(message):
        return FailureClass.CREDIT
    if status == 401:
        return FailureClass.AUTH_REFRESH if _AUTH_REFRESH.search(message) else FailureClass.AUTH
    if status == 403:
        return FailureClass.AUTH
    if (
        status in (400, 404, 422)
        and _MODEL.search(message)
        and not (_QUOTA.search(message) or _CREDIT.search(message))
    ):
        return FailureClass.MODEL_REFUSED
    if status in (500, 502, 503, 504):
        return FailureClass.UPSTREAM
    if any(code in message.casefold() for code in _REFUSAL_CODES):
        return FailureClass.CONTENT_REFUSAL
    return None


def classify_reply(
    status: int,
    content_type: str,
    body: bytes,
    *,
    model_asked: str | None = None,
    require_served_model: bool = False,
) -> ReplyClassification:
    """Reject non-API success bodies, flag a served-model mismatch, and preserve refusals."""
    if not 200 <= status < 300:
        return ReplyClassification()
    if "text/html" in content_type.lower() or not body.strip():
        return ReplyClassification(FailureClass.BAD_REPLY)
    if "json" not in content_type.lower():
        return ReplyClassification()
    try:
        value: Any = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ReplyClassification(FailureClass.BAD_REPLY)
    if not isinstance(value, dict):
        return ReplyClassification(FailureClass.BAD_REPLY)
    value_map = cast(dict[str, Any], value)
    if value_map.get("stop_reason") == "refusal" or _contains_refusal(value_map):
        return ReplyClassification(FailureClass.CONTENT_REFUSAL)
    served = value_map.get("model")
    swapped = bool(model_asked and isinstance(served, str) and served != model_asked)
    result = ReplyClassification(
        FailureClass.MODEL_REFUSED if swapped and require_served_model else None, swapped
    )
    return result


def enforce_served_model(result: ReplyClassification, *, require_served_model: bool) -> None:
    """Fail a call when an opted-in provider serves a different model."""
    if result.swapped and require_served_model:
        raise SwappedModelError("provider served a different model than requested")


def _error_text(text: str) -> str:
    try:
        value: Any = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(value, dict):
        value_map = cast(dict[str, Any], value)
        error: Any = value_map.get("error", value_map)
        if isinstance(error, dict):
            error_map = cast(dict[str, Any], error)
            return " ".join(
                str(error_map.get(key, "")) for key in ("code", "type", "message", "status")
            )
    return text


def _contains_refusal(value: Any) -> bool:
    if isinstance(value, dict):
        value_map = cast(dict[str, Any], value)
        if value_map.get("finish_reason") == "content_filter":
            return True
        return any(_contains_refusal(child) for child in value_map.values())
    if isinstance(value, list):
        return any(_contains_refusal(child) for child in cast(list[Any], value))
    return False
