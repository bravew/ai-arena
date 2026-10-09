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
        encoded = _merge_json_object(request.body, effort.added_parameters)
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


def _merge_json_object(body: bytes, added: Mapping[str, Any]) -> bytes:
    """Insert mapped parameters before the final object brace without reserializing it."""
    text = body.decode("utf-8")
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


UpstreamAttempt = Callable[[], Awaitable[AsyncIterator[bytes]]]


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
            return
        except Exception as exc:
            if sent_byte:
                raise
            last_error = exc
            continue
    message = "all upstream attempts failed before response bytes"
    raise UpstreamAttemptsExhausted(message) from last_error
