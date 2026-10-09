"""The small gateway server every gateway subsystem plugs into.

`create_app` builds a Starlette ASGI application for the four protocol surfaces, token auth,
Origin refusal, bounded compressed request bodies, catalog resolution, and `/v1/models`.
The actual upstream relay is owned by issue #17 and the remaining decision path by later issues;
protocol POSTs therefore return 501 after auth, body decoding and model resolution.

The app is intended for loopback and Docker-bridge interfaces only. It does not add CORS headers.
"""

from __future__ import annotations

import json
import logging
import zlib
from collections.abc import Awaitable, Callable
from typing import Any, cast

import zstandard
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from arena.catalog.config import ModelCatalog
from arena.gateway.auth import Caller, authenticate
from arena.gateway.redact import RedactingFilter, scrub_headers, scrub_json
from arena.gateway.resolve import ResolveError, listed_models, resolve_target
from arena.providers.config import ProviderConfig

DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_ENCODED_BYTES = 10 * 1024 * 1024
_SUPPORTED_ENCODINGS = {"identity", "gzip", "zstd", "x-gzip"}
logger = logging.getLogger("arena.gateway")
logger.addFilter(RedactingFilter())


class BodyTooLarge(Exception):
    """The request body exceeded its encoded or decoded byte budget."""


class InvalidBody(Exception):
    """The body encoding, compression stream, or JSON was invalid."""


class GatewayState:
    """Dependencies for one app instance, injectable for tests and later gateway issues."""

    def __init__(
        self,
        providers: ProviderConfig,
        catalog: ModelCatalog,
        *,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        max_encoded_bytes: int = DEFAULT_MAX_ENCODED_BYTES,
    ) -> None:
        if max_body_bytes < 1 or max_encoded_bytes < 1:
            raise ValueError("body limits must be positive")
        self.providers = providers
        self.catalog = catalog
        self.max_body_bytes = max_body_bytes
        self.max_encoded_bytes = max_encoded_bytes


