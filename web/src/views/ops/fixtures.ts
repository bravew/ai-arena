import type { Call } from '../../lib/schema';

export interface LaneStatus {
  in_flight: number;
  queued: number;
  concurrency: number;
  resting_until: number | null;
  rest_class: string | null;
}

export interface UsagePoint { label: string; tokens: number; latencyMs: number }
export interface SubscriptionMeter { account: string; model: string; label: string; used: number; limit: number; resetsAt: string }
export interface HookStat { hook: string; calls: number; failures: number; averageUs: number }

export interface OpsDataSource {
  calls(): Call[];
  lanes(): Record<string, LaneStatus>;
  usage(): UsagePoint[];
  meters(): SubscriptionMeter[];
  hooks(): HookStat[];
}

// Fixtures conform to the gateway /arena/lanes envelope and Metrics.snapshot() maps.
// A live OpsDataSource can replace this adapter when the gateway is mounted in the viewer.
export const fixtureOps: OpsDataSource = {
  calls: () => [
    { id: 'call-sub', seq: 1, run_id: 'run-schema-001', trial_id: 'trial-sub', span_id: null, parent_span_id: null, purpose: 'contestant', protocol_in: 'anthropic', protocol_out: 'anthropic', translated: false, provider: 'anthropic-max', account_id: 'acct-hash-01', model_asked: 'claude-opus-5-5', model_served: 'claude-opus-5-5', swapped: false, upstream: null, effort_asked: null, effort_applied: null, tries: [{ account_id: 'acct-hash-01', status: 200, error_class: null, rest_ms: 0, ms: 812 }], status: 200, error_class: null, tokens: { in: 120, out: 32, reasoning: 0, cache_read: 0, cache_write: 0 }, cost_usd: null, price_version: null, queue_ms: 0, ttft_ms: 310, first_text_ms: 320, total_ms: 812, cache: 'off', prompt_parts: [{ kind: 'conversation', tokens: 120 }] },
    { id: 'call-api', seq: 2, run_id: 'run-schema-001', trial_id: 'trial-api', span_id: null, parent_span_id: null, purpose: 'contestant', protocol_in: 'responses', protocol_out: 'responses', translated: false, provider: 'openai', account_id: 'acct-hash-02', model_asked: 'gpt-5', model_served: 'gpt-5', swapped: false, upstream: null, effort_asked: null, effort_applied: null, tries: [{ account_id: 'acct-hash-02', status: 200, error_class: null, rest_ms: 0, ms: 430 }], status: 200, error_class: null, tokens: { in: 840, out: 210, reasoning: 40, cache_read: 0, cache_write: 0 }, cost_usd: 0.0042, price_version: 'catalog-v1', queue_ms: 12, ttft_ms: 150, first_text_ms: 160, total_ms: 430, cache: 'miss', prompt_parts: [{ kind: 'conversation', tokens: 840 }] },
  ],
  lanes: () => ({ 'anthropic/main': { in_flight: 1, queued: 2, concurrency: 4, resting_until: null, rest_class: null }, 'openai/main': { in_flight: 0, queued: 0, concurrency: 2, resting_until: Date.now() / 1000 + 780, rest_class: 'rate_limit' } }),
  usage: () => [{ label: '10:00', tokens: 420 }, { label: '10:05', tokens: 760 }, { label: '10:10', tokens: 690 }, { label: '10:15', tokens: 1150 }].map((point, i) => ({ ...point, latencyMs: [430, 812, 560, 640][i]! })),
  meters: () => [{ account: 'acct-hash-01', model: 'claude-opus-5-5', label: 'weekly usage', used: 412, limit: 1000, resetsAt: new Date(Date.now() + 46 * 3600_000).toISOString() }],
  hooks: () => [{ hook: 'audit-request', calls: 120, failures: 2, averageUs: 184 }],
};
