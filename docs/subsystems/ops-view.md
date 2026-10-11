# Ops view

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Calls ledger | Filter call rows by provider, status and call/model/account text; display attribution, tokens, cost, queue and total latency | [`OpsView`](../../web/src/views/ops/OpsView.tsx) |
| Usage and latency | Render supplied precomputed time series without deriving chart data in the browser | [`SeriesChart`](../../web/src/views/ops/OpsView.tsx) |
| Lanes and rests | Display in-flight versus concurrency, queued calls and any active rest countdown | [`OpsView`](../../web/src/views/ops/OpsView.tsx) |
| Subscription meters | Display usage against a meter limit and time remaining until its reset | [`OpsView`](../../web/src/views/ops/OpsView.tsx) |
| Hook stats | Show hook call count, failures and average duration | [`OpsView`](../../web/src/views/ops/OpsView.tsx) |
| Fixture adapter | Supply deterministic data through the replaceable `OpsDataSource` interface; its lane fields follow the documented gateway snapshot shape | [`fixtureOps`](../../web/src/views/ops/fixtures.ts), [`OpsDataSource`](../../web/src/views/ops/fixtures.ts) |

## Runtime path

1. The router mounts `OpsView` at `/ops`.
2. `OpsView` reads calls, lane snapshots, precomputed series, subscription meters and hook totals from the injected `OpsDataSource` (the current default is `fixtureOps`).
3. Call filters are applied in the view, then the table renders token totals and cost/latency values. Usage and latency chart marks use the series values as received.
4. Lane and account reset timestamps are compared with the current time to show countdowns. Hook statistics are presented as supplied.

## Constraints and failure behavior

- Lane records use the gateway `/arena/lanes` `lanes` envelope fields `in_flight`, `queued`, `concurrency`, `resting_until` and `rest_class`. Gateway `/arena/stats` metrics are precomputed by its metrics registry; this fixture UI does not derive historical series from raw counters.
- The view uses fixture data by default and makes no live gateway requests. It does not imply that live health, lane state, hook instrumentation or vendor subscription meters were exercised. The replaceable `OpsDataSource` is the future adapter seam for calls, lanes, precomputed usage, meters and hooks.
- The #134 real-gateway exercise criterion is dropped: this checkout's `arena serve` exposes bundle and event APIs, but has no gateway `/arena/lanes` route or ledger snapshot endpoint, and the viewer has no authenticated gateway adapter. Fixture regressions cover the rendered lane and ledger fields; they do not count as real gateway exercise. Adding the gateway routes and viewer authentication/adapter crosses issue ownership and remains future integration work.
- A missing cost is displayed as `flat`; it is not converted to zero. Meter, lane and hook values have no live-source error state until a live data adapter is integrated.

## Verification

```sh
pnpm -C web test
pnpm -C web typecheck
pnpm -C web lint
pnpm -C web e2e -- ops
```
