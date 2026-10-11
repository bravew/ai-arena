# Server and static export

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| HTTP server | Serve the viewer assets, run listing and bundle API, health, and event long-poll. Dispatch registered GET and POST routes through `ArenaHTTPServer.register_route` for extensions such as human votes. | [`create_server`](../../src/arena/server/app.py) |
| Remote authentication | Require `ARENA_RUN_KEY` and a configured trusted TLS-terminating proxy for non-loopback binds; exchange the key by POST for a Secure, HttpOnly SameSite cookie; refuse browser-origin POSTs. | `create_server` in [`app.py`](../../src/arena/server/app.py) |
| Artifact origin | Serve authenticated, content-addressed artifact blobs from a separate listener, using stored MIME metadata and restrictive sandboxing headers. | `serve_artifact` and `serve` in [`app.py`](../../src/arena/server/app.py) |
| Human vote routes | `GET /api/votes/pair`, `POST /api/votes`, `GET /api/votes/leaderboard` on the viewer/API listener only. The voter id is a random `arena_voter` HttpOnly SameSite=Strict cookie issued with the first pair. Blind pairs are built at startup from succeeded trials' first text artifact (`text/plain`, `text/markdown`), pairing different contestants on the same run and task; votes append to `votes.jsonl` in the data home. | [`register_vote_routes`](../../src/arena/server/vote_routes.py), [`build_pairs`](../../src/arena/server/vote_pairs.py) |
| Provider onboarding | `GET /api/providers` returns provider kind, configured runtime, CLI/version/auth state and recovery hint, with redacted account ids and API env names only. `POST /api/providers/{id}/test` sends one capped test and is rate-limited per provider. API connection tests require an absolute HTTPS URL (or a loopback HTTP URL, which the DEV_PLAN allows for the local gateway), reject userinfo, do not follow redirects, and report `ok` only for a 2xx status. | [`register_provider_routes`](../../src/arena/server/providers_api.py), [`ProvidersView`](../../web/src/views/providers/ProvidersView.tsx) |
| Gold-set export | `arena votes export-gold --home <dir> [--out file]` writes one JSONL row per voted pair: majority `human_verdict` (`a`, `b`, `tie`, `both_bad`) in the pair's own A/B order, vote counts, trial and contestant ids, answer lengths. Rows carry no judge verdict, so they join a judge's output by `item_id`; they are not a `PairJudgment` file. | [`gold_labels`](../../src/arena/server/votes.py), [`export_gold`](../../src/arena/cli_votes.py) |
| Static viewer packaging | Copy only a prebuilt Vite `web/dist` tree into a bundle export; reject missing assets and symlinks. | [`export_viewer_assets`](../../src/arena/server/static.py) |
| CLI | Register `arena serve` and extend `arena export --format static`. | [`cli_serve.py`](../../src/arena/cli_serve.py), [`cli_report.py`](../../src/arena/cli_report.py) |

## Runtime path

1. `arena serve` invokes `serve` with loopback `127.0.0.1:7400` and artifact port `7402` defaults.
2. `serve` starts separate standard-library threaded HTTP servers on separate ports. The viewer/API server serves `web/dist` when built and `/api/health`, `/api/runs`, `/api/runs/{id}/bundle`, and `/api/runs/{id}/events` routes. Both listeners share one process-local session-signing key so the artifact listener can validate the API's signed session cookie.
3. Event requests validate the JSONL records and return an array of events with `seq > after`, matching the viewer's `LongPollSource` contract, waiting up to the capped `wait` duration when no new events exist.
4. The artifact server requires the same signed session cookie as the remote API, accepts only a lowercase SHA-256 digest path, and verifies the blob through `ArtifactStore`. It uses the recorded artifact MIME type when it is allowed and returns a restrictive content security policy, `X-Frame-Options: DENY`, and `Cross-Origin-Resource-Policy: cross-origin`.
5. `arena export --format static` writes the validated bundle and event log, copies the already-built `web/dist` tree, inlines its JavaScript and CSS into `index.html`, and embeds the run data as inert JSON for the viewer to validate and load.

## Constraints and failure behavior

- Loopback is the default. A non-loopback bind without both a run key and `--trusted-proxy` / `ARENA_TRUSTED_PROXY` fails before listening. The artifact listener is also bound off-box in this configuration and requires the API-issued signed session cookie. A loopback artifact listener with a run key also requires that cookie, and the loopback API accepts sign-in when that key is configured; the default loopback configuration without a run key allows local artifact reads. Remote sign-in accepts `X-Forwarded-Proto: https` only when the socket peer matches the configured proxy IP; the proxy must terminate TLS and replace that header. The sign-in key is accepted only in a bounded POST JSON body. Success sets a Secure, HttpOnly, SameSite=Strict cookie.
- A POST (sign-in included) whose `Origin` names a different host:port than its `Host` header returns 403; a viewer's same-origin POST passes. Non-API paths without a file extension that match no file serve the viewer's `index.html`, so routes such as `/vote` survive a reload; `/api/` paths and missing files still return 404.
- Unauthenticated remote POSTs return 401. GET on `/auth/sign-in` returns 405.
- The gateway has no route or listener in this package. When the viewer/API binds off-box, the artifact listener shares that bind and must sit behind the configured trusted TLS proxy. Artifact documents run in a CSP sandbox without script or same-origin privileges; only an explicit set of complete MIME values is taken from artifact metadata, with unknown types served as `application/octet-stream`.
- Run IDs and artifact digests are constrained before filesystem access. Event parse/schema failures return an error and are not reported as an empty event stream.
- Static packaging needs a prebuilt `web/dist` containing the expected Vite module and stylesheet tags. The exporter inlines those assets and embeds run data in inert JSON so opening `index.html` from `file://` does not fetch adjacent files. The viewer uses hash routing and validates both the embedded bundle and event stream; Live replays that run's events. Missing or malformed embedded data is reported as a validation/load error. `--format static` reports missing viewer assets and removes its staging directory on failure.
- Vote pairs are read once when the server starts, so trials finished later need a restart. A home with no text artifacts serves 204 and an empty leaderboard. Pair content is whatever the trial produced; the server cannot remove a model name an answer states about itself, so identity hiding covers the API payload, not the text. At most 200 pairs per run task and 20,000 characters per side. `votes.jsonl` is local and does not coordinate simultaneous writers. `pnpm -C web e2e:live` builds the viewer, seeds a temporary home with 20 two-contestant tasks (`tests/server/seed_vote_home.py`), runs `arena serve` over it and casts 20 votes in Chromium, checking that identities stay hidden until the vote, the leaderboard updates, and 20 lines reach `votes.jsonl`. `pnpm -C web e2e -- arena` still uses the mock data source.
- The server uses only the Python standard library and existing bundle/storage code.

## Verification

- `uv run pytest tests/server tests/test_cli.py`
- `uv run --with playwright pytest tests/server/test_artifact_browser.py` verifies actual browser script blocking, sandbox cookie denial and blocked API response. The optional test skips when Python Playwright is unavailable.
- `uv run pyright`
- `uv run ruff check`
- `uv run ruff format --check`
