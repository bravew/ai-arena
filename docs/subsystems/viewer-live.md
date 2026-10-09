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
3. `reduceEvents` orders the source stream by sequence and applies trial, call, lane, budget, and run-finished state. Initial ledger rows seed the counters so the event replay view can be compared with recorded totals.
4. Replay controls advance an event cursor; a long-poll source can be selected with `?source=<arena-origin>` and subscribes using the run event cursor endpoint.
5. The stage caption and facts tables render event and ledger state. The race chart summarizes score and completion progress, and the trial board displays current task status.

## Constraints and failure behavior

- RunEvents are the source of changing state; the view does not invent call results. Reduced-motion preference suppresses animation and advances replay state immediately.
- Animation frames stop when replay is paused, reduced motion is enabled, or the stage leaves the viewport. A hidden document does not start a frame loop.
- Fixture parsing errors result in no fixture events; live transport request errors are shown as an alert and stop polling. The event stream remains isolated behind `LiveSource` for transport replacement.
- Every visual status is paired with text or a symbol and the detailed facts are available in semantic tables. The latest notable event is announced through a polite live region.
- Real server live mode depends on the long-poll API from issue #47 and is not verified until that work merges.

## Verification

- `pnpm -C web test`
- `pnpm -C web e2e -- live`
- `pnpm -C web lint`
- `pnpm -C web typecheck`
