# Gateway

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| HTTP surface | ASGI server for OpenAI chat and responses, Anthropic messages and token counting, Gemini stream generation, and model listing | [`arena.gateway.server.create_app`](../../src/arena/gateway/server.py) |
| Authentication | Require a trial, judge, or ops token on every route, including loopback | [`arena.gateway.auth.authenticate`](../../src/arena/gateway/auth.py) |
| Redaction | Scrub values named as credentials and recognizable secrets before logging, forwarding to later gateway components, or storing | [`arena.gateway.redact.scrub_json`](../../src/arena/gateway/redact.py) |
| Model resolution | Distinguish unknown model, disabled provider and unsupported provider/model protocol | [`arena.gateway.resolve.resolve_target`](../../src/arena/gateway/resolve.py) |
| Request orchestration | Authenticated protocol requests resolve targets, acquire per-provider lanes, invoke the injected dispatcher, and release permits on success, upstream errors, cancellation, and disconnect | [`arena.gateway.server._protocol`](../../src/arena/gateway/server.py) |
| Candidate planning | Order ready keys before resting keys while restricting attempts to the requested model | [`arena.gateway.plan.plan_candidates`](../../src/arena/gateway/plan.py) |
| Failure classification and rests | Classify provider failures narrowly, track per-key rests, reject malformed success replies, flag swapped models, and preserve content refusals | [`arena.gateway.classify.classify_failure`](../../src/arena/gateway/classify.py), [`arena.gateway.rests.RestBook`](../../src/arena/gateway/rests.py) |
| Dispatch contract | An immutable context passes target, prepared body, protocols, and streaming intent; caller headers and credentials are excluded | [`arena.gateway.server.DispatchContext`](../../src/arena/gateway/server.py) |
| Cassette primitives | Normalize JSON requests, append recorded response pairs, and replay exact protocol/request matches; replay has no live fallback | [`arena.gateway.cassettes.CassetteHandler`](../../src/arena/gateway/cassettes.py) |
| Budget and ledger context | Optional budget and ledger integrations require the injected `CallContext` to supply validated run, call, account, and pricing values | [`arena.gateway.server.CallContext`](../../src/arena/gateway/server.py) |
| Status and metrics | Ops status routes share the authenticated middleware; request metrics are recorded and an optional OTLP exporter starts and drains with app lifespan | [`arena.gateway.status.status_routes`](../../src/arena/gateway/status.py) |

## Runtime path

1. `create_app` installs `GatewayMiddleware` on the Starlette app. It refuses any request with an `Origin` header using 403, then requires an arena token using 401 when missing or invalid. This applies to loopback too.
2. A protocol route reads the request stream, limits the encoded and decoded size, and accepts identity, gzip or zstd bodies. It rejects unsupported encodings, truncated compressed data and invalid JSON before routing.
3. `resolve_target` validates the model and provider. If no dispatcher is configured, the request retains the compatibility 501 response. With a dispatcher, optional budget and ledger use is refused unless `CallContext` supplies validated identifiers, account hash, and price values.
4. The gateway prepares passthrough or translation, takes a per-provider lane permit, and calls the injected dispatcher with only model/protocol/body/streaming context. Incoming headers, cookies, auth tokens, and credentials are never included in that contract.
5. The response streams to the caller. For streaming responses, the body iterator owns finalization: it writes the ledger row and submits the span when configured, settles or releases the budget reservation, releases the lane permit, and records request metrics after body completion, upstream body failure or ASGI cancellation/disconnect. Pre-stream failures are cleaned up by the request handler. Non-stream responses finish accounting before returning their response.
6. `/arena/health`, `/arena/lanes`, and `/arena/stats` are mounted through the same authenticated middleware and require the ops purpose. Metrics are recorded for protocol requests. An optional OTLP exporter starts and drains in app lifespan.
7. `GET /v1/models` lists catalog models that belong to enabled providers and pass the protocol compatibility check.
8. Retry planning filters candidates to the requested model and places resting keys last. `classify_failure` maps upstream error bodies to narrow §5.5 classes; `classify_reply` detects malformed success replies, content refusals and model swaps. `RestBook` tracks per-key rests and honors rate-limit `Retry-After` values. These are primitives for dispatcher integration; the current server does not yet select provider handlers or run retries.
9. A caller that selects `CassetteHandler` can wrap an injected async provider handler in record mode, or use replay mode to match the normalized protocol and JSON request against JSON Lines entries. The current server does not choose provider handlers; the #95 dispatcher integration must select the handler and convert its result to/from `UpstreamResponse`.

## Constraints and failure behavior

- Tokens use `arena-<trial_id>`, `arena-judge-<run_id>` or `arena-ops`. Requests may carry a token as a Bearer credential, `x-api-key`, `x-goog-api-key`, or Gemini's `key` query parameter. A request needs a valid token regardless of network interface.
- All browser-originated requests are refused with 403. The gateway adds no CORS permission.
- Default decoded and encoded body limits are 10 MiB. Exceeding either returns 413; malformed or truncated gzip/zstd and invalid JSON return 400. The app accepts configurable positive limits.
- Redaction masks secret-named JSON fields and headers and recognizable API keys, JWTs, private keys, bearer credentials and URL passwords. Token count fields and inlined `data:` images are preserved. Log messages, arguments and tracebacks pass through `RedactingFilter`.
- Resolution failures are distinct 404 responses: `unknown_model`, `provider_off` and `not_served`. Missing model names and malformed model refs return 400.
- With no dispatcher configured, protocol POSTs return `not_implemented` (501). A configured dispatcher receives a `DispatchContext` with no caller headers or credentials; its own transport credentials/endpoints are configured out of band.
- `LaneRejected` maps to 429 with `Retry-After`; a full lane pool maps to 503. Streaming requests ask the lane to send keepalive callbacks while waiting. The callback currently only yields to the event loop; it does not send SSE comments. Sending comments before the dispatcher returns would require coordinating a single ASGI response start with subsequent response headers and status, so keepalive wire output remains unimplemented.
- Budget and ledger services are optional. Enabling either requires a `CallContextProvider` that returns validated run/call/account/pricing data. Missing provider configuration fails app construction; missing context for a request fails closed with 503. Budget checks also require an `EventLog`.
- The dispatcher contract supplies response status, headers, body, token counts and optional served model. Provider credential resolution/HTTP transport and protocol adapter configuration remain with #17. Production run/call/account lookup and pre-call price estimation need explicit runtime contracts from the gateway owner; this server does not invent them or use a zero-cost estimate.
- `CassetteReplayer` raises `CassetteMiss` when the file or matching protocol/request entry is absent. It never calls a live handler. Invalid entries and I/O failures raise `CassetteError`; record mode requires an injected handler. These primitives are not yet wired into `create_app` because dispatcher selection remains outside the owned server integration.
- The current lane key is provider scoped (`<provider>/main`) until #18/#17 supply selected account/key identity and rest-state integration. Rate-limit and success results update the lane's adaptive concurrency.

## Verification

```sh
uv run pytest tests/gateway/test_surface.py tests/gateway/test_server_integration.py tests/gateway/test_cassettes.py
```
