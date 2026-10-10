# Server and static export

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| HTTP server | Serve the viewer assets, run listing and bundle API, health, and event long-poll. Dispatch registered GET and POST routes through `ArenaHTTPServer.register_route` for extensions such as human votes. | [`create_server`](../../src/arena/server/app.py) |
| Remote authentication | Require `ARENA_RUN_KEY` and a configured trusted TLS-terminating proxy for non-loopback binds; exchange the key by POST for a Secure, HttpOnly SameSite cookie; refuse browser-origin POSTs. | `create_server` in [`app.py`](../../src/arena/server/app.py) |
| Artifact origin | Serve authenticated, content-addressed artifact blobs from a separate listener, using stored MIME metadata and restrictive sandboxing headers. | `serve_artifact` and `serve` in [`app.py`](../../src/arena/server/app.py) |
| Static viewer packaging | Copy only a prebuilt Vite `web/dist` tree into a bundle export; reject missing assets and symlinks. | [`export_viewer_assets`](../../src/arena/server/static.py) |
| CLI | Register `arena serve` and extend `arena export --format static`. | [`cli_serve.py`](../../src/arena/cli_serve.py), [`cli_report.py`](../../src/arena/cli_report.py) |

## Runtime path

1. `arena serve` invokes `serve` with loopback `127.0.0.1:7400` and artifact port `7402` defaults.
2. `serve` starts separate standard-library threaded HTTP servers on separate ports. The viewer/API server serves `web/dist` when built and `/api/health`, `/api/runs`, `/api/runs/{id}/bundle`, and `/api/runs/{id}/events` routes. Both listeners share one process-local session-signing key so the artifact listener can validate the API's signed session cookie.
3. Event requests validate the JSONL records and return an array of events with `seq > after`, matching the viewer's `LongPollSource` contract, waiting up to the capped `wait` duration when no new events exist.
4. The artifact server requires the same signed session cookie as the remote API, accepts only a lowercase SHA-256 digest path, and verifies the blob through `ArtifactStore`. It uses the recorded artifact MIME type when it is allowed and returns a restrictive content security policy, `X-Frame-Options: DENY`, and `Cross-Origin-Resource-Policy: cross-origin`.
5. `arena export --format static` writes the validated bundle and event log, copies the already-built `web/dist` tree, inlines its JavaScript and CSS into `index.html`, and embeds the run data as inert JSON for the viewer to validate and load.

## Constraints and failure behavior

- Loopback is the default. A non-loopback bind without both a run key and `--trusted-proxy` / `ARENA_TRUSTED_PROXY` fails before listening. The artifact listener is also bound off-box in this configuration and requires the API-issued signed session cookie. A loopback artifact listener with a run key also requires that cookie; the default loopback configuration without a run key allows local artifact reads. Remote sign-in accepts `X-Forwarded-Proto: https` only when the socket peer matches the configured proxy IP; the proxy must terminate TLS and replace that header. The sign-in key is accepted only in a bounded POST JSON body. Success sets a Secure, HttpOnly, SameSite=Strict cookie.
- Any non-auth POST carrying an `Origin` header returns 403. Unauthenticated remote POSTs return 401. GET on `/auth/sign-in` returns 405.
- The gateway has no route or listener in this package. When the viewer/API binds off-box, the artifact listener shares that bind and must sit behind the configured trusted TLS proxy. Artifact documents run in a CSP sandbox without script or same-origin privileges; only an explicit set of complete MIME values is taken from artifact metadata, with unknown types served as `application/octet-stream`.
- Run IDs and artifact digests are constrained before filesystem access. Event parse/schema failures return an error and are not reported as an empty event stream.
- Static packaging needs a prebuilt `web/dist` containing the expected Vite module and stylesheet tags. The exporter inlines those assets and embeds run data in inert JSON so opening `index.html` from `file://` does not fetch adjacent files. The viewer uses hash routing and validates both the embedded bundle and event stream; Live replays that run's events. Missing or malformed embedded data is reported as a validation/load error. `--format static` reports missing viewer assets and removes its staging directory on failure.
- The server uses only the Python standard library and existing bundle/storage code.

## Verification

- `uv run pytest tests/server tests/test_cli.py`
- `uv run pyright`
- `uv run ruff check`
- `uv run ruff format --check`
