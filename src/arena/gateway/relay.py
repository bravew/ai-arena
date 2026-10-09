"""Provider relay primitives for protocol endpoints.

Same-protocol relays preserve the caller's original bytes, including native cache
breakpoints. Retries are allowed only before the first response byte is yielded.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from arena.core.modelref import Effort
from arena.gateway.effort import apply_effort
from arena.gateway.translate import (
    ProtocolTranslator,
    StructuredOutputMapper,
    translate_request,
)


@dataclass(frozen=True, slots=True)
class RelayRequest:
    """Inputs needed to prepare one upstream request."""

    body: bytes
    payload: Mapping[str, Any]
    source_protocol: str
    target_protocol: str
    effort: Effort | None = None
    effort_mapping: Mapping[str, Mapping[str, Any]] | None = None
    scaffold_carries_effort: bool = False
    structured_output_mapper: StructuredOutputMapper | None = None


@dataclass(frozen=True, slots=True)
class PreparedRelay:
    """Request body and decision metadata ready for the upstream HTTP client."""

    body: bytes
    payload: Mapping[str, Any]
    translated: bool
    effort_applied: Effort | None


def prepare_relay(
    request: RelayRequest,
    *,
    translator: ProtocolTranslator,
) -> PreparedRelay:
    """Prepare passthrough or translated bytes and expose relay decision metadata."""
    if request.source_protocol == request.target_protocol:
        effort = apply_effort(
            request.payload,
            request.effort,
            mapping=request.effort_mapping,
            scaffold_carries_effort=request.scaffold_carries_effort,
        )
        if not effort.added_parameters:
            return PreparedRelay(request.body, request.payload, False, effort.effort_applied)
        encoded = _merge_json_object(request.body, request.payload, effort.added_parameters)
        return PreparedRelay(encoded, effort.parameters, False, effort.effort_applied)

    translated_payload = translate_request(
        request.payload,
        source_protocol=request.source_protocol,
        target_protocol=request.target_protocol,
        translator=translator,
        structured_output_mapper=request.structured_output_mapper,
    )
    effort = apply_effort(
        translated_payload,
        request.effort,
        mapping=request.effort_mapping,
        scaffold_carries_effort=request.scaffold_carries_effort,
    )
    body = json.dumps(effort.parameters, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return PreparedRelay(body, effort.parameters, True, effort.effort_applied)


def _merge_json_object(body: bytes, payload: Mapping[str, Any], added: Mapping[str, Any]) -> bytes:
    """Insert mapped parameters into matching JSON object bytes without reserializing."""
    text = body.decode("utf-8")
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("same-protocol effort mapping requires a JSON object body") from exc
    if not isinstance(decoded, dict) or decoded != payload:
        raise ValueError("same-protocol effort mapping body does not match decoded payload")
    brace = text.rfind("}")
    if brace < 0 or text[brace + 1 :].strip():
        raise ValueError("same-protocol effort mapping requires a JSON object body")
    prefix = text[:brace]
    separator = "" if prefix.rstrip().endswith("{") else ","
    suffix = text[brace:]
    pairs = (
        json.dumps(key, ensure_ascii=False)
        + ":"
        + json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        for key, value in added.items()
    )
    encoded = ",".join(pairs)
    return f"{prefix}{separator}{encoded}{suffix}".encode()


class UpstreamAttemptsExhausted(Exception):
    """All retryable upstream attempts failed before a response byte was sent."""


class BadUpstreamReply(Exception):
    """The upstream completed without sending any response bytes."""


UpstreamAttempt = Callable[[], Awaitable[AsyncIterator[bytes]]]
_RETRYABLE_UPSTREAM_ERRORS = (ConnectionError, TimeoutError, BadUpstreamReply)


async def relay_stream(
    attempts: tuple[UpstreamAttempt, ...],
) -> AsyncIterator[bytes]:
    """Yield upstream bytes, retrying failures only while no byte reached the caller."""
    if not attempts:
        raise UpstreamAttemptsExhausted("no upstream attempts were configured")

    last_error: Exception | None = None
    for attempt in attempts:
        sent_byte = False
        try:
            stream = await attempt()
            async for chunk in stream:
                if not chunk:
                    continue
                sent_byte = True
                yield chunk
            if sent_byte:
                return
            raise BadUpstreamReply("upstream completed before sending response bytes")
        except _RETRYABLE_UPSTREAM_ERRORS as exc:
            if sent_byte:
                raise
            last_error = exc
            continue
    message = "all upstream attempts failed before response bytes"
    raise UpstreamAttemptsExhausted(message) from last_error
