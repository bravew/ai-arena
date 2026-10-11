from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from arena.catalog.config import load_catalog
from arena.gateway.endpoints import EndpointResolver, ProviderEndpointError
from arena.gateway.litellm_adapter import (
    LiteLLMAdapter,
    NativeDispatcher,
    ProductionDispatcher,
    create_production_app,
    litellm_structured_output_mapper,
    litellm_translator,
)
from arena.gateway.relay import PreparedRelay
from arena.gateway.resolve import resolve_target
from arena.gateway.server import DispatchContext, create_app
from arena.gateway.translate import TranslationError
from arena.gateway.transport import HttpTransport
from arena.providers.config import load_providers
from arena.providers.subscriptions import SubscriptionStore, import_subscription

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = load_providers(ROOT / "providers.yaml")
CATALOG = load_catalog(ROOT / "catalog" / "models.yaml")


def test_endpoint_resolution_uses_candidate_key_and_never_logs_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-value")
    target = __import__("arena.gateway.resolve", fromlist=["resolve_target"]).resolve_target(
        "anthropic/claude-opus-5-5", PROVIDERS, CATALOG
    )
    other_candidate = type(target.provider.keys[0])(id="other", env="OTHER_API_KEY")
    with pytest.raises(ProviderEndpointError, match="does not belong"):
        EndpointResolver().resolve(target, protocol="anthropic", candidate=other_candidate)

    endpoint = EndpointResolver().resolve(
        target, protocol="anthropic", candidate=target.provider.keys[0]
    )

    assert endpoint.base_url == "https://api.anthropic.com"
    assert endpoint.key_id == "main"
    assert endpoint.api_key == "secret-value"
    assert "secret-value" not in repr(endpoint)


def test_endpoint_resolution_fails_closed_for_unconfigured_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    target = __import__("arena.gateway.resolve", fromlist=["resolve_target"]).resolve_target(
        "anthropic/claude-opus-5-5", PROVIDERS, CATALOG
    )

    with pytest.raises(ProviderEndpointError, match="credential is unavailable"):
        EndpointResolver().resolve(target, protocol="anthropic", candidate=target.provider.keys[0])


def test_litellm_adapter_dispatches_one_candidate_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    request_body = b'{"model":"anthropic/claude-opus-5-5","messages":[]}'
    target = __import__("arena.gateway.resolve", fromlist=["resolve_target"]).resolve_target(
        "anthropic/claude-opus-5-5", PROVIDERS, CATALOG
    )
    captured: list[dict[str, Any]] = []

    async def fake_request(url: str, *, body: bytes, headers: Any) -> Any:
        captured.append({"url": url, "body": body, "headers": dict(headers)})

        async def response_body() -> AsyncIterator[bytes]:
            yield (
                b'{"id":"id","model":"anthropic/claude-opus-5-5",'
                b'"usage":{"input_tokens":2,"output_tokens":1}}'
            )

        return await HttpTransport().from_response(
            200,
            {"content-type": "application/json"},
            response_body(),
        )

    adapter = NativeDispatcher()
    monkeypatch.setattr(adapter.transport, "request", fake_request)
    context = DispatchContext(
        target=target,
        prepared=__import__("arena.gateway.relay", fromlist=["PreparedRelay"]).PreparedRelay(
            b'{"model":"anthropic/claude-opus-5-5","messages":[]}',
            {"model": "anthropic/claude-opus-5-5", "messages": []},
            False,
            None,
        ),
        source_protocol="anthropic",
        target_protocol="anthropic",
        streaming=False,
    )

    response = asyncio.run(adapter.dispatch(context))

    assert response.status_code == 200
    assert captured == [
        {
            "url": "https://api.anthropic.com/v1/messages",
            "body": request_body,
            "headers": {
                "x-api-key": "test-secret",
                "anthropic-version": "2023-06-01",
            },
        }
    ]
    assert response.tokens is not None
    assert response.tokens.in_ == 2
    assert response.tokens.out == 1


