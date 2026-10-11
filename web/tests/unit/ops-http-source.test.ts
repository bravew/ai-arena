import { describe, expect, it, vi } from 'vitest';
import { createHttpOpsSource, fetchOpsSnapshot, type OpsSnapshot } from '../../src/views/ops/http-source';

const call = (id: string, provider: string, model: string, protocol: string) => ({
  id, seq: 1, run_id: 'run-1', trial_id: `trial-${id}`, span_id: null, parent_span_id: null,
  purpose: 'contestant' as const, protocol_in: protocol, protocol_out: protocol,
  translated: false, provider, account_id: 'acct-hash', model_asked: model,
  model_served: model, swapped: false,
  upstream: protocol === 'local-cli' ? 'cli_reported' : null,
  effort_asked: null, effort_applied: null,
  tries: [{ account_id: 'acct-hash', status: 200, error_class: null, rest_ms: 0, ms: 10 }],
  status: 200, error_class: null,
  tokens: { in: 8, out: 2, reasoning: 0, cache_read: 0, cache_write: 0 },
  cost_usd: protocol === 'responses' ? 0.1 : null, price_version: null,
  queue_ms: 0, ttft_ms: 1, first_text_ms: 1, total_ms: 10,
  cache: 'off' as const, prompt_parts: [],
});

const snapshot: OpsSnapshot = {
  calls: [
    call('api', 'openai', 'gpt-6', 'responses'),
    call('gateway', 'anthropic-max', 'claude-opus', 'anthropic'),
    call('cli', 'openai-plus', 'gpt-6.1-sol', 'local-cli'),
  ],
  lanes: {}, usage: [],
  meters: [{ account: 'acct-hash', model: 'gpt-6.1-sol', label: 'arena-counted tokens', used: 10, limit: null, resetsAt: null, source: 'arena-counted' }],
  hooks: [],
  providers: [{ runtime: 'claude', vendor: 'claude', status: 'ok', auth: 'authenticated', binary: '/usr/bin/claude', version: '2.1.296', detail: 'ready', hint: null }],
  accounts: [{ vendor: 'claude', id: 'a1b2c3d4', source: 'import', savedAt: '2026-10-11T00:00:00Z' }],
  sources: { lanes: 'unavailable', meters: 'arena-counted tokens' },
};

const response = (status: number, body: unknown) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

describe('Ops HTTP source', () => {
  it('loads the seeded live snapshot with all call serving modes', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => response(200, snapshot));
    const result = await fetchOpsSnapshot(fetcher);
    const data = createHttpOpsSource(result);
    expect(data.calls().map((item) => item.upstream)).toEqual([null, null, 'cli_reported']);
    expect(data.meters()[0]?.source).toBe('arena-counted');
    expect(result.accounts[0]?.id).toBe('a1b2c3d4');
    expect(result.providers[0]?.status).toBe('ok');
    expect(fetcher).toHaveBeenCalledWith('/api/ops', { credentials: 'same-origin' });
  });

  it('reports authentication and server errors without exposing fallback content', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => response(401, { error: 'sign in required' }));
    await expect(fetchOpsSnapshot(fetcher)).rejects.toThrow('loading Ops data failed (401): sign in required');
  });

  it('rejects a malformed response', async () => {
    await expect(fetchOpsSnapshot(async () => response(200, {}))).rejects.toThrow('invalid shape');
  });
});
