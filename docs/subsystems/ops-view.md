# Ops view

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Calls ledger | Filter call rows by provider, status and call/model/account text; display attribution, tokens, cost, queue and total latency | [`OpsView`](../../web/src/views/ops/OpsView.tsx) |
| Usage and latency | Render supplied precomputed time series without deriving chart data in the browser | [`SeriesChart`](../../web/src/views/ops/OpsView.tsx) |
| Lanes and rests | Display in-flight versus concurrency, queued calls and any active rest countdown | [`OpsView`](../../web/src/views/ops/OpsView.tsx) |
| Subscription meters | Display usage against a meter limit and time remaining until its reset | [`OpsView`](../../web/src/views/ops/OpsView.tsx) |
| Hook stats | Show hook call count, failures and average duration | [`OpsView`](../../web/src/views/ops/OpsView.tsx) |
| HTTP adapter | Fetch the authenticated server's `/api/ops` snapshot with same-origin credentials | [`fetchOpsSnapshot`](../../web/src/views/ops/http-source.ts) |
| Fixture adapter | Supply deterministic data when a component test injects it or the live snapshot cannot load | [`fixtureOps`](../../web/src/views/ops/fixtures.ts), [`OpsDataSource`](../../web/src/views/ops/fixtures.ts) |
| Server route | Read the calls ledger, aggregate arena-counted tokens, list redacted stored accounts and report vendor CLI status | [`register_ops_routes`](../../src/arena/server/ops.py) |

## Runtime path

1. The router mounts `OpsView` at `/ops`.
2. Without an injected source, `OpsView` requests `/api/ops` with same-origin credentials. The server's normal authentication check protects this route; off-box use requires a run key and session.
3. The server returns the latest 200 calls, one usage and mean-latency point per run, arena-counted token meters, redacted stored accounts, and cached vendor CLI status probes. Calls from API-key providers and `cli_reported` calls from Claude or Codex share the ledger while retaining their provider and protocol attribution. There is no vendor quota endpoint in this runtime.
4. Calls and subscription meters are live. Lanes and hooks are empty and each has a source explanation because the viewer process has no live gateway lane snapshot or hook instrumentation. An HTTP error is shown in the page, with fixture values retained as fallback.

## Constraints and failure behavior

- The route returns the latest 200 ledger calls and groups up to 5,000 ledger calls into per-run usage and mean latency. The ledger stores run creation timestamps, not call timestamps, so the series is explicitly a run-level summary.
- A successful call without a recorded price contributes its tokens to a meter grouped by provider, redacted account id and model. The label says `arena-counted`; limits and reset times are null because vendor quotas are not queried. Missing call cost remains `flat` in the table.
- Provider status uses only the read-only CLI probes from `providers detect`, and the results are cached for 30 seconds. The response contains status, auth state, binary path, version, detail and recovery hint, never a sign-in payload, token, environment value or API key.
- Lanes remain empty until the server has a live gateway snapshot API. Hooks remain empty until hook instrumentation is connected. `sources` describes both gaps and the origin of every supplied metric.
- The route is GET-only and uses the server's standard auth path. In remote mode, that path requires the run key session; browser cross-origin protections remain enforced by the base server.

## Verification

```sh
pnpm -C web test
pnpm -C web typecheck
pnpm -C web lint
pnpm -C web e2e -- ops
```
