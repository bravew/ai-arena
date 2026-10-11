# Live viewer

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Live route | Presents replay and live run activity from a validated bundle and RunEvents | [`LiveRoute`](../../web/src/app/App.tsx), [`LiveView`](../../web/src/views/live/LiveView.tsx) |
| Event state | Orders and reduces RunEvents into trial/call state; exposes replay and long-poll sources | [`reduceEvents`](../../web/src/views/live/model.ts), [`ReplaySource`](../../web/src/views/live/model.ts), [`LongPollSource`](../../web/src/views/live/model.ts) |
| Stage | Shows contestants, gateway, lanes, accessible event caption, and reduced-motion behavior | [`LiveView`](../../web/src/views/live/LiveView.tsx) |
| Race chart and trial board | Shows score/rank progress and per-task trial state; accompanying tables expose the underlying facts | [`RaceChart`](../../web/src/views/live/LiveView.tsx), [`TrialBoard`](../../web/src/views/live/LiveView.tsx) |

## Runtime path

1. The Live route reads the current validated report bundle from the app's bundle context.
2. The view initializes a replay source from the published `fixtures/events/events.jsonl`; the event boundary validates each event with `validateEventStream`.
3. Every live response batch is validated with `validateEventStream`. `reduceEvents` orders the source stream by sequence and applies only trial, call, lane, budget, and run-finished states present in the replay cursor; bundle ledgers provide totals and descriptive labels, not replay state.
4. Replay controls advance an event cursor; a long-poll source can be selected with `?source=<arena-origin>` and subscribes using the run event cursor endpoint. Live mode reduces all received events immediately; replay mode uses the slider cursor. Initial reads merge with subscription batches, and callbacks from a previous source are ignored after switching sources.
5. The stage caption and facts tables render event and ledger state. The race chart summarizes score and completion progress, and the trial board displays current task status.

## Constraints and failure behavior

- RunEvents are the source of changing state; the view does not invent call results. Reduced-motion preference suppresses animation and advances replay state immediately.
- Animation frames stop when replay is paused, reduced motion is enabled, the stage leaves the viewport, or the document is hidden; scheduling resumes when the document becomes visible again. React frame-count state updates are sampled every 15 animation frames to avoid a full view render on every frame.
- For #134, the strict `<16 ms` frame-cadence acceptance gate is dropped in favor of recording the measurement: a 60 Hz display schedules frames about 16.67 ms apart even when idle. The browser harness keeps 200 calls in flight while the stage frame loop runs and samples 239 successive `requestAnimationFrame` intervals. On macOS, the full-suite Chromium sample measured 17.30 ms p95 and WebKit 18.00 ms p95; neither meets the original target. The test checks that all 200 calls remain observed and publishes the sample as an annotation and console output. This measures browser cadence, not per-frame rendering work; the stage currently has one aggregate call marker, so this is not a benchmark of 200 independently animated flights.
- Fixture parsing errors result in no fixture events; live transport request errors are shown as an alert and stop polling. The event stream remains isolated behind `LiveSource` for transport replacement.
- Every visual status is paired with text or a symbol and the detailed facts are available in semantic tables. The latest notable event is announced through a polite live region.
- Real server live mode depends on the long-poll API from issue #47. The #134 browser acceptance exercises the loopback `arena serve` endpoint with a local event file; it does not contact external providers.

## Verification

- `pnpm -C web test`
- `pnpm -C web e2e -- live` (includes the 200-call p95 browser sample and loopback `arena serve` long-poll test)
- `pnpm -C web lint`
- `pnpm -C web typecheck`
