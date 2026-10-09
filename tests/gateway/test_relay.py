"""Relay, protocol translation and effort mapping behavior."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

from arena.gateway.effort import apply_effort
from arena.gateway.relay import (
    RelayRequest,
    UpstreamAttemptsExhausted,
    prepare_relay,
    relay_stream,
)
from arena.gateway.translate import TranslationError, translate_request

RECORDED_REQUEST = (
    b'{"model":"anthropic/claude-opus-5-5","messages":[{"role":"user",'
    b'"content":[{"type":"text","text":"hi"},'
    b'{"type":"text","text":"cache boundary","cache_control":{"type":"ephemeral"}}]}]}'
)


def test_same_protocol_relay_preserves_recorded_request_bytes() -> None:
    request = RelayRequest(
        body=RECORDED_REQUEST,
        payload={"model": "anthropic/claude-opus-5-5", "messages": []},
        source_protocol="anthropic",
        target_protocol="anthropic",
    )
    prepared = prepare_relay(request, translator=_unexpected_translator)
    assert prepared.body == RECORDED_REQUEST
    assert prepared.translated is False
    assert prepared.effort_applied is None


def test_cross_protocol_translation_preserves_structured_output_and_records_translation() -> None:
    source = {
        "model": "anthropic/claude-opus-5-5",
        "messages": [{"role": "user", "content": "json please"}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer"}},
    }
    seen: list[tuple[str, str]] = []

    def translator(
        source_protocol: str, target_protocol: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        seen.append((source_protocol, target_protocol))
        translated = dict(payload)
        translated.pop("response_format")
        return {"model": translated["model"], "input": translated["messages"]}

    prepared = prepare_relay(
        RelayRequest(
            body=b"unused serialized request",
            payload=source,
            source_protocol="anthropic",
            target_protocol="responses",
        ),
        translator=translator,
    )
    assert seen == [("anthropic", "responses")]
    assert prepared.translated is True
    assert prepared.payload["response_format"] == source["response_format"]
    assert b'"response_format"' in prepared.body


def test_effort_is_applied_only_when_mapping_exists_and_scaffold_does_not_carry_it() -> None:
    payload = {"model": "example/model", "input": "hello"}
    result = apply_effort(
        payload,
        "high",
        mapping={"high": {"reasoning_effort": "high", "temperature": 0.2}},
    )
    assert result == {**payload, "reasoning_effort": "high", "temperature": 0.2}
    assert result.effort_applied == "high"

    carried = apply_effort(
        payload,
        "high",
        mapping={"high": {"reasoning_effort": "high"}},
        scaffold_carries_effort=True,
    )
    assert carried == payload
    assert carried.effort_applied is None

    unmapped = apply_effort(payload, "max", mapping={"high": {"reasoning_effort": "high"}})
    assert unmapped == payload
    assert unmapped.effort_applied is None


def test_translator_failure_has_gateway_translation_error() -> None:
    with pytest.raises(TranslationError, match="translation from chat to anthropic failed"):
        translate_request(
            {"model": "example/model"},
            source_protocol="chat",
            target_protocol="anthropic",
            translator=_failing_translator,
        )


def test_stream_retries_only_before_first_byte() -> None:
    calls = 0

    async def before_first_byte() -> AsyncIterator[bytes]:
        if False:
            yield b""
        raise ConnectionError("upstream disconnected before headers")

    async def succeeds() -> AsyncIterator[bytes]:
        yield b"first"
        yield b"second"

    async def attempt_before() -> AsyncIterator[bytes]:
        nonlocal calls
        calls += 1
        return await _stream(before_first_byte())

    async def attempt_after() -> AsyncIterator[bytes]:
        nonlocal calls
        calls += 1
        return await _stream(succeeds())

    async def collect() -> list[bytes]:
        return [chunk async for chunk in relay_stream((attempt_before, attempt_after))]

    assert asyncio.run(collect()) == [b"first", b"second"]
    assert calls == 2

    calls = 0

    async def failing_after_byte() -> AsyncIterator[bytes]:
        yield b"already sent"
        raise ConnectionError("upstream disconnected after first byte")

    async def attempt_stream_failure() -> AsyncIterator[bytes]:
        nonlocal calls
        calls += 1
        return await _stream(failing_after_byte())

    async def collect_failure() -> None:
        async for _ in relay_stream((attempt_stream_failure, attempt_after)):
            pass

    with pytest.raises(ConnectionError, match="after first byte"):
        asyncio.run(collect_failure())
    assert calls == 1


def test_all_pre_byte_failures_raise_exhausted() -> None:
    async def fails() -> AsyncIterator[bytes]:
        if False:
            yield b""
        raise TimeoutError("timed out before response")

    async def attempt() -> AsyncIterator[bytes]:
        return await _stream(fails())

    async def collect() -> None:
        async for _ in relay_stream((attempt, attempt)):
            pass

    with pytest.raises(UpstreamAttemptsExhausted):
        asyncio.run(collect())


async def _stream(stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    return stream


def _unexpected_translator(
    source_protocol: str, target_protocol: str, payload: Mapping[str, Any]
) -> Mapping[str, Any]:
    raise AssertionError("same-protocol relay must not invoke translation")


def _failing_translator(
    source_protocol: str, target_protocol: str, payload: Mapping[str, Any]
) -> Mapping[str, Any]:
    raise RuntimeError("adapter failure")
