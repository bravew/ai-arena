# Server and static export

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| HTTP server | Serve the viewer assets, run listing and bundle API, health, and event long-poll. Dispatch registered GET and POST routes through `ArenaHTTPServer.register_route` for extensions such as human votes. | [`create_server`](../../src/arena/server/app.py) |
| Remote authentication | Require `ARENA_RUN_KEY` and a configured trusted TLS-terminating proxy for non-loopback binds; exchange the key by POST for a Secure, HttpOnly SameSite cookie; refuse browser-origin POSTs. | `create_server` in [`app.py`](../../src/arena/server/app.py) |
| Artifact origin | Serve content-addressed HTML artifact blobs from a separate listener with sandboxing headers. | `serve_artifact` and `serve` in [`app.py`](../../src/arena/server/app.py) |
| Static viewer packaging | Copy only a prebuilt Vite `web/dist` tree into a bundle export; reject missing assets and symlinks. | [`export_viewer_assets`](../../src/arena/server/static.py) |
| CLI | Register `arena serve` and extend `arena export --format static`. | [`cli_serve.py`](../../src/arena/cli_serve.py), [`cli_report.py`](../../src/arena/cli_report.py) |

## Runtime path

1. `arena serve` invokes `serve` with loopback `127.0.0.1:7400` and artifact port `7402` defaults.
2. `serve` starts separate standard-library threaded HTTP servers. The viewer/API server serves `web/dist` when built and `/api/health`, `/api/runs`, `/api/runs/{id}/bundle`, and `/api/runs/{id}/events` routes.
3. Event requests validate the JSONL records and return an array of events with `seq > after`, matching the viewer's `LongPollSource` contract, waiting up to the capped `wait` duration when no new events exist.
4. The artifact server accepts only a lowercase SHA-256 digest path and verifies the blob through `ArtifactStore`; responses include a restrictive content security policy.
5. `arena export --format static` writes the validated report bundle, then copies the already-built `web/dist` files into the export directory.

## Constraints and failure behavior

- Loopback is the default. A non-loopback bind without both a run key and `--trusted-proxy` / `ARENA_TRUSTED_PROXY` fails before listening. Remote sign-in accepts `X-Forwarded-Proto: https` only when the socket peer matches the configured proxy IP; the proxy must terminate TLS and replace that header. The sign-in key is accepted only in a bounded POST JSON body. Success sets a Secure, HttpOnly, SameSite=Strict cookie.
- Any non-auth POST carrying an `Origin` header returns 403. Unauthenticated remote POSTs return 401. GET on `/auth/sign-in` returns 405.
- The gateway has no route or listener in this package; the separate artifact listener is loopback-only when the viewer/API is bound off-box.
- Run IDs and artifact digests are constrained before filesystem access. Event parse/schema failures return an error and are not reported as an empty event stream.
- Static packaging needs a prebuilt `web/dist`. The current shell hardcodes the fixture bundle and Live remains a placeholder, so this server issue cannot provide a run-specific viewer or Live replay from `file://` without changes to web-owned files. `--format static` reports the precise missing-assets/build condition instead of fabricating viewer behavior.
- The server uses only the Python standard library and existing bundle/storage code.

## Verification

- `uv run pytest tests/server tests/test_cli.py`
- `uv run pyright`
- `uv run ruff check`
- `uv run ruff format --check`
