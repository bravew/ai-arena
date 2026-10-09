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
from arena.gateway.effort import EffortResult, apply_effort
from arena.gateway.translate import ProtocolTranslator, translate_request


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
        effort: EffortResult = apply_effort(
            request.payload,
            request.effort,
            mapping=request.effort_mapping,
            scaffold_carries_effort=request.scaffold_carries_effort,
        )
        if dict(effort) == dict(request.payload):
            return PreparedRelay(request.body, request.payload, False, effort.effort_applied)
        encoded = json.dumps(effort, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return PreparedRelay(encoded, effort, False, effort.effort_applied)

    translated_payload = translate_request(
        request.payload,
        source_protocol=request.source_protocol,
        target_protocol=request.target_protocol,
        translator=translator,
    )
    effort = apply_effort(
        translated_payload,
        request.effort,
        mapping=request.effort_mapping,
        scaffold_carries_effort=request.scaffold_carries_effort,
    )
    body = json.dumps(effort, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return PreparedRelay(body, effort, True, effort.effort_applied)


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
