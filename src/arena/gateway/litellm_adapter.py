"""Concrete dispatchers: native passthrough, a LiteLLM translation path, and their composition.

Same-protocol calls and the mock provider go through `NativeDispatcher`, which sends the caller's
bytes unchanged. Cross-protocol calls go through `LiteLLMAdapter`, which uses LiteLLM only as the
protocol-translation library (DEV_PLAN §11): it accepts chat-completions requests and lets LiteLLM
translate them to, and the reply back from, the provider's native protocol. Any other protocol pair
fails closed. Neither path retries or switches model.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import replace
from importlib import import_module
from typing import Any, cast

from starlette.applications import Starlette

from arena.core.models import Tokens
from arena.gateway.endpoints import EndpointResolver, ProviderEndpointError
from arena.gateway.server import DispatchContext, UpstreamResponse, create_app
from arena.gateway.translate import TranslationError
from arena.gateway.transport import HttpTransport, TransportResponse
from arena.providers.mock import MockProvider
from arena.providers.subscriptions import FailureOutcome, Vendor, classify_response


class NativeDispatcher:
    """Dispatch one selected model through its native protocol without hidden fallback."""

    def __init__(
        self,
        *,
        endpoint_resolver: EndpointResolver | None = None,
        failure_sink: Callable[[FailureOutcome], None] | None = None,
    ) -> None:
        self.endpoint_resolver = endpoint_resolver or EndpointResolver()
        self.transport = HttpTransport()
        self.failure_sink = failure_sink

    async def dispatch(self, context: DispatchContext) -> UpstreamResponse:
        if context.target.provider.kind == "mock":
            return await self._mock(context)
        if context.source_protocol != context.target_protocol:
            raise ProviderEndpointError(
                "native dispatcher does not translate protocols; use the LiteLLM adapter"
            )
        endpoint = self.endpoint_resolver.resolve(context.target, protocol=context.target_protocol)
        request_body = (
            _streaming_body(context.prepared.body)
            if context.streaming and context.target_protocol in {"chat", "responses"}
            else context.prepared.body
        )
        response = await self.transport.request(
            _endpoint_url(endpoint.base_url, context.target_protocol),
            body=request_body,
            headers=endpoint.auth_headers
            or _provider_headers(context.target_protocol, endpoint.api_key),
        )
        status_code = response.status_code
        if 200 <= status_code < 300 and not context.streaming:
            payload = await _collect_json(response.body)
            usage = payload.get("usage")
            usage_map: Mapping[str, Any] = (
                cast(Mapping[str, Any], usage) if isinstance(usage, Mapping) else {}
            )
            input_tokens = usage_map.get("input_tokens")
            if input_tokens is None:
                input_tokens = usage_map.get("prompt_tokens", 0)
            output_tokens = usage_map.get("output_tokens")
            if output_tokens is None:
                output_tokens = usage_map.get("completion_tokens", 0)
            model_served = payload.get("model")
            return UpstreamResponse(
                status_code,
                response.headers,
                _bytes(json.dumps(payload, separators=(",", ":")).encode()),
                Tokens.model_validate({"in": input_tokens, "out": output_tokens}),
                model_served if isinstance(model_served, str) else context.target.catalog_ref,
            )
        if endpoint.account_id is not None and not 200 <= status_code < 300:
            return await self._subscription_failure(context, endpoint.account_id, response)
        return UpstreamResponse(status_code, response.headers, response.body)

    async def _subscription_failure(
        self, context: DispatchContext, account_id: str, response: TransportResponse
    ) -> UpstreamResponse:
        """Classify a failed subscription call; the caller still gets the vendor's reply."""
        subscription = context.target.provider.subscription
        assert subscription is not None
        body = b"".join([chunk async for chunk in response.body])
        outcome = classify_response(
            subscription.vendor, response.status_code, body, account_id=account_id
        )
        if outcome.disable_account:
            self._disable(subscription.vendor, outcome)
        if self.failure_sink is not None and outcome.kind is not None:
            self.failure_sink(outcome)
        return UpstreamResponse(response.status_code, response.headers, _bytes(body))

    def _disable(self, vendor: Vendor, outcome: FailureOutcome) -> None:
        store = self.endpoint_resolver.subscriptions
        if store is None:
            return
        account = store.load(vendor)
        store.save(
            replace(
                account,
                disabled=True,
                disabled_reason=outcome.kind,
            )
        )

    async def _mock(self, context: DispatchContext) -> UpstreamResponse:
        if context.source_protocol != context.target_protocol:
            raise ProviderEndpointError("mock provider does not translate protocols")
        payload = MockProvider().complete(context.target_protocol, context.prepared.payload)
        return UpstreamResponse(
            200,
            {"content-type": "application/json"},
            _bytes(json.dumps(payload, separators=(",", ":")).encode()),
            Tokens.model_validate({"in": 1, "out": 2}),
            context.target.catalog_ref,
        )

    @staticmethod
    def mock_dispatcher() -> Any:
        """Return the adapter dispatch callable for applications with mock providers."""
        return NativeDispatcher().dispatch


