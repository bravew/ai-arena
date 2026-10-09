# Gateway

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| HTTP surface | ASGI server for OpenAI chat and responses, Anthropic messages and token counting, Gemini stream generation, and model listing | [`arena.gateway.server.create_app`](../../src/arena/gateway/server.py) |
| Authentication | Require a trial, judge, or ops token on every route, including loopback | [`arena.gateway.auth.authenticate`](../../src/arena/gateway/auth.py) |
| Redaction | Scrub values named as credentials and recognizable secrets before logging, forwarding to later gateway components, or storing | [`arena.gateway.redact.scrub_json`](../../src/arena/gateway/redact.py) |
| Model resolution | Distinguish unknown model, disabled provider and unsupported provider/model protocol | [`arena.gateway.resolve.resolve_target`](../../src/arena/gateway/resolve.py) |
| Request orchestration | Authenticated protocol requests resolve targets, acquire per-provider lanes, invoke the injected dispatcher, and release permits on success, upstream errors, cancellation, and disconnect | [`arena.gateway.server._protocol`](../../src/arena/gateway/server.py) |
| Dispatch contract | An immutable context passes target, prepared body, protocols, and streaming intent; caller headers and credentials are excluded | [`arena.gateway.server.DispatchContext`](../../src/arena/gateway/server.py) |
| Budget and ledger context | Optional budget and ledger integrations require the injected `CallContext` to supply validated run, call, account, and pricing values | [`arena.gateway.server.CallContext`](../../src/arena/gateway/server.py) |
| Status and metrics | Ops status routes share the authenticated middleware; request metrics are recorded and an optional OTLP exporter starts and drains with app lifespan | [`arena.gateway.status.status_routes`](../../src/arena/gateway/status.py) |

## Runtime path

1. `create_app` installs `GatewayMiddleware` on the Starlette app. It refuses any request with an `Origin` header using 403, then requires an arena token using 401 when missing or invalid. This applies to loopback too.
2. A protocol route reads the request stream, limits the encoded and decoded size, and accepts identity, gzip or zstd bodies. It rejects unsupported encodings, truncated compressed data and invalid JSON before routing.
3. `resolve_target` validates the model and provider. If no dispatcher is configured, the request retains the compatibility 501 response. With a dispatcher, optional budget and ledger use is refused unless `CallContext` supplies validated identifiers, account hash, and price values.
4. The gateway prepares passthrough or translation, takes a per-provider lane permit, and calls the injected dispatcher with only model/protocol/body/streaming context. Incoming headers, cookies, auth tokens, and credentials are never included in that contract.
5. The response streams to the caller; finalization writes the ledger row and submits the span when configured, then settles budget and releases the lane permit. The permit is released on dispatcher failures, cancellation and client disconnect. Client disconnect during a streaming response still leaves response consumption to the ASGI server and injected dispatcher lifecycle.
6. `/arena/health`, `/arena/lanes`, and `/arena/stats` are mounted through the same authenticated middleware and require the ops purpose. Metrics are recorded for protocol requests. An optional OTLP exporter starts and drains in app lifespan.
7. `GET /v1/models` lists catalog models that belong to enabled providers and pass the protocol compatibility check.

## Constraints and failure behavior

- Tokens use `arena-<trial_id>`, `arena-judge-<run_id>` or `arena-ops`. Requests may carry a token as a Bearer credential, `x-api-key`, `x-goog-api-key`, or Gemini's `key` query parameter. A request needs a valid token regardless of network interface.
- All browser-originated requests are refused with 403. The gateway adds no CORS permission.
- Default decoded and encoded body limits are 10 MiB. Exceeding either returns 413; malformed or truncated gzip/zstd and invalid JSON return 400. The app accepts configurable positive limits.
- Redaction masks secret-named JSON fields and headers and recognizable API keys, JWTs, private keys, bearer credentials and URL passwords. Token count fields and inlined `data:` images are preserved. Log messages, arguments and tracebacks pass through `RedactingFilter`.
- Resolution failures are distinct 404 responses: `unknown_model`, `provider_off` and `not_served`. Missing model names and malformed model refs return 400.
- With no dispatcher configured, protocol POSTs return `not_implemented` (501). A configured dispatcher receives a `DispatchContext` with no caller headers or credentials; its own transport credentials/endpoints are configured out of band.
- `LaneRejected` maps to 429 with `Retry-After`; a full lane pool maps to 503. Streaming requests ask the lane to send keepalive callbacks while waiting. The server callback is a no-op placeholder pending an ASGI response-streaming queue contract.
- Budget and ledger services are optional. Enabling either requires a `CallContextProvider` that returns validated run/call/account/pricing data. Missing provider configuration fails app construction; missing context for a request fails closed with 503. Budget checks also require an `EventLog`.
- The dispatcher contract supplies response status, headers, body, token counts and optional served model. Provider credential resolution/HTTP transport and protocol adapter configuration remain with #17. Production run/call/account lookup and pre-call price estimation need explicit runtime contracts from the gateway owner; this server does not invent them or use a zero-cost estimate.
- The current lane key is provider scoped (`<provider>/main`) until #18/#17 supply selected account/key identity and rest-state integration. Rate-limit and success results update the lane's adaptive concurrency.

## Verification

```sh
uv run pytest tests/gateway/test_surface.py tests/gateway/test_server_integration.py
```