class GatewayMiddleware:
    """Reject browser Origins and authenticate before every route, including loopback."""

    def __init__(
        self,
        app: Callable[
            [dict[str, Any], Callable[..., Awaitable[dict[str, Any]]], Callable[..., Any]],
            Awaitable[None],
        ],
    ) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[dict[str, Any]]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if headers.get("origin"):
            await JSONResponse(
                {"error": {"type": "forbidden", "message": "browser Origin requests are refused"}},
                status_code=403,
            )(scope, receive, send)
            return
        caller = authenticate(headers, Request(scope).query_params)
        if caller is None:
            await JSONResponse(
                {
                    "error": {
                        "type": "authentication_error",
                        "message": "missing or invalid arena token",
                    }
                },
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        scope.setdefault("state", {})["arena.caller"] = caller
        await self.app(scope, receive, send)


def create_app(
    providers: ProviderConfig,
    catalog: ModelCatalog,
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    max_encoded_bytes: int = DEFAULT_MAX_ENCODED_BYTES,
) -> Starlette:
    """Create a gateway app with validated provider and catalog configuration."""
    state = GatewayState(
        providers,
        catalog,
        max_body_bytes=max_body_bytes,
        max_encoded_bytes=max_encoded_bytes,
    )
    app = Starlette(
        routes=[
            Route("/v1/chat/completions", _protocol, methods=["POST"]),
            Route("/v1/responses", _protocol, methods=["POST"]),
            Route("/v1/messages", _protocol, methods=["POST"]),
            Route("/v1/messages/count_tokens", _protocol, methods=["POST"]),
            Route("/v1/models", _models, methods=["GET"]),
            Route("/v1beta/models/{model:path}:streamGenerateContent", _protocol, methods=["POST"]),
            Route("/v1beta/models/{model:path}:streamGenerateContent", _protocol, methods=["GET"]),
        ],
        middleware=[Middleware(GatewayMiddleware)],
    )
    app.state.gateway = state
    return app


def _state(request: Request) -> GatewayState:
    return cast(GatewayState, request.app.state.gateway)


def _caller(request: Request) -> Caller:
    return cast(Caller, request.scope["state"]["arena.caller"])


async def _models(request: Request) -> Response:
    models = [
        {"id": model.ref, "object": "model", "owned_by": model.ref.partition("/")[0]}
        for model in listed_models(_state(request).providers, _state(request).catalog)
    ]
    return JSONResponse({"object": "list", "data": models})


async def _protocol(request: Request) -> Response:
    state = _state(request)
    try:
        body = await read_body(request, state.max_body_bytes, state.max_encoded_bytes)
    except BodyTooLarge:
        return _error(
            413, "request_too_large", f"request body exceeds {state.max_body_bytes} decoded bytes"
        )
    except InvalidBody as exc:
        return _error(400, "invalid_request_error", str(exc))

    payload: Any = None
    if body:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error(400, "invalid_request_error", "request body must be valid JSON")
        if not isinstance(payload, dict):
            return _error(400, "invalid_request_error", "request body must be a JSON object")

    path_model = request.path_params.get("model")
    body_model = cast(dict[str, Any], payload).get("model") if isinstance(payload, dict) else None
    model_name: str | None = path_model or (body_model if isinstance(body_model, str) else None)
    if not isinstance(model_name, str) or not model_name:
        return _error(400, "invalid_request_error", "request must name a model")
    try:
        target = resolve_target(model_name, state.providers, state.catalog)
    except ResolveError as exc:
        return _error(404, exc.failure.value, exc.message)

    # Keep a scrubbed request attached for the relay/ledger issues. Never retain raw body bytes
    # in application state, and never include either body or caller token in an error/log.
    request.state.redacted_body = scrub_json(body)
    request.state.redacted_headers = scrub_headers(dict(request.headers))
    request.state.gateway_caller = _caller(request)
    request.state.gateway_target = target
    logger.debug("gateway request admitted: %s %s", request.method, request.url.path)
    return _error(501, "not_implemented", "protocol relay is not implemented yet")


async def read_body(request: Request, max_decoded: int, max_encoded: int) -> bytes:
    """Read, decompress and bound the request body before JSON parsing or logging.

    Both the encoded and decoded sizes are limited. Content-Length is checked early when it is
    usable, and the streamed bytes are counted too because that header is optional/untrusted.
    Gzip and zstd are decoded incrementally with an output budget to prevent decompression bombs.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_encoded:
                raise BodyTooLarge
        except ValueError:
            raise InvalidBody("invalid Content-Length") from None
    encoded = bytearray()
    async for chunk in request.stream():
        encoded.extend(chunk)
        if len(encoded) > max_encoded:
            raise BodyTooLarge

    encoding = request.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in _SUPPORTED_ENCODINGS:
        raise InvalidBody(f"unsupported Content-Encoding: {encoding}")
    if encoding == "identity":
        if len(encoded) > max_decoded:
            raise BodyTooLarge
        return bytes(encoded)
    if encoding in {"gzip", "x-gzip"}:
        return _decompress_gzip(bytes(encoded), max_decoded)
    try:
        decoded = zstandard.ZstdDecompressor().decompress(
            bytes(encoded), max_output_size=max_decoded + 1
        )
    except zstandard.ZstdError:
        raise InvalidBody("invalid zstd request body") from None
    if len(decoded) > max_decoded:
        raise BodyTooLarge
    return decoded


def _decompress_gzip(encoded: bytes, max_decoded: int) -> bytes:
    """Inflate exactly one complete gzip stream, without exceeding `max_decoded`."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        decoded = inflater.decompress(encoded, max_decoded + 1)
        if len(decoded) > max_decoded or inflater.unconsumed_tail:
            raise BodyTooLarge
        decoded += inflater.flush(max_decoded + 1 - len(decoded))
    except zlib.error:
        raise InvalidBody("invalid gzip request body") from None
    if len(decoded) > max_decoded:
        raise BodyTooLarge
    if not inflater.eof:
        raise InvalidBody("incomplete gzip request body")
    if inflater.unused_data:
        raise InvalidBody("trailing data after gzip request body")
    return decoded


def _error(status: int, kind: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"type": kind, "message": message}}, status_code=status)
