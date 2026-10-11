"""Mock provider and cassette record/replay behavior."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from arena.gateway.cassettes import (
    CassetteHandler,
    CassetteMiss,
    CassetteRecorder,
    CassetteReplayer,
    CassetteResponse,
    normalize_request,
)
from arena.gateway.redact import SCRUBBED
from arena.providers.mock import MockProvider


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic", "gemini"])
def test_mock_provider_returns_protocol_shaped_deterministic_output(protocol: str) -> None:
    provider = MockProvider()
    request = {"model": "mock/test", "messages": [{"role": "user", "content": "hello"}]}

    first = provider.complete(protocol, request)
    second = provider.complete(protocol, request)

    assert first == second
    assert isinstance(first, dict)
    assert first.get("id", first.get("responseId")) == second.get("id", second.get("responseId"))
    if protocol == "chat":
        assert first["choices"][0]["message"]["content"] == "Mock response"
    elif protocol == "responses":
        assert first["output"][0]["content"][0]["text"] == "Mock response"
    elif protocol == "anthropic":
        assert first["content"][0]["text"] == "Mock response"
    else:
        assert first["candidates"][0]["content"]["parts"][0]["text"] == "Mock response"


def test_normalize_request_ignores_json_key_order() -> None:
    assert normalize_request({"model": "mock/test", "messages": []}) == normalize_request(
        {"messages": [], "model": "mock/test"}
    )


def test_recording_redacts_secret_fields_and_headers(tmp_path: Path) -> None:
    path = tmp_path / "secrets.jsonl"
    response = CassetteResponse(
        200,
        {"authorization": "Bearer top-secret-token", "content-type": "application/json"},
        {"api_key": "never-store-this", "text": "safe"},
    )
    request = {"model": "mock/test", "messages": [], "api_key": "never-store-this"}

    asyncio.run(CassetteRecorder(path).record("chat", request, response))
    entry = json.loads(path.read_text())

    assert entry["request"]["api_key"] == SCRUBBED
    assert entry["response"]["headers"]["authorization"] == SCRUBBED
    assert entry["response"]["body"]["api_key"] == SCRUBBED


def test_record_then_replay_uses_normalized_request(tmp_path: Path) -> None:
    cassette_path = tmp_path / "recording.jsonl"
    request = {"model": "mock/test", "messages": [{"role": "user", "content": "hello"}]}
    response = CassetteResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body={"answer": "recorded"},
    )
    recorder = CassetteRecorder(cassette_path)

    asyncio.run(recorder.record("chat", request, response))
    reordered_request = {"messages": request["messages"], "model": request["model"]}
    replayed = asyncio.run(CassetteReplayer(cassette_path).replay("chat", reordered_request))

    assert replayed == response
    assert json.loads(cassette_path.read_text()) == {
        "protocol": "chat",
        "request": request,
        "response": {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": {"answer": "recorded"},
        },
    }


def test_record_handler_records_normalized_response(tmp_path: Path) -> None:
    async def handler(protocol: str, request: Mapping[str, Any]) -> CassetteResponse:
        assert protocol == "chat"
        assert request == {"model": "mock/test"}
        return CassetteResponse(201, {"x-source": "mock"}, {"answer": "recorded"})

    wrapped = CassetteHandler("record", tmp_path / "record.jsonl", handler)
    result = asyncio.run(wrapped("chat", {"model": "mock/test"}))
    replayed = asyncio.run(
        CassetteReplayer(tmp_path / "record.jsonl").replay("chat", {"model": "mock/test"})
    )

    assert result == replayed
    assert result.status_code == 201


def test_replay_miss_raises_and_never_calls_live_handler(tmp_path: Path) -> None:
    live_calls = 0

    async def live_handler(protocol: str, request: Mapping[str, Any]) -> CassetteResponse:
        nonlocal live_calls
        live_calls += 1
        return CassetteResponse(200, {}, {"answer": "live"})

    replayer = CassetteReplayer(tmp_path / "empty.jsonl")
    with pytest.raises(CassetteMiss, match="no recorded cassette entry"):
        asyncio.run(replayer.dispatch("chat", {"model": "mock/test"}, live_handler))
    assert live_calls == 0


def test_replayer_matches_protocol_and_normalized_request(tmp_path: Path) -> None:
    path = tmp_path / "recording.jsonl"
    response = CassetteResponse(200, {}, {"answer": "right"})
    asyncio.run(CassetteRecorder(path).record("chat", {"model": "m"}, response))

    with pytest.raises(CassetteMiss):
        asyncio.run(CassetteReplayer(path).replay("responses", {"model": "m"}))
    with pytest.raises(CassetteMiss):
        asyncio.run(CassetteReplayer(path).replay("chat", {"model": "other"}))