async def _collect_json(body: AsyncIterator[bytes]) -> dict[str, Any]:
    chunks = [chunk async for chunk in body]
    payload = json.loads(b"".join(chunks))
    if not isinstance(payload, dict):
        raise ValueError("provider returned a non-object JSON response")
    return cast(dict[str, Any], payload)


def _streaming_body(body: bytes) -> bytes:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("streaming request body must be a JSON object")
    payload = cast(dict[str, Any], value)
    payload["stream"] = True
    if "messages" in payload and isinstance(payload["messages"], list):
        payload["stream_options"] = {"include_usage": True}
    return json.dumps(payload, separators=(",", ":")).encode()


def _provider_headers(protocol: str, api_key: str) -> dict[str, str]:
    if protocol == "anthropic":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {api_key}"}


def _endpoint_url(base_url: str, protocol: str) -> str:
    if protocol == "anthropic":
        return base_url.rstrip("/") + "/v1/messages"
    if protocol in {"chat", "responses"}:
        suffix = "/chat/completions" if protocol == "chat" else "/responses"
        return base_url.rstrip("/") + suffix
    raise ProviderEndpointError(f"native HTTP transport does not support protocol {protocol!r}")


async def _bytes(value: bytes) -> AsyncIterator[bytes]:
    yield value


# LiteLLM provider prefix for each native protocol it can reach.
_LITELLM_PROVIDERS = {"anthropic": "anthropic", "chat": "openai"}

Completion = Callable[..., Awaitable[Any]]


