"""The small gateway server every gateway subsystem plugs into.

`create_app` builds a Starlette ASGI application for the four protocol surfaces, token auth,
Origin refusal, bounded compressed request bodies, catalog resolution, and `/v1/models`.
The actual upstream relay is owned by issue #17 and the remaining decision path by later issues;
protocol POSTs therefore return 501 after auth, body decoding and model resolution.

The app is intended for loopback and Docker-bridge interfaces only. It does not add CORS headers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import zlib
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast

import zstandard
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from arena.catalog.config import ModelCatalog
from arena.core.models import Call, Tokens
from arena.gateway.auth import Caller, Purpose, authenticate
from arena.gateway.budget import Budget, BudgetExceeded
from arena.gateway.lanes import KeyLane, LanePermit, LanePool, LanePoolFull, LaneRejected
from arena.gateway.redact import RedactingFilter, scrub_headers, scrub_json
from arena.gateway.relay import PreparedRelay, RelayRequest, prepare_relay
from arena.gateway.resolve import ResolveError, Target, listed_models, resolve_target
from arena.gateway.status import LaneSnapshot, status_routes
from arena.gateway.translate import ProtocolTranslator, StructuredOutputMapper, TranslationError
from arena.obs.events import EventLog
from arena.obs.metrics import Metrics
from arena.obs.otel import OtlpExporter
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


@dataclass(frozen=True, slots=True)
class UpstreamResponse:
    """Response returned by an injected dispatcher; no provider credentials live here."""

    status_code: int
    headers: Mapping[str, str]
    body: AsyncIterator[bytes]
    tokens: Tokens | None = None
    model_served: str | None = None


@dataclass(frozen=True, slots=True)
class CallContext:
    """Validated identity and pricing inputs required for budget and ledger writes."""

    run_id: str
    account_id: str
    call_id: str
    seq: int
    cost_usd: float | None
    price_version: str | None = None


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """Immutable request data passed to the injected dispatcher, without caller headers."""

    target: Target
    prepared: PreparedRelay
    source_protocol: str
    target_protocol: str
    streaming: bool


Dispatcher = Callable[[DispatchContext], Awaitable[UpstreamResponse]]
CallContextProvider = Callable[[Target, Caller], CallContext | None]


class LanePoolView(Protocol):
    def for_key(self, key: str) -> KeyLane: ...


class LedgerWriter(Protocol):
    def record(self, call: Call) -> Call: ...


class GatewayState:
    """Dependencies for one app instance, injectable for tests and later gateway issues."""

    def __init__(
        self,
        providers: ProviderConfig,
        catalog: ModelCatalog,
        *,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        max_encoded_bytes: int = DEFAULT_MAX_ENCODED_BYTES,
        dispatcher: Dispatcher | None = None,
        context_provider: CallContextProvider | None = None,
        translator: ProtocolTranslator | None = None,
        structured_output_mapper: StructuredOutputMapper | None = None,
        lanes: LanePoolView | None = None,
        budget: Budget | None = None,
        ledger: LedgerWriter | None = None,
        events: EventLog | None = None,
        metrics: Metrics | None = None,
        exporter: OtlpExporter | None = None,
        effort_mapping: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        if max_body_bytes < 1 or max_encoded_bytes < 1:
            raise ValueError("body limits must be positive")
        self.providers = providers
        self.catalog = catalog
        self.max_body_bytes = max_body_bytes
        self.max_encoded_bytes = max_encoded_bytes
        if (budget is not None or ledger is not None) and context_provider is None:
            raise ValueError("budget or ledger requires a validated call context provider")
        if budget is not None and events is None:
            raise ValueError("budget requires an event log")
        self.dispatcher = dispatcher
        self.context_provider = context_provider
        self.translator = translator
        self.structured_output_mapper = structured_output_mapper
        self.lanes = lanes or LanePool(concurrency=8, queue_wait=120)
        self.known_lanes: dict[str, KeyLane] = {}
        self.budget = budget
        self.ledger = ledger
        self.events = events
        self.metrics = metrics or Metrics()
        self.exporter = exporter
        self.effort_mapping = effort_mapping
        self.sequence = 0
        self.sequence_lock = asyncio.Lock()

    async def next_sequence(self) -> int:
        async with self.sequence_lock:
            self.sequence += 1
            return self.sequence

    def lane_snapshots(self) -> dict[str, LaneSnapshot]:
        # LanePool does not yet expose iteration; retain only keys admitted by this server.
        return {
            key: LaneSnapshot(lane.in_flight, lane.queued, lane.concurrency)
            for key, lane in self.known_lanes.items()
        }


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
    dispatcher: Dispatcher | None = None,
    context_provider: CallContextProvider | None = None,
    translator: ProtocolTranslator | None = None,
    structured_output_mapper: StructuredOutputMapper | None = None,
    lanes: LanePoolView | None = None,
    budget: Budget | None = None,
    ledger: LedgerWriter | None = None,
    events: EventLog | None = None,
    metrics: Metrics | None = None,
    exporter: OtlpExporter | None = None,
    effort_mapping: Mapping[str, Mapping[str, Any]] | None = None,
) -> Starlette:
    """Create a gateway app with validated provider and catalog configuration."""
    state = GatewayState(
        providers,
        catalog,
        max_body_bytes=max_body_bytes,
        max_encoded_bytes=max_encoded_bytes,
        dispatcher=dispatcher,
        context_provider=context_provider,
        translator=translator,
        structured_output_mapper=structured_output_mapper,
        lanes=lanes,
        budget=budget,
        ledger=ledger,
        events=events,
        metrics=metrics,
        exporter=exporter,
        effort_mapping=effort_mapping,
    )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncGenerator[None, None]:
        if state.exporter is not None:
            state.exporter.start()
        try:
            yield
        finally:
            if state.exporter is not None:
                await asyncio.to_thread(state.exporter.close)

    app = Starlette(
        routes=[
            Route("/v1/chat/completions", _protocol, methods=["POST"]),
            Route("/v1/responses", _protocol, methods=["POST"]),
            Route("/v1/messages", _protocol, methods=["POST"]),
            Route("/v1/messages/count_tokens", _protocol, methods=["POST"]),
            Route("/v1/models", _models, methods=["GET"]),
            Route("/v1beta/models/{model:path}:streamGenerateContent", _protocol, methods=["POST"]),
            Route("/v1beta/models/{model:path}:streamGenerateContent", _protocol, methods=["GET"]),
            *status_routes(
                lanes=state.lane_snapshots,
                metrics=state.metrics,
                exporter=state.exporter,
            ),
        ],
        middleware=[Middleware(GatewayMiddleware)],
        lifespan=lifespan,
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
    started = time.monotonic()
    result_status = 500
    lane: KeyLane | None = None
    permit: LanePermit | None = None
    reservation = None
    call: Call | None = None
    stream_owns_lifecycle = False
    metrics_recorded = False

    def record_metrics(status_code: int) -> None:
        nonlocal metrics_recorded
        if metrics_recorded:
            return
        metrics_recorded = True
        state.metrics.inc("gateway_requests", status=str(status_code))
        state.metrics.observe(
            "gateway_request_duration_ms",
            (time.monotonic() - started) * 1000,
            status=str(status_code),
        )

    try:
        try:
            body = await read_body(request, state.max_body_bytes, state.max_encoded_bytes)
        except BodyTooLarge:
            result_status = 413
            return _error(
                result_status,
                "request_too_large",
                f"request body exceeds {state.max_body_bytes} decoded bytes",
            )
        except InvalidBody as exc:
            result_status = 400
            return _error(result_status, "invalid_request_error", str(exc))

        payload: Any = None
        if body:
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                result_status = 400
                return _error(
                    result_status, "invalid_request_error", "request body must be valid JSON"
                )
            if not isinstance(payload, dict):
                result_status = 400
                return _error(
                    result_status, "invalid_request_error", "request body must be a JSON object"
                )

        path_model = request.path_params.get("model")
        body_model = (
            cast(dict[str, Any], payload).get("model") if isinstance(payload, dict) else None
        )
        model_name = path_model or (body_model if isinstance(body_model, str) else None)
        if not isinstance(model_name, str) or not model_name:
            result_status = 400
            return _error(result_status, "invalid_request_error", "request must name a model")
        try:
            target = resolve_target(model_name, state.providers, state.catalog)
        except ResolveError as exc:
            result_status = 404
            return _error(result_status, exc.failure.value, exc.message)

        caller = _caller(request)
        request.state.redacted_body = scrub_json(body)
        request.state.redacted_headers = scrub_headers(dict(request.headers))
        request.state.gateway_caller = caller
        request.state.gateway_target = target
        if state.dispatcher is None:
            result_status = 501
            return _error(result_status, "not_implemented", "protocol relay is not configured")
        if state.translator is None:
            result_status = 503
            return _error(
                result_status, "relay_unavailable", "protocol translation is not configured"
            )

        context = (
            state.context_provider(target, caller) if state.context_provider is not None else None
        )
        if (state.budget is not None or state.ledger is not None) and context is None:
            result_status = 503
            return _error(
                result_status,
                "call_context_unavailable",
                "validated call identity and pricing context is required",
            )
        if state.budget is not None and state.events is None:
            result_status = 503
            return _error(
                result_status,
                "budget_context_unavailable",
                "budget service requires a run event log",
            )
        seq = context.seq if context is not None else await state.next_sequence()
        run_id = (
            context.run_id if context is not None else (caller.run_id or f"trial-{caller.trial_id}")
        )
        cost_usd = context.cost_usd if context is not None else None
        if state.budget is not None and context is not None and state.events is not None:
            call = _build_call(request, target, caller, context, seq, run_id, cost_usd, 0)
            try:
                reservation = await state.budget.check_call(
                    call, state.events, subscription=target.model.pricing == "subscription"
                )
            except BudgetExceeded:
                result_status = 402
                return _error(
                    result_status, "budget_exceeded", "call exceeds the configured run budget"
                )

        source_protocol = _request_protocol(request)
        target_protocol = _target_protocol(target)
        lane_key = f"{target.provider.id}/main"
        try:
            lane = state.lanes.for_key(lane_key)
            state.known_lanes[lane_key] = lane
        except LaneRejected as exc:
            result_status = 429
            return JSONResponse(
                {"error": {"type": "lane_rejected", "message": str(exc)}},
                status_code=429,
                headers=exc.headers,
            )
        except LanePoolFull:
            result_status = 503
            return _error(
                result_status,
                "lane_pool_full",
                "gateway lane pool is full; retry after capacity is freed",
            )

        async def keepalive() -> None:
            await asyncio.sleep(0)

        request_payload = cast(dict[str, Any], payload)
        streaming = bool(
            request_payload.get("stream")
        ) or "text/event-stream" in request.headers.get("accept", "")
        try:
            permit = await lane.acquire(streaming=streaming, keepalive=keepalive)
        except LaneRejected as exc:
            result_status = 429
            return JSONResponse(
                {"error": {"type": "lane_rejected", "message": str(exc)}},
                status_code=429,
                headers=exc.headers,
            )

        if state.budget is not None and reservation is None:
            result_status = 503
            return _error(
                result_status,
                "budget_context_unavailable",
                "budget service requires an event log and validated call context",
            )
        if state.ledger is not None and context is None:
            result_status = 503
            return _error(
                result_status,
                "ledger_context_unavailable",
                "ledger requires validated call identity and pricing context",
            )

        try:
            prepared = prepare_relay(
                RelayRequest(
                    body=body,
                    payload=cast(Mapping[str, Any], payload),
                    source_protocol=source_protocol,
                    target_protocol=target_protocol,
                    effort=target.ref.effort,
                    effort_mapping=state.effort_mapping,
                    structured_output_mapper=state.structured_output_mapper,
                ),
                translator=state.translator,
            )
        except TranslationError as exc:
            if reservation is not None:
                await reservation.release()
                reservation = None
            result_status = 400
            return _error(result_status, "translation_error", str(exc))

        queue_ms = permit.queue_ms
        if context is not None:
            call = _build_call(request, target, caller, context, seq, run_id, cost_usd, queue_ms)
        dispatch_context = DispatchContext(
            target=target,
            prepared=prepared,
            source_protocol=source_protocol,
            target_protocol=target_protocol,
            streaming=streaming,
        )
        upstream = await state.dispatcher(dispatch_context)
        if upstream.status_code == 429:
            await lane.report_rate_limit()
        elif 200 <= upstream.status_code < 300:
            await lane.report_success()

        finalized = False

        async def finalize(status_code: int, tokens: Tokens | None = None) -> None:
            nonlocal permit, reservation, finalized
            if finalized:
                return
            finalized = True
            if call is not None and context is not None:
                final_call = call.model_copy(
                    update={
                        "status": status_code,
                        "tokens": tokens or Tokens(),
                        "queue_ms": queue_ms,
                        "total_ms": max(0, round((time.monotonic() - started) * 1000)),
                        "translated": prepared.translated,
                        "effort_applied": prepared.effort_applied,
                        "model_served": upstream.model_served,
                        "cost_usd": context.cost_usd,
                        "price_version": context.price_version,
                    }
                )
                if state.ledger is not None:
                    state.ledger.record(final_call)
                if state.exporter is not None:
                    state.exporter.submit_call(final_call)
            if reservation is not None:
                if 200 <= status_code < 300:
                    await reservation.settle(context.cost_usd if context is not None else None)
                else:
                    await reservation.release()
                reservation = None
            if permit is not None:
                assert lane is not None
                await lane.release()
                permit = None

        if upstream.status_code >= 400:
            if upstream.status_code == 429:
                async for _ in upstream.body:
                    pass
                await finalize(upstream.status_code, upstream.tokens)
                result_status = upstream.status_code
                return JSONResponse(
                    {
                        "error": {
                            "type": "upstream_rate_limit",
                            "message": "upstream rate limit reached",
                        }
                    },
                    status_code=429,
                    headers={
                        key: value
                        for key, value in upstream.headers.items()
                        if key.lower() in {"retry-after"}
                    },
                )
            async for _ in upstream.body:
                pass
            await finalize(upstream.status_code, upstream.tokens)
            result_status = upstream.status_code
            return _error(
                result_status, "upstream_error", f"upstream returned HTTP {result_status}"
            )

        async def response_stream() -> AsyncIterator[bytes]:
            sent = False
            stream_status = upstream.status_code
            try:
                async for chunk in upstream.body:
                    sent = sent or bool(chunk)
                    yield chunk
            except asyncio.CancelledError:
                stream_status = 499
                raise
            except Exception:
                stream_status = 502
                logger.warning("upstream stream failed after response began")
                raise
            finally:
                if not sent and stream_status == upstream.status_code:
                    stream_status = 502
                finalize_task = asyncio.create_task(finalize(stream_status, upstream.tokens))
                try:
                    await asyncio.shield(finalize_task)
                except asyncio.CancelledError:
                    await finalize_task
                    raise
                except Exception:
                    logger.exception("failed to finalize gateway call")
                finally:
                    record_metrics(stream_status)

        result_status = upstream.status_code
        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in {"connection", "transfer-encoding", "content-length"}
        }
        stream_owns_lifecycle = True
        return StreamingResponse(
            response_stream(), status_code=result_status, headers=response_headers
        )
    except asyncio.CancelledError:
        if permit is not None and lane is not None:
            await asyncio.shield(lane.release())
            permit = None
        if reservation is not None:
            await asyncio.shield(reservation.release())
            reservation = None
        raise
    except Exception:
        result_status = 502
        logger.exception("gateway upstream request failed")
        return _error(result_status, "upstream_error", "upstream request failed")
    finally:
        if not stream_owns_lifecycle:
            if permit is not None and lane is not None:
                await asyncio.shield(lane.release())
                permit = None
            if reservation is not None:
                await asyncio.shield(reservation.release())
                reservation = None
            record_metrics(result_status)


def _request_protocol(request: Request) -> str:
    if request.url.path.startswith("/v1beta/"):
        return "gemini"
    return {
        "/v1/chat/completions": "chat",
        "/v1/responses": "responses",
        "/v1/messages": "anthropic",
        "/v1/messages/count_tokens": "anthropic",
    }.get(request.url.path, "unknown")


def _target_protocol(target: Target) -> str:
    extra: dict[str, Any] = target.model.model_extra or {}
    protocols = extra.get("protocols")
    if isinstance(protocols, list) and protocols and isinstance(protocols[0], str):
        return protocols[0]
    provider_extra: dict[str, Any] = target.provider.model_extra or {}
    apis = provider_extra.get("apis")
    if isinstance(apis, dict) and apis:
        api_map = cast(dict[str, Any], apis)
        return str(next(iter(api_map)))
    return "unknown"


def _build_call(
    request: Request,
    target: Target,
    caller: Caller,
    context: CallContext,
    seq: int,
    run_id: str,
    cost_usd: float | None,
    queue_ms: int,
) -> Call:
    protocol = _request_protocol(request)
    return Call(
        id=context.call_id,
        seq=seq,
        run_id=run_id,
        trial_id=caller.trial_id,
        purpose="judge" if caller.purpose is Purpose.JUDGE else "contestant",
        protocol_in=protocol,
        protocol_out=_target_protocol(target),
        provider=target.provider.id,
        account_id=context.account_id,
        model_asked=target.asked,
        effort_asked=target.ref.effort,
        cost_usd=cost_usd,
        price_version=context.price_version,
        queue_ms=queue_ms,
    )


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
