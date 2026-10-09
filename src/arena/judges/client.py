"""A small transport for judge model calls, shared by the pointwise and pairwise judges.

A judge sends one request and gets one reply. It carries the ``arena-judge-<run_id>`` token, so
the gateway attributes the call to purpose ``judge`` and the ledger records the spend apart
from contestant spend. A transport never retries and never switches model: a judge that
changed model partway would change the experiment.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Literal, Protocol, cast

from pydantic import Field

from arena.core.models import Frozen
from arena.scorers.base import ScorerError

JUDGE_PURPOSE = "judge"
MAX_REPLY_BYTES = 4 * 1024 * 1024

WireProtocol = Literal["openai", "anthropic"]


class JudgeError(ScorerError):
    """A judge could not produce a result. Scoring stops and no score is written."""


class JudgeTransportError(JudgeError):
    """The judge endpoint was unreachable, answered with an error, or sent a non-reply."""


class JudgeMessage(Frozen):
    role: Literal["system", "user"]
    content: str


class JudgeRequest(Frozen):
    model: str
    messages: tuple[JudgeMessage, ...]
    token: str
    purpose: Literal["judge"] = JUDGE_PURPOSE
    temperature: float = 0.0
    max_tokens: int = Field(default=2048, gt=0)


class JudgeReply(Frozen):
    text: str
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


class JudgeTransport(Protocol):
    """Send one judge request and return the reply text. Tests inject an in-process fake."""

    def complete(self, request: JudgeRequest) -> JudgeReply: ...


def judge_token(run_id: str) -> str:
    return f"arena-judge-{run_id}"


def judge_request(
    run_id: str,
    model: str,
    *,
    system: str,
    user: str,
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> JudgeRequest:
    """Build a judge request for a run: system and user messages plus the judge token."""
    return JudgeRequest(
        model=model,
        messages=(
            JudgeMessage(role="system", content=system),
            JudgeMessage(role="user", content=user),
        ),
        token=judge_token(run_id),
        temperature=temperature,
        max_tokens=max_tokens,
    )


class HttpJudgeTransport:
    """Call an OpenAI chat-completions or Anthropic messages endpoint with the bearer token.

    ``base_url`` is the gateway, or the mock provider before the gateway lands. The purpose is
    carried by the token, as the gateway attributes a call from it. Requests bypass any
    environment proxy, because the endpoint is a local one.
    """

    def __init__(self, base_url: str, *, protocol: WireProtocol, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.protocol = protocol
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def complete(self, request: JudgeRequest) -> JudgeReply:
        path, body = self._encode(request)
        http_request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {request.token}",
                "Content-Type": "application/json",
                **({"anthropic-version": "2023-06-01"} if self.protocol == "anthropic" else {}),
            },
        )
        raw = self._send(http_request)
        try:
            parsed: object = json.loads(raw)
        except ValueError:
            raise JudgeTransportError(f"judge endpoint reply is not JSON: {raw[:80]!r}") from None
        if not isinstance(parsed, dict):
            raise JudgeTransportError("judge endpoint reply is not a JSON object")
        payload = cast(dict[str, Any], parsed)
        return self._decode(payload)

    def _send(self, http_request: urllib.request.Request) -> bytes:
        try:
            with self._opener.open(http_request, timeout=self.timeout) as response:
                data: bytes = response.read(MAX_REPLY_BYTES + 1)
        except urllib.error.HTTPError as error:
            detail = error.read(200).decode("utf-8", "replace")
            raise JudgeTransportError(
                f"judge endpoint answered HTTP {error.code}: {detail}"
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise JudgeTransportError(
                f"cannot reach judge endpoint {self.base_url}: {error}"
            ) from error
        if len(data) > MAX_REPLY_BYTES:
            raise JudgeTransportError("judge endpoint reply is larger than 4 MiB")
        return data

    def _encode(self, request: JudgeRequest) -> tuple[str, dict[str, Any]]:
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        if self.protocol == "openai":
            return "/v1/chat/completions", {
                "model": request.model,
                "messages": messages,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
            }
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        return "/v1/messages", {
            "model": request.model,
            "system": system,
            "messages": [m for m in messages if m["role"] != "system"],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

    def _decode(self, payload: dict[str, Any]) -> JudgeReply:
        raw_usage = payload.get("usage")
        usage: dict[str, Any] = (
            cast(dict[str, Any], raw_usage) if isinstance(raw_usage, dict) else {}
        )
        model = payload.get("model")
        if self.protocol == "openai":
            choices_value = payload.get("choices")
            choices = cast(list[Any], choices_value) if isinstance(choices_value, list) else []
            if not choices:
                raise JudgeTransportError("judge endpoint reply has no completion")
            first_value: object = choices[0]
            if not isinstance(first_value, dict):
                raise JudgeTransportError("judge endpoint reply has no completion")
            first = cast(dict[str, Any], first_value)
            message_value = first.get("message")
            message = cast(dict[str, Any], message_value) if isinstance(message_value, dict) else {}
            text = message.get("content")
            tokens = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
        else:
            blocks_value = payload.get("content")
            blocks = cast(list[Any], blocks_value) if isinstance(blocks_value, list) else []
            parts: list[str] = []
            for item in blocks:
                if not isinstance(item, dict):
                    continue
                block = cast(dict[str, Any], item)
                text_part = block.get("text")
                if block.get("type") == "text" and isinstance(text_part, str):
                    parts.append(text_part)
            text = "".join(parts) if parts else None
            tokens = (usage.get("input_tokens"), usage.get("output_tokens"))
        if not isinstance(text, str):
            raise JudgeTransportError("judge endpoint reply has no text")
        return JudgeReply(
            text=text,
            model=model if isinstance(model, str) else None,
            tokens_in=tokens[0] if isinstance(tokens[0], int) else None,
            tokens_out=tokens[1] if isinstance(tokens[1], int) else None,
        )
