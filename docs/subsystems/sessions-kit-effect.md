# Sessions and Kit effect views

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Sessions list and timeline | Filter sessions by applied kit and show turn usage, calls, tools, MCP calls, skill events, file changes, and partial status | [`SessionsView`](../../web/src/views/sessions/SessionsView.tsx), [`getSessionRows`](../../web/src/views/sessions/session-data.ts) |
| Turn usage | Sum gateway call token fields and known costs from each turn's call IDs; unknown call cost remains unavailable | [`getTurnMetrics`](../../web/src/views/sessions/session-data.ts) |
| Kit effect pairing | Compare scored kit trials only with a unique, same-run/task baseline whose model, agent and non-kit configuration match | [`buildPairs`](../../web/src/views/kit-effect/kit-effect-data.ts) |
| Kit effect display | Show paired score and cost differences, observed skill uptake, and label the invoked/not-invoked split as observational | [`KitEffectView`](../../web/src/views/kit-effect/KitEffectView.tsx) |

## Runtime path

1. The app validates the imported bundle and supplies the validated value to the route components through [`App`](../../web/src/app/App.tsx).
2. `SessionsView` derives rows by joining sessions to trials and contestants, and associates turn call IDs with the gateway calls in the bundle.
3. The kit filter uses `KitInstall.kit_hash`, falling back to `No kit` when a trial has no install record. Selecting a session renders its turns and event markers.
4. `KitEffectView` visits kit-installed trials and finds baseline candidates in the same run, task, and attempt. It requires exactly one baseline, a `none` kit identity, and matching model, scaffold ID and version, prompt mode, orchestration, prompt version, model parameters, scaffold settings, and hooks. A baseline can be used by only one pair; ambiguous or unmatched candidates are omitted.
5. The view averages normalized score rows for each paired trial and sums costs only when every associated call has a known cost. It displays the observational skill-use split without implying causal effect.

## Constraints and failure behavior

- Missing sessions, calls, scores, or kit installs produce empty or unavailable view data; no invented usage or score is shown.
- A call with unknown cost makes that session/turn/pair cost unavailable rather than treating it as zero.
- A kit trial without a unique configuration-matched baseline is omitted from the Kit effect table. A baseline is used for at most one pair.
- `partial` is surfaced from the session status. Unmetered turns are marked separately.
- The kit-effect comparison is descriptive. Skill invocation is observational and does not establish causation.

## Verification

- `pnpm -C web test`
- `pnpm -C web e2e -- sessions` (Chromium and WebKit)
- `pnpm -C web typecheck`
- `pnpm -C web lint`