def test_configured_mock_dispatch_runs_authenticated_gateway_path(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(
        "price_version: test\nmodels:\n"
        "  - ref: mock/test-model\n"
        "    protocols: [anthropic]\n"
        "    pricing: subscription\n",
        encoding="utf-8",
    )
    mock_catalog = load_catalog(catalog_path)
    app = create_app(PROVIDERS, mock_catalog, dispatcher=NativeDispatcher.mock_dispatcher())

    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer arena-trial-112"},
            json={"model": "mock/test-model", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 200
    assert response.json()["type"] == "message"
    assert response.json()["content"][0]["text"] == "Mock response"
    assert response.headers["content-type"].startswith("application/json")


def test_http_transport_preserves_upstream_stream_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def response_stream() -> AsyncIterator[bytes]:
        yield b'data: {"x":'
        yield b"1}\n\n"

    async def exercise() -> list[bytes]:
        transport = HttpTransport()
        result = await transport.from_response(
            200, {"content-type": "text/event-stream"}, response_stream()
        )
        return [chunk async for chunk in result.body]

    received = asyncio.run(exercise())
    assert received == [b'data: {"x":', b"1}\n\n"]


def test_http_transport_closes_response_and_client_after_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.closed = False
            self.headers = {"content-type": "application/octet-stream"}
            self.status_code = 200

        async def aiter_raw(self) -> AsyncIterator[bytes]:
            yield b"chunk"

        async def aclose(self) -> None:
            self.closed = True

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self.closed = False
            self.response = FakeStream()

        def build_request(self, *args: Any, **kwargs: Any) -> object:
            return object()

        async def send(self, request: object, *, stream: bool) -> Any:
            return self.response

        async def aclose(self) -> None:
            self.closed = True

    created: list[FakeClient] = []

    def client_factory(**kwargs: Any) -> FakeClient:
        client = FakeClient(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr("arena.gateway.transport.httpx.AsyncClient", client_factory)

    async def exercise() -> tuple[list[bytes], FakeClient]:
        response = await HttpTransport().request(
            "https://example.invalid/v1/messages",
            body=b"{}",
            headers={"x-api-key": "test"},
        )
        chunks = [chunk async for chunk in response.body]
        return chunks, created[0]

    chunks, client = asyncio.run(exercise())
    assert chunks == [b"chunk"]
    assert client.response.closed
    assert client.closed


def _chat_context(
    ref: str,
    *,
    target_protocol: str,
    streaming: bool = False,
    payload: dict[str, Any] | None = None,
) -> DispatchContext:
    from arena.gateway.resolve import resolve_target

    body = payload or {"model": ref, "messages": [{"role": "user", "content": "hi"}]}
    return DispatchContext(
        target=resolve_target(ref, PROVIDERS, CATALOG),
        prepared=PreparedRelay(b"{}", body, True, None),
        source_protocol="chat",
        target_protocol=target_protocol,
        streaming=streaming,
    )


class _Reply:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return self.data


def test_litellm_translator_accepts_only_chat_requests_and_refuses_other_pairs() -> None:
    payload = {"model": "m", "messages": []}
    assert litellm_translator("chat", "anthropic", payload) == payload
    for source, target in (
        ("anthropic", "chat"),
        ("responses", "anthropic"),
        ("chat", "responses"),
    ):
        with pytest.raises(TranslationError):
            litellm_translator(source, target, payload)
    with pytest.raises(TranslationError):
        litellm_translator("chat", "anthropic", {"model": "m"})
    assert litellm_structured_output_mapper("chat", "anthropic", {"response_format": 1}) == {
        "response_format": 1
    }
    assert litellm_structured_output_mapper("chat", "anthropic", {"json_schema": 1}) == {}


def test_litellm_adapter_calls_litellm_once_without_retries_or_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    calls: list[dict[str, Any]] = []

    async def completion(**arguments: Any) -> _Reply:
        calls.append(arguments)
        return _Reply(
            {"model": "claude-opus-5-5", "usage": {"prompt_tokens": 3, "completion_tokens": 4}}
        )

    adapter = LiteLLMAdapter(completion=completion)
    response = asyncio.run(
        adapter.dispatch(_chat_context("anthropic/claude-opus-5-5", target_protocol="anthropic"))
    )

    assert len(calls) == 1
    assert calls[0]["model"] == "anthropic/claude-opus-5-5"
    assert calls[0]["api_base"] == "https://api.anthropic.com"
    assert calls[0]["api_key"] == "test-secret"
    assert calls[0]["num_retries"] == 0
    assert calls[0]["stream"] is False
    assert response.status_code == 200
    assert response.tokens is not None
    assert (response.tokens.in_, response.tokens.out) == (3, 4)
    assert response.model_served == "claude-opus-5-5"


def test_litellm_adapter_streams_chat_sse_and_closes_the_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    closed: list[bool] = []

    class Stream:
        async def __aiter__(self) -> AsyncIterator[_Reply]:
            yield _Reply({"choices": [{"delta": {"content": "hi"}}]})

        async def aclose(self) -> None:
            closed.append(True)

    async def completion(**arguments: Any) -> Stream:
        assert arguments["stream"] is True
        assert arguments["stream_options"] == {"include_usage": True}
        return Stream()

    async def exercise() -> list[bytes]:
        context = _chat_context(
            "anthropic/claude-opus-5-5", target_protocol="anthropic", streaming=True
        )
        response = await LiteLLMAdapter(completion=completion).dispatch(context)
        return [chunk async for chunk in response.body]

    chunks = asyncio.run(exercise())
    assert chunks == [b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', b"data: [DONE]\n\n"]
    assert closed == [True]


def test_litellm_adapter_returns_provider_error_status_and_refuses_other_protocols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")

    class RateLimited(Exception):
        status_code = 429

    async def completion(**_: Any) -> _Reply:
        raise RateLimited("slow down")

    adapter = LiteLLMAdapter(completion=completion)
    response = asyncio.run(
        adapter.dispatch(_chat_context("anthropic/claude-opus-5-5", target_protocol="anthropic"))
    )
    assert response.status_code == 429

    anthropic_source = DispatchContext(
        target=_chat_context("anthropic/claude-opus-5-5", target_protocol="chat").target,
        prepared=PreparedRelay(b"{}", {}, True, None),
        source_protocol="anthropic",
        target_protocol="chat",
        streaming=False,
    )
    with pytest.raises(ProviderEndpointError, match="only accepts chat"):
        asyncio.run(adapter.dispatch(anthropic_source))


def test_production_dispatcher_routes_native_and_translated_calls() -> None:
    seen: list[str] = []

    class Native:
        async def dispatch(self, context: DispatchContext) -> Any:
            seen.append("native")

    class Translating:
        async def dispatch(self, context: DispatchContext) -> Any:
            seen.append("litellm")

    dispatcher = ProductionDispatcher(native=Native(), translating=Translating())  # type: ignore[arg-type]
    asyncio.run(
        dispatcher.dispatch(_chat_context("anthropic/claude-opus-5-5", target_protocol="chat"))
    )
    asyncio.run(
        dispatcher.dispatch(_chat_context("anthropic/claude-opus-5-5", target_protocol="anthropic"))
    )
    assert seen == ["native", "litellm"]


def test_production_app_routes_chat_to_litellm_and_native_to_passthrough_and_refuses_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    calls: list[dict[str, Any]] = []

    async def completion(**arguments: Any) -> _Reply:
        calls.append(arguments)
        return _Reply(
            {"model": "claude-opus-5-5", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        )

    app = create_production_app(PROVIDERS, CATALOG, completion=completion)
    headers = {"Authorization": "Bearer arena-trial-112"}
    with TestClient(app) as client:
        translated = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "anthropic/claude-opus-5-5", "messages": []},
        )
        refused = client.post(
            "/v1/responses",
            headers=headers,
            json={"model": "anthropic/claude-opus-5-5", "input": "hi"},
        )

    assert translated.status_code == 200
    assert translated.json()["model"] == "claude-opus-5-5"
    assert len(calls) == 1 and calls[0]["model"] == "anthropic/claude-opus-5-5"
    assert refused.status_code == 400
    assert refused.json()["error"]["type"] == "translation_error"
    assert len(calls) == 1


SUBSCRIPTION_PROVIDERS_YAML = (
    "providers:\n"
    "  - id: anthropic-max\n"
    "    apis: {anthropic: https://api.anthropic.com}\n"
    "    subscription: {vendor: claude, plan: max, import: own-sign-in}\n"
)
SUBSCRIPTION_CATALOG_YAML = (
    "price_version: test\nmodels:\n"
    "  - ref: anthropic-max/claude-opus-5-5\n"
    "    protocols: [anthropic]\n"
    "    pricing: subscription\n"
)
BEFORE_EXPIRY = datetime(2026, 1, 1, tzinfo=UTC)


def _subscription_setup(tmp_path: Path) -> tuple[Any, Any, SubscriptionStore]:
    (tmp_path / "providers.yaml").write_text(SUBSCRIPTION_PROVIDERS_YAML, encoding="utf-8")
    (tmp_path / "models.yaml").write_text(SUBSCRIPTION_CATALOG_YAML, encoding="utf-8")
    store = SubscriptionStore(tmp_path / "accounts")
    import_subscription(
        "claude", ROOT / "fixtures" / "subscriptions" / "claude.synthetic.json", store
    )
    return (
        load_providers(tmp_path / "providers.yaml"),
        load_catalog(tmp_path / "models.yaml"),
        store,
    )


def test_subscription_endpoint_signs_with_imported_account_and_hides_token(
    tmp_path: Path,
) -> None:
    providers, catalog, store = _subscription_setup(tmp_path)
    target = resolve_target("anthropic-max/claude-opus-5-5", providers, catalog)
    resolver = EndpointResolver(subscriptions=store, clock=lambda: BEFORE_EXPIRY)

    endpoint = resolver.resolve(target, protocol="anthropic")

    assert endpoint.auth_headers is not None
    assert endpoint.auth_headers["authorization"] == "Bearer synthetic-claude-access"
    assert endpoint.auth_headers["anthropic-version"] == "2023-06-01"
    assert endpoint.account_id is not None
    assert "synthetic-claude-access" not in repr(endpoint)
    with pytest.raises(ProviderEndpointError, match="does not belong"):
        resolver.resolve(target, protocol="anthropic", account_id="someone-else")


def test_subscription_endpoint_fails_closed_without_store_import_or_valid_token(
    tmp_path: Path,
) -> None:
    providers, catalog, store = _subscription_setup(tmp_path)
    target = resolve_target("anthropic-max/claude-opus-5-5", providers, catalog)

    with pytest.raises(ProviderEndpointError, match="not configured"):
        EndpointResolver().resolve(target, protocol="anthropic")
    empty = SubscriptionStore(tmp_path / "empty")
    with pytest.raises(ProviderEndpointError, match="not imported"):
        EndpointResolver(subscriptions=empty).resolve(target, protocol="anthropic")
    expired = EndpointResolver(subscriptions=store, clock=lambda: datetime(2030, 1, 1, tzinfo=UTC))
    with pytest.raises(ProviderEndpointError, match="expired"):
        expired.resolve(target, protocol="anthropic")


def _subscription_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    reply: bytes,
) -> tuple[Any, list[Any], list[dict[str, str]], SubscriptionStore]:
    providers, catalog, store = _subscription_setup(tmp_path)
    sent: list[dict[str, str]] = []
    outcomes: list[Any] = []

    async def fake_request(url: str, *, body: bytes, headers: Any) -> Any:
        sent.append(dict(headers))

        async def chunks() -> AsyncIterator[bytes]:
            yield reply

        return await HttpTransport().from_response(
            status, {"content-type": "application/json"}, chunks()
        )

    dispatcher = NativeDispatcher(
        endpoint_resolver=EndpointResolver(subscriptions=store, clock=lambda: BEFORE_EXPIRY),
        failure_sink=outcomes.append,
    )
    monkeypatch.setattr(dispatcher.transport, "request", fake_request)
    context = DispatchContext(
        target=resolve_target("anthropic-max/claude-opus-5-5", providers, catalog),
        prepared=PreparedRelay(b"{}", {}, False, None),
        source_protocol="anthropic",
        target_protocol="anthropic",
        streaming=False,
    )
    response = asyncio.run(dispatcher.dispatch(context))
    return response, outcomes, sent, store


def test_subscription_call_is_signed_and_limit_failure_is_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = b'{"error":{"type":"rate_limit_error","message":"weekly usage limit reached"}}'
    response, outcomes, sent, store = _subscription_dispatch(tmp_path, monkeypatch, 429, reply)

    assert sent[0]["authorization"] == "Bearer synthetic-claude-access"
    assert response.status_code == 429
    assert b"".join(asyncio.run(_drain(response.body))) == reply
    assert [item.kind for item in outcomes] == ["subscription_limit"]
    assert not store.load("claude").disabled


def test_subscription_auth_failure_disables_the_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, outcomes, _, store = _subscription_dispatch(
        tmp_path, monkeypatch, 401, b'{"error":{"message":"token revoked"}}'
    )

    assert [item.kind for item in outcomes] == ["auth_refresh"]
    assert store.load("claude").disabled


async def _drain(body: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in body]
