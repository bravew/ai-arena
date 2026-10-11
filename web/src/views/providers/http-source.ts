export interface ProviderStatus {
  id: string;
  kind: string;
  enabled: boolean;
  runtime: string;
  vendor: string | null;
  plan?: string;
  cli_found: boolean | null;
  binary?: string;
  version: string | null;
  auth: string | null;
  sign_in?: string;
  status: string;
  detail?: string;
  hint: string | null;
  key_envs: string[];
}

export interface ProvidersSnapshot {
  providers: ProviderStatus[];
  instructions: { subscription: string; api: string; note: string };
}

export interface TestResult {
  ok: boolean;
  status?: string;
  message?: string;
  error?: string;
  call_id?: string | null;
  tokens?: Record<string, number> | null;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

export async function fetchProviders(
  fetcher: typeof fetch = (...args) => globalThis.fetch(...args),
): Promise<ProvidersSnapshot> {
  const response = await fetcher('/api/providers', { credentials: 'same-origin' });
  const body: unknown = await response.json();
  if (!response.ok) {
    const detail = isRecord(body) && typeof body.error === 'string' ? `: ${body.error}` : '';
    throw new Error(`loading providers failed (${response.status})${detail}`);
  }
  if (!isRecord(body) || !Array.isArray(body.providers) || !isRecord(body.instructions)) {
    throw new Error('the provider response has an invalid shape');
  }
  return body as unknown as ProvidersSnapshot;
}

export async function testProvider(
  id: string,
  fetcher: typeof fetch = (...args) => globalThis.fetch(...args),
): Promise<TestResult> {
  const response = await fetcher(`/api/providers/${encodeURIComponent(id)}/test`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  const body: unknown = await response.json();
  if (!isRecord(body)) throw new Error('the provider test response has an invalid shape');
  if (!response.ok && typeof body.error === 'string') throw new Error(body.error);
  return body as unknown as TestResult;
}