def litellm_translator(
    source_protocol: str, target_protocol: str, payload: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Accept only requests `LiteLLMAdapter` can hand to LiteLLM; refuse every other pair.

    LiteLLM's input format is chat-completions, so the request is already in the form the
    adapter passes on and LiteLLM performs the provider-specific translation at dispatch.
    """
    if source_protocol != "chat":
        raise TranslationError(
            f"LiteLLM translation requires a chat request, not {source_protocol}"
        )
    if target_protocol not in _LITELLM_PROVIDERS:
        raise TranslationError(f"LiteLLM translation does not reach protocol {target_protocol}")
    if not isinstance(payload.get("messages"), list):
        raise TranslationError("chat request has no messages list")
    return dict(payload)


def litellm_structured_output_mapper(
    source_protocol: str, target_protocol: str, setting: Mapping[str, Any]
) -> Mapping[str, Any]:
    """LiteLLM carries `response_format` itself; other structured-output fields have no mapping."""
    del source_protocol, target_protocol
    return dict(setting) if set(setting) == {"response_format"} else {}


class LiteLLMAdapter:
    """Send a chat request to one provider through LiteLLM and return a chat reply.

    One attempt, no LiteLLM retries, no model fallback: a failure surfaces as the provider's
    status. Streaming replies are re-emitted as chat SSE; their token counts are not known when
    the response starts, so streamed calls report no tokens.
    """

    def __init__(
        self,
        *,
        endpoint_resolver: EndpointResolver | None = None,
        completion: Completion | None = None,
    ) -> None:
        self.endpoint_resolver = endpoint_resolver or EndpointResolver()
        self._completion = completion

    async def dispatch(self, context: DispatchContext) -> UpstreamResponse:
        if context.source_protocol != "chat":
            raise ProviderEndpointError("LiteLLM adapter only accepts chat requests")
        provider = _LITELLM_PROVIDERS.get(context.target_protocol)
        if provider is None:
            raise ProviderEndpointError(
                f"LiteLLM adapter does not reach protocol {context.target_protocol!r}"
            )
        endpoint = self.endpoint_resolver.resolve(context.target, protocol=context.target_protocol)
        if endpoint.auth_headers is not None:
            raise ProviderEndpointError(
                "LiteLLM adapter does not send subscription-signed requests"
            )
        arguments = dict(context.prepared.payload)
        arguments.update(
            model=f"{provider}/{context.target.ref.model}",
            api_base=endpoint.base_url,
            api_key=endpoint.api_key,
            num_retries=0,
            stream=context.streaming,
        )
        if context.streaming:
            arguments["stream_options"] = {"include_usage": True}
        completion = self._completion or _litellm_completion()
        try:
            result = await completion(**arguments)
        except Exception as error:
            status = getattr(error, "status_code", None)
            if not isinstance(status, int) or not 400 <= status <= 599:
                raise
            body = {"error": {"type": type(error).__name__, "message": str(error)}}
            return UpstreamResponse(
                status,
                {"content-type": "application/json"},
                _bytes(json.dumps(body, separators=(",", ":")).encode()),
            )
        if context.streaming:
            return UpstreamResponse(200, {"content-type": "text/event-stream"}, _chat_sse(result))
        reply = cast(dict[str, Any], result.model_dump(exclude_none=True))
        usage = cast(Mapping[str, Any], reply.get("usage") or {})
        served = reply.get("model")
        return UpstreamResponse(
            200,
            {"content-type": "application/json"},
            _bytes(json.dumps(reply, separators=(",", ":")).encode()),
            Tokens.model_validate(
                {
                    "in": usage.get("prompt_tokens", 0),
                    "out": usage.get("completion_tokens", 0),
                }
            ),
            served if isinstance(served, str) else context.target.catalog_ref,
        )


def _litellm_completion() -> Completion:
    # LiteLLM would otherwise fetch its model-cost map from the network at import time.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    module: Any = import_module("litellm")
    module.telemetry = False
    return cast(Completion, module.acompletion)


async def _chat_sse(stream: Any) -> AsyncIterator[bytes]:
    try:
        async for chunk in stream:
            payload = cast(dict[str, Any], chunk.model_dump(exclude_none=True))
            yield b"data: " + json.dumps(payload, separators=(",", ":")).encode() + b"\n\n"
        yield b"data: [DONE]\n\n"
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


class ProductionDispatcher:
    """Route each call: native bytes when protocols match or mock, LiteLLM otherwise."""

    def __init__(
        self,
        *,
        endpoint_resolver: EndpointResolver | None = None,
        native: NativeDispatcher | None = None,
        translating: LiteLLMAdapter | None = None,
    ) -> None:
        resolver = endpoint_resolver or EndpointResolver()
        self.native = native or NativeDispatcher(endpoint_resolver=resolver)
        self.translating = translating or LiteLLMAdapter(endpoint_resolver=resolver)

    async def dispatch(self, context: DispatchContext) -> UpstreamResponse:
        if (
            context.target.provider.kind == "mock"
            or context.source_protocol == context.target_protocol
        ):
            return await self.native.dispatch(context)
        return await self.translating.dispatch(context)


def create_production_app(
    providers: Any,
    catalog: Any,
    *,
    endpoint_resolver: EndpointResolver | None = None,
    completion: Completion | None = None,
    failure_sink: Callable[[FailureOutcome], None] | None = None,
    **options: Any,
) -> Starlette:
    """Build the gateway with the production dispatcher, LiteLLM translator and mapper."""
    resolver = endpoint_resolver or EndpointResolver()
    dispatcher = ProductionDispatcher(
        endpoint_resolver=resolver,
        native=NativeDispatcher(endpoint_resolver=resolver, failure_sink=failure_sink),
        translating=LiteLLMAdapter(endpoint_resolver=resolver, completion=completion),
    )
    options.setdefault("dispatcher", dispatcher.dispatch)
    options.setdefault("translator", litellm_translator)
    options.setdefault("structured_output_mapper", litellm_structured_output_mapper)
    return create_app(providers, catalog, **options)
