"""Credential transport tests for the LiteLLM translation path.

These call the pinned LiteLLM library with a fake HTTP transport, so no provider is contacted.
They prove the redirect boundary holds for the real translation path and that a successful call
still reaches the configured host with the provider credential.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from arena.catalog.config import load_catalog
from arena.gateway.litellm_adapter import LiteLLMAdapter
from arena.gateway.relay import PreparedRelay
from arena.gateway.resolve import resolve_target
from arena.gateway.server import DispatchContext
from arena.gateway.transport import HttpTransport
from arena.providers.config import load_providers

PROVIDERS_YAML = (
    "providers:\n"
    "  - id: anthropic\n"
    "    apis: {anthropic: https://api.anthropic.test}\n"
    "    keys: [{id: main, env: ANTHROPIC_API_KEY}]\n"
    "  - id: openai\n"
    "    apis: {chat: https://api.openai.test/v1}\n"
    "    keys: [{id: main, env: OPENAI_API_KEY}]\n"
)
CATALOG_YAML = (
    "price_version: test\n"
    "models:\n"
    "  - ref: anthropic/claude-x\n"
    "    protocols: [anthropic]\n"
    "    price_per_mtok: {in: 1, out: 2}\n"
    "  - ref: openai/gpt-x\n"
    "    protocols: [chat]\n"
    "    price_per_mtok: {in: 1, out: 2}\n"
)

ANTHROPIC_REPLY = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-x",
    "content": [{"type": "text", "text": "hello"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 2, "output_tokens": 1},
}
OPENAI_REPLY = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-x",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
}


def _configs(tmp_path: Path) -> tuple[Any, Any]:
    (tmp_path / "providers.yaml").write_text(PROVIDERS_YAML, encoding="utf-8")
    (tmp_path / "models.yaml").write_text(CATALOG_YAML, encoding="utf-8")
    return (
        load_providers(tmp_path / "providers.yaml"),
        load_catalog(tmp_path / "models.yaml"),
    )


def _context(
    providers: Any, catalog: Any, ref: str, *, target_protocol: str, payload: dict[str, Any]
) -> DispatchContext:
    return DispatchContext(
        target=resolve_target(ref, providers, catalog),
        prepared=PreparedRelay(b"{}", payload, True, None),
        source_protocol="chat",
        target_protocol=target_protocol,
        streaming=False,
    )


def _fake_http(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[httpx.Request]:
    """Route every real httpx client LiteLLM builds through a fake transport."""
    seen: list[httpx.Request] = []
    original = httpx.AsyncClient.__init__

    def init(self: httpx.AsyncClient, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(handler)
        original(self, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)
    return seen


async def _drain(body: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in body]


def test_litellm_anthropic_call_does_not_follow_a_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-key")
    providers, catalog = _configs(tmp_path)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "api.anthropic.test", request.url.host
        return httpx.Response(
            302, headers={"location": "https://redirect.example.test/steal"}, json=ANTHROPIC_REPLY
        )

    _fake_http(monkeypatch, handler)
    context = _context(
        providers,
        catalog,
        "anthropic/claude-x",
        target_protocol="anthropic",
        payload={"model": "anthropic/claude-x", "messages": [{"role": "user", "content": "hi"}]},
    )
    try:
        asyncio.run(LiteLLMAdapter().dispatch(context))
    except Exception as error:  # any failure is acceptable; the host is the point
        assert getattr(error, "status_code", None) in {302, 500}

    # The credential reaches the configured host once and never the redirect target.
    assert [request.url.host for request in seen] == ["api.anthropic.test"]


def test_litellm_openai_chat_call_does_not_follow_a_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key")
    providers, catalog = _configs(tmp_path)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "api.openai.test", request.url.host
        return httpx.Response(
            302, headers={"location": "https://redirect.example.test/steal"}, json=OPENAI_REPLY
        )

    _fake_http(monkeypatch, handler)
    context = _context(
        providers,
        catalog,
        "openai/gpt-x",
        target_protocol="chat",
        payload={"model": "openai/gpt-x", "messages": [{"role": "user", "content": "hi"}]},
    )
    try:
        asyncio.run(LiteLLMAdapter().dispatch(context))
    except Exception as error:  # any failure is acceptable; the host is the point
        assert getattr(error, "status_code", None) in {302, 500}

    # The credential reaches the configured host once and never the redirect target.
    assert [request.url.host for request in seen] == ["api.openai.test"]


SECOND_CHAT_PROVIDERS_YAML = (
    "providers:\n"
    "  - id: openai\n"
    "    apis: {chat: https://api.openai.test/v1}\n"
    "    keys: [{id: main, env: OPENAI_API_KEY}]\n"
    "  - id: other\n"
    "    apis: {chat: https://api.other.test/v1}\n"
    "    keys: [{id: main, env: OTHER_API_KEY}]\n"
)
SECOND_CHAT_CATALOG_YAML = (
    "price_version: test\n"
    "models:\n"
    "  - ref: openai/gpt-x\n"
    "    protocols: [chat]\n"
    "    price_per_mtok: {in: 1, out: 2}\n"
    "  - ref: other/other-x\n"
    "    protocols: [chat]\n"
    "    price_per_mtok: {in: 1, out: 2}\n"
)


def test_two_chat_providers_each_reach_their_own_host_with_their_own_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OTHER_API_KEY", "other-key")
    (tmp_path / "providers.yaml").write_text(SECOND_CHAT_PROVIDERS_YAML, encoding="utf-8")
    (tmp_path / "models.yaml").write_text(SECOND_CHAT_CATALOG_YAML, encoding="utf-8")
    providers = load_providers(tmp_path / "providers.yaml")
    catalog = load_catalog(tmp_path / "models.yaml")
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("authorization", "")))
        return httpx.Response(200, json=OPENAI_REPLY)

    _fake_http(monkeypatch, handler)
    adapter = LiteLLMAdapter()
    for ref in ("openai/gpt-x", "other/other-x"):
        context = _context(
            providers,
            catalog,
            ref,
            target_protocol="chat",
            payload={"model": ref, "messages": [{"role": "user", "content": "hi"}]},
        )
        response = asyncio.run(adapter.dispatch(context))
        assert response.status_code == 200

    assert seen == [
        ("api.openai.test", "Bearer openai-key"),
        ("api.other.test", "Bearer other-key"),
    ]


SHARED_HOST_PROVIDERS_YAML = (
    "providers:\n"
    "  - id: primary\n"
    "    apis: {chat: https://shared.gateway.test/v1}\n"
    "    keys: [{id: main, env: PRIMARY_API_KEY}]\n"
    "  - id: backup\n"
    "    apis: {chat: https://shared.gateway.test/v1}\n"
    "    keys: [{id: main, env: BACKUP_API_KEY}]\n"
)
SHARED_HOST_CATALOG_YAML = (
    "price_version: test\n"
    "models:\n"
    "  - ref: primary/primary-x\n"
    "    protocols: [chat]\n"
    "    price_per_mtok: {in: 1, out: 2}\n"
    "  - ref: backup/backup-x\n"
    "    protocols: [chat]\n"
    "    price_per_mtok: {in: 1, out: 2}\n"
)


def test_two_providers_on_one_host_do_not_share_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two providers on the same host with the same key id but different key values must not
    # share a client, or the second call would carry the first provider's credential.
    monkeypatch.setenv("PRIMARY_API_KEY", "primary-key")
    monkeypatch.setenv("BACKUP_API_KEY", "backup-key")
    (tmp_path / "providers.yaml").write_text(SHARED_HOST_PROVIDERS_YAML, encoding="utf-8")
    (tmp_path / "models.yaml").write_text(SHARED_HOST_CATALOG_YAML, encoding="utf-8")
    providers = load_providers(tmp_path / "providers.yaml")
    catalog = load_catalog(tmp_path / "models.yaml")
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("authorization", "")))
        return httpx.Response(200, json=OPENAI_REPLY)

    _fake_http(monkeypatch, handler)
    adapter = LiteLLMAdapter()
    for ref in ("primary/primary-x", "backup/backup-x"):
        context = _context(
            providers,
            catalog,
            ref,
            target_protocol="chat",
            payload={"model": ref, "messages": [{"role": "user", "content": "hi"}]},
        )
        response = asyncio.run(adapter.dispatch(context))
        assert response.status_code == 200

    assert seen == [
        ("shared.gateway.test", "Bearer primary-key"),
        ("shared.gateway.test", "Bearer backup-key"),
    ]


def test_litellm_anthropic_call_reaches_fake_http_and_returns_a_chat_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-key")
    providers, catalog = _configs(tmp_path)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "api.anthropic.test"
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "dummy-key"
        return httpx.Response(200, json=ANTHROPIC_REPLY)

    _fake_http(monkeypatch, handler)
    context = _context(
        providers,
        catalog,
        "anthropic/claude-x",
        target_protocol="anthropic",
        payload={"model": "anthropic/claude-x", "messages": [{"role": "user", "content": "hi"}]},
    )
    response = asyncio.run(LiteLLMAdapter().dispatch(context))

    assert response.status_code == 200
    body = json.loads(b"".join(asyncio.run(_drain(response.body))))
    assert body["choices"][0]["message"]["content"] == "hello"
    assert len(seen) == 1


def test_native_transport_returns_a_redirect_status_without_following(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://redirect.example.test/steal"})

    real_init = httpx.AsyncClient.__init__

    def init(self: httpx.AsyncClient, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)

    async def exercise() -> int:
        response = await HttpTransport().request(
            "https://api.anthropic.test/v1/messages",
            body=b"{}",
            headers={"x-api-key": "dummy-key"},
        )
        return response.status_code

    assert asyncio.run(exercise()) == 302
    assert len(seen) == 1
    assert seen[0].url.host == "api.anthropic.test"
    assert seen[0].headers["x-api-key"] == "dummy-key"
