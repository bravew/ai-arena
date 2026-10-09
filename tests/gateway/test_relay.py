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


def test_same_protocol_effort_merge_preserves_existing_bytes_and_cache_markers() -> None:
    body = b'{ "model" : "example/model", "cache_control":{"type":"ephemeral"} }  '
    request = RelayRequest(
        body=body,
        payload={"model": "example/model", "cache_control": {"type": "ephemeral"}},
        source_protocol="anthropic",
        target_protocol="anthropic",
        effort="high",
        effort_mapping={"high": {"thinking": {"type": "enabled"}}},
    )
    prepared = prepare_relay(request, translator=_unexpected_translator)
    assert prepared.body == (
        b'{ "model" : "example/model", "cache_control":{"type":"ephemeral"} '
        b',"thinking":{"type":"enabled"}}  '
    )
    assert prepared.effort_applied == "high"


def test_same_protocol_already_mapped_effort_keeps_original_bytes() -> None:
    body = b'{ "model" : "example/model", "reasoning_effort" : "high" } '
    request = RelayRequest(
        body=body,
        payload={"model": "example/model", "reasoning_effort": "high"},
        source_protocol="responses",
        target_protocol="responses",
        effort="high",
        effort_mapping={"high": {"reasoning_effort": "high"}},
    )
    prepared = prepare_relay(request, translator=_unexpected_translator)
    assert prepared.body == body
    assert prepared.effort_applied == "high"


def test_existing_conflicting_effort_is_not_reported_as_applied() -> None:
    result = apply_effort(
        {"reasoning_effort": "low"},
        "high",
        mapping={"high": {"reasoning_effort": "high"}},
    )
    assert result.parameters == {"reasoning_effort": "low"}
    assert result.added_parameters == {}
    assert result.effort_applied is None


def test_cross_protocol_translation_requires_target_shaped_structured_output() -> None:
    source = {
        "model": "example/model",
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer"}},
    }

    def translator(
        source_protocol: str, target_protocol: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return {"model": payload["model"], "input": []}

    def mapper(
        source_protocol: str, target_protocol: str, constraints: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        assert source_protocol == "anthropic"
        assert target_protocol == "responses"
        assert constraints == {"response_format": source["response_format"]}
        return {"text": {"format": {"type": "json_schema", "name": "answer"}}}

    with pytest.raises(TranslationError, match="requires a structured_output_mapper"):
        translate_request(
            source,
            source_protocol="anthropic",
            target_protocol="responses",
            translator=translator,
        )

    translated = translate_request(
        source,
        source_protocol="anthropic",
        target_protocol="responses",
        translator=lambda source, target, payload: {
            "model": payload["model"],
            "text": {"format": {"type": "json_schema", "name": "answer"}},
        },
        structured_output_mapper=mapper,
    )
    assert "response_format" not in translated
    assert translated["text"] == {"format": {"type": "json_schema", "name": "answer"}}


def test_empty_or_incomplete_structured_output_mapping_is_rejected() -> None:
    source = {
        "model": "example/model",
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer"}},
        "structured_outputs": {"strict": True},
    }
    target = {"text": {"format": {"type": "json_schema", "name": "answer"}}}

    def translator(
        source_protocol: str, target_protocol: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return target

    with pytest.raises(TranslationError, match=r"response_format.*no target-protocol mapping"):
        translate_request(
            source,
            source_protocol="chat",
            target_protocol="responses",
            translator=translator,
            structured_output_mapper=lambda source, target, constraint: {},
        )

    with pytest.raises(TranslationError, match=r"structured_outputs.*no target-protocol mapping"):
        translate_request(
            source,
            source_protocol="chat",
            target_protocol="responses",
            translator=translator,
            structured_output_mapper=lambda source, target, constraint: (
                {"text": {"format": {"type": "json_schema", "name": "answer"}}}
                if "response_format" in constraint
                else {}
            ),
        )


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
