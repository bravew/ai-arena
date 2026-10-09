import type { Bundle, RunEvent, TrialStatus } from '../../lib/schema';

export type LiveBundle = Pick<Bundle, 'run' | 'contestants' | 'trials' | 'calls' | 'scores'>;

export interface LiveSnapshot {
  events: RunEvent[];
  trials: Map<string, { contestantId: string; taskId: string; status: TrialStatus }>;
  calls: Map<string, { trialId: string; contestantId: string; status: string }>;
  latestCaption: string;
  complete: boolean;
}

export interface LiveSource {
  read(): Promise<RunEvent[]>;
  subscribe?(onEvents: (events: RunEvent[]) => void, onError: (error: Error) => void): () => void;
}

export class ReplaySource implements LiveSource {
  constructor(private readonly events: RunEvent[]) {}
  async read(): Promise<RunEvent[]> { return [...this.events]; }
}

export class LongPollSource implements LiveSource {
  constructor(private readonly url: string, private readonly runId: string, private readonly fetcher: typeof fetch = fetch) {}
  async read(): Promise<RunEvent[]> { return this.fetchEvents(0, 0, new AbortController().signal); }
  subscribe(onEvents: (events: RunEvent[]) => void, onError: (error: Error) => void): () => void {
    const controller = new AbortController();
    let cursor = 0;
    const poll = async () => {
      while (!controller.signal.aborted) {
        try {
          const batch = await this.fetchEvents(cursor, 25, controller.signal);
          if (batch.length) {
            cursor = batch[batch.length - 1]!.seq;
            onEvents(batch);
          }
        } catch (error) {
          if (controller.signal.aborted) return;
          onError(error instanceof Error ? error : new Error(String(error)));
          return;
        }
      }
    };
    void poll();
    return () => controller.abort();
  }
  private async fetchEvents(after: number, wait: number, signal: AbortSignal): Promise<RunEvent[]> {
    const url = new URL(`${this.url.replace(/\/$/, '')}/api/runs/${encodeURIComponent(this.runId)}/events`, window.location.href);
    url.searchParams.set('after', String(after));
    url.searchParams.set('wait', String(wait));
    const response = await this.fetcher(url, { signal });
    if (!response.ok) throw new Error(`Live event request failed (${response.status})`);
    const body: unknown = await response.json();
    if (!Array.isArray(body)) throw new Error('Live event response must be an array');
    return body as RunEvent[];
  }
}

export function parseLiveBundle(input: unknown): LiveBundle | undefined {
  if (typeof input !== 'object' || input === null || Array.isArray(input)) return undefined;
  const value = input as Partial<Bundle>;
  if (!value.run || typeof value.run.id !== 'string' || !Array.isArray(value.contestants) || !Array.isArray(value.trials) || !Array.isArray(value.calls) || !Array.isArray(value.scores)) return undefined;
  if (!value.contestants.every((item) => item && typeof item.id === 'string') || !value.trials.every((item) => item && typeof item.id === 'string' && typeof item.contestant_id === 'string' && typeof item.task_id === 'string') || !value.calls.every((item) => item && typeof item.id === 'string') || !value.scores.every((item) => item && typeof item.trial_id === 'string' && typeof item.normalized === 'number')) return undefined;
  return { run: value.run, contestants: value.contestants, trials: value.trials, calls: value.calls, scores: value.scores };
}

export function reduceEvents(events: RunEvent[], bundle?: LiveBundle): LiveSnapshot {
  const ordered = [...events].sort((a, b) => a.seq - b.seq);
  const trials = new Map<string, { contestantId: string; taskId: string; status: TrialStatus }>((bundle?.trials ?? []).map((trial) => [trial.id, { contestantId: trial.contestant_id, taskId: trial.task_id, status: trial.status }]));
  const calls = new Map((bundle?.calls ?? []).map((call) => [call.id, { trialId: call.trial_id ?? '', contestantId: bundle?.trials.find((trial) => trial.id === call.trial_id)?.contestant_id ?? '', status: call.status === null ? 'unknown' : call.status >= 200 && call.status < 300 ? 'succeeded' : 'failed' }]));
  let latestCaption = 'Waiting for run events.';
  let complete = false;
  for (const event of ordered) {
    const data = event.data ?? {};
    if (event.kind === 'trial_queued' || event.kind === 'trial_started') {
      const id = event.ref;
      if (id) {
        const previous = trials.get(id);
        trials.set(id, { contestantId: stringField(data, 'contestant_id') ?? previous?.contestantId ?? '', taskId: stringField(data, 'task_id') ?? previous?.taskId ?? '', status: event.kind === 'trial_queued' ? 'queued' : 'running' });
      }
      latestCaption = event.kind === 'trial_started' ? `Trial ${event.ref ?? ''} started.` : `Trial ${event.ref ?? ''} queued.`;
    } else if (event.kind === 'call_queued' || event.kind === 'call_try' || event.kind === 'call_finished') {
      const id = event.ref;
      if (id) {
        const previous = calls.get(id);
        const trialId = stringField(data, 'trial_id') ?? previous?.trialId ?? '';
        const trial = trials.get(trialId);
        const status = event.kind === 'call_finished' ? stringField(data, 'status') ?? 'finished' : event.kind === 'call_try' ? 'in flight' : 'queued';
        calls.set(id, { trialId, contestantId: trial?.contestantId ?? '', status });
      }
      latestCaption = event.kind === 'call_try' ? `Call ${event.ref ?? ''} is in flight.` : event.kind === 'call_finished' ? `Call ${event.ref ?? ''} finished.` : `Call ${event.ref ?? ''} queued.`;
    } else if (event.kind === 'trial_finished') {
      if (event.ref) {
        const previous = trials.get(event.ref);
        trials.set(event.ref, { contestantId: previous?.contestantId ?? stringField(data, 'contestant_id') ?? '', taskId: previous?.taskId ?? stringField(data, 'task_id') ?? '', status: trialStatus(stringField(data, 'status')) ?? 'succeeded' });
      }
      latestCaption = `Trial ${event.ref ?? ''} ${stringField(data, 'status') ?? 'finished'}.`;
    } else if (event.kind === 'run_finished') {
      complete = true;
      latestCaption = `Run ${stringField(data, 'status') ?? 'finished'}.`;
    } else if (event.kind === 'lane_changed') latestCaption = 'Provider lane state changed.';
    else if (event.kind === 'budget') latestCaption = 'Run budget limit reached.';
  }
  return { events: ordered, trials, calls, latestCaption, complete };
}

function trialStatus(value: string | undefined): TrialStatus | undefined {
  return value === 'queued' || value === 'running' || value === 'succeeded' || value === 'failed' || value === 'errored' || value === 'timeout' || value === 'skipped' ? value : undefined;
}

export function ledgerTotals(bundle: LiveBundle): { trials: number; calls: number } {
  return { trials: bundle.trials.length, calls: bundle.calls.length };
}

export function stringField(data: Record<string, unknown>, field: string): string | undefined {
  const value = data[field];
  return typeof value === 'string' ? value : undefined;
}
