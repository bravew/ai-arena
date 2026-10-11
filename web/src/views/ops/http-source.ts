import type { Call } from '../../lib/schema';
import type { HookStat, LaneStatus, OpsDataSource, SubscriptionMeter, UsagePoint } from './fixtures';

export interface OpsAccount { vendor: string; id: string; source: string; savedAt: string }
export interface OpsProvider {
  runtime: string;
  vendor: string;
  status: string;
  auth: string;
  binary: string | null;
  version: string | null;
  detail: string;
  hint: string | null;
}
export interface OpsSnapshot {
  calls: Call[];
  lanes: Record<string, LaneStatus>;
  usage: UsagePoint[];
  meters: SubscriptionMeter[];
  hooks: HookStat[];
  providers: OpsProvider[];
  accounts: OpsAccount[];
  sources: Record<string, string>;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

function parseSnapshot(value: unknown): OpsSnapshot {
  if (!isRecord(value) || !Array.isArray(value.calls) || !Array.isArray(value.usage)
      || !Array.isArray(value.meters) || !Array.isArray(value.hooks)
      || !Array.isArray(value.providers) || !Array.isArray(value.accounts)
      || !isRecord(value.lanes) || !isRecord(value.sources)) {
    throw new Error('the Ops response has an invalid shape');
  }
  return value as unknown as OpsSnapshot;
}

export async function fetchOpsSnapshot(
  fetcher: typeof fetch = (...args) => globalThis.fetch(...args),
): Promise<OpsSnapshot> {
  const response = await fetcher('/api/ops', { credentials: 'same-origin' });
  if (!response.ok) {
    let detail = '';
    try {
      const body: unknown = await response.json();
      if (isRecord(body) && typeof body.error === 'string') detail = `: ${body.error}`;
    } catch { /* Keep the HTTP status when the response is not JSON. */ }
    throw new Error(`loading Ops data failed (${response.status})${detail}`);
  }
  return parseSnapshot(await response.json());
}

export function createHttpOpsSource(snapshot: OpsSnapshot): OpsDataSource {
  return {
    calls: () => snapshot.calls,
    lanes: () => snapshot.lanes,
    usage: () => snapshot.usage,
    meters: () => snapshot.meters,
    hooks: () => snapshot.hooks,
  };
}
