# Gateway

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| HTTP surface | ASGI server for OpenAI chat and responses, Anthropic messages and token counting, Gemini stream generation, and model listing | [`arena.gateway.server.create_app`](../../src/arena/gateway/server.py) |
| Authentication | Require a trial, judge, or ops token on every route, including loopback | [`arena.gateway.auth.authenticate`](../../src/arena/gateway/auth.py) |
| Redaction | Scrub values named as credentials and recognizable secrets before logging, forwarding to later gateway components, or storing | [`arena.gateway.redact.scrub_json`](../../src/arena/gateway/redact.py) |
| Model resolution | Distinguish unknown model, disabled provider and unsupported provider/model protocol | [`arena.gateway.resolve.resolve_target`](../../src/arena/gateway/resolve.py) |
| Protocol relay | Owned by gateway issue #17; protocol routes currently stop with 501 after common request checks | [`arena.gateway.server._protocol`](../../src/arena/gateway/server.py) |

## Runtime path

1. `create_app` installs `GatewayMiddleware` on the Starlette app. It refuses any request with an `Origin` header using 403, then requires an arena token using 401 when missing or invalid. This applies to loopback too.
2. A protocol route reads the request stream, limits the encoded and decoded size, and accepts identity, gzip or zstd bodies. It rejects unsupported encodings, truncated compressed data and invalid JSON before recording any request data.
3. The request's model name is resolved by `resolve_target`. The provider must exist and be enabled, the model must be in the catalog, and when catalog protocol and provider endpoint declarations exist, they must intersect.
4. The body and headers are scrubbed and retained as redacted request metadata for downstream gateway issues. The protocol handler currently returns 501 after this common surface; issue #17 adds passthrough and translation.
5. `GET /v1/models` lists catalog models that belong to enabled providers and pass the protocol compatibility check.

## Constraints and failure behavior

- Tokens use `arena-<trial_id>`, `arena-judge-<run_id>` or `arena-ops`. Requests may carry a token as a Bearer credential, `x-api-key`, `x-goog-api-key`, or Gemini's `key` query parameter. A request needs a valid token regardless of network interface.
- All browser-originated requests are refused with 403. The gateway adds no CORS permission.
- Default decoded and encoded body limits are 10 MiB. Exceeding either returns 413; malformed or truncated gzip/zstd and invalid JSON return 400. The app accepts configurable positive limits.
- Redaction masks secret-named JSON fields and headers and recognizable API keys, JWTs, private keys, bearer credentials and URL passwords. Token count fields and inlined `data:` images are preserved. Log messages, arguments and tracebacks pass through `RedactingFilter`.
- Resolution failures are distinct 404 responses: `unknown_model`, `provider_off` and `not_served`. Missing model names and malformed model refs return 400.
- Protocol POSTs currently return `not_implemented` (501) after the common checks. This is the expected integration point for #17.

## Verification

```sh
uv run pytest tests/gateway/test_surface.py
```
