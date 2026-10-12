export type Protocol = 'responses' | 'chat' | 'anthropic';
export const protocols: Protocol[] = ['responses', 'chat', 'anthropic'];
export interface ProviderKey extends Record<string, unknown> { id: string; env: string }
export interface ProviderConfig extends Record<string, unknown> {
  id: string;
  enabled?: boolean;
  apis?: Record<string, string>;
  keys?: ProviderKey[];
  subscription?: (Record<string, unknown> & { vendor: string; plan: string; runtime?: string }) | null;
  limits?: Record<string, unknown>;
}
export interface CatalogModel extends Record<string, unknown> {
  ref: string;
  protocols: Protocol[];
  pricing?: 'subscription';
  price_per_mtok?: Record<string, number> | null;
  source?: string;
}
export interface CredentialStatus { id: string; env: string; configured: boolean; source: string | null }
export interface ProviderStatus {
  id: string; kind: string; enabled: boolean; runtime: string; vendor: string | null;
  plan?: string; cli_found: boolean | null; binary?: string; version: string | null;
  auth: string | null; sign_in?: string; status: string; detail?: string; hint: string | null;
  key_envs: string[]; config?: ProviderConfig; credentials?: CredentialStatus[]; models?: CatalogModel[];
}
export interface ProvidersSnapshot {
  providers: ProviderStatus[];
  instructions: { subscription: string; api: string; note: string };
  revision?: string; catalog_revision?: string;
  capabilities?: { settings: boolean; launch?: boolean }; settings_home?: string;
}
export interface TestSelection { model?: string; protocol?: Protocol; key_id?: string }
export interface TestTokens { input?: number; output?: number; reasoning?: number; cache_read?: number; cache_write?: number }
export interface TestResult { ok: boolean; status?: string; message?: string; error?: string; tokens?: TestTokens; source?: string; http_status?: number }
export interface LoginStatus { id: string; vendor: string; state: string; url?: string; user_code?: string; message?: string }
export interface DiscoveredModel { id: string; source: string }
export const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown): string | undefined => typeof value === 'string' ? value : undefined;
const nullableText = (value: unknown): string | null => text(value) ?? null;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((v: unknown) => typeof v === 'string');
const invalid = (name: string): never => { throw new Error(`The ${name} response has an invalid shape.`); };
export function safeMessage(value: string): string {
  return value.replace(/(?:sk-|sess-|Bearer\s+)[A-Za-z0-9_.-]+/gi, '[redacted]');
}
export class ProviderApiError extends Error {
  constructor(readonly status: number, message: string) { super(message); }
}
async function request(path: string, init: RequestInit, fetcher: typeof fetch): Promise<Record<string, unknown>> {
  const response = await fetcher(path, { ...init, credentials: 'same-origin', redirect: 'error', cache: 'no-store' });
  let body: unknown;
  try { body = await response.json(); } catch { throw new ProviderApiError(response.status, `Provider request failed (${response.status}): invalid JSON response.`); }
  if (!response.ok) {
    const message = isRecord(body) ? text(body.error) ?? text(body.message) : undefined;
    throw new ProviderApiError(response.status, response.status === 409
      ? 'Settings changed elsewhere. Refresh providers and reopen the form before saving again.'
      : safeMessage(message ?? `Provider request failed (${response.status}).`));
  }
  if (!isRecord(body)) return invalid('provider');
  return body;
}
function parseConfig(value: unknown): ProviderConfig {
  if (!isRecord(value) || typeof value.id !== 'string') return invalid('provider config');
  const apis = value.apis;
  if (apis !== undefined && (!isRecord(apis) || !Object.values(apis).every((v) => typeof v === 'string'))) return invalid('provider config');
  const endpoints: Record<string, string> = {};
  if (isRecord(apis)) for (const [key, endpoint] of Object.entries(apis)) if (typeof endpoint === 'string') endpoints[key] = endpoint;
  if (value.keys !== undefined && !Array.isArray(value.keys)) return invalid('provider config');
  const keys: ProviderKey[] = Array.isArray(value.keys) ? value.keys.map((key: unknown) => {
    if (!isRecord(key) || typeof key.id !== 'string' || typeof key.env !== 'string') return invalid('provider key');
    return { ...key, id: key.id, env: key.env };
  }) : [];
  const sub = value.subscription;
  if (sub != null && (!isRecord(sub) || typeof sub.vendor !== 'string' || typeof sub.plan !== 'string')) return invalid('provider subscription');
  return { ...value, id: value.id, enabled: typeof value.enabled === 'boolean' ? value.enabled : true,
    apis: endpoints, keys, limits: isRecord(value.limits) ? value.limits : undefined,
    subscription: isRecord(sub) && typeof sub.vendor === 'string' && typeof sub.plan === 'string'
      ? { ...sub, vendor: sub.vendor, plan: sub.plan, runtime: text(sub.runtime) } : null };
}
function parseModel(value: unknown): CatalogModel {
  if (!isRecord(value) || typeof value.ref !== 'string' || !strings(value.protocols)) return invalid('model');
  const declared = value.protocols;
  if (!declared.every((p) => protocols.some((known) => p === known))) return invalid('model');
  const price = value.price_per_mtok;
  const prices: Record<string, number> = {};
  if (price != null) {
    if (!isRecord(price)) return invalid('model pricing');
    for (const [key, amount] of Object.entries(price)) {
      if (typeof amount !== 'number' || !Number.isFinite(amount) || amount < 0) return invalid('model pricing');
      prices[key] = amount;
    }
  }
  return { ...value, ref: value.ref, protocols: protocols.filter((p) => declared.includes(p)),
    pricing: value.pricing === 'subscription' ? 'subscription' : undefined,
    price_per_mtok: price == null ? null : prices, source: text(value.source) };
}
export async function fetchProviders(fetcher: typeof fetch = (...args) => globalThis.fetch(...args)): Promise<ProvidersSnapshot> {
  const body = await request('/api/providers', {}, fetcher);
  if (!Array.isArray(body.providers) || !isRecord(body.instructions)) return invalid('provider');
  const instructions = body.instructions;
  if (typeof instructions.subscription !== 'string' || typeof instructions.api !== 'string' || typeof instructions.note !== 'string') return invalid('provider');
  const providers = body.providers.map((value: unknown): ProviderStatus => {
    if (!isRecord(value) || typeof value.id !== 'string' || typeof value.kind !== 'string'
      || typeof value.enabled !== 'boolean' || typeof value.runtime !== 'string' || typeof value.status !== 'string'
      || !strings(value.key_envs)) return invalid('provider');
    if (value.models !== undefined && !Array.isArray(value.models)) return invalid('model');
    if (value.credentials !== undefined && !Array.isArray(value.credentials)) return invalid('credentials');
    const credentials = Array.isArray(value.credentials) ? value.credentials.map((c: unknown): CredentialStatus => {
      if (!isRecord(c) || typeof c.id !== 'string' || typeof c.env !== 'string' || typeof c.configured !== 'boolean') return invalid('credentials');
      return { id: c.id, env: c.env, configured: c.configured, source: nullableText(c.source) };
    }) : undefined;
    return { id: value.id, kind: value.kind, enabled: value.enabled, runtime: value.runtime, status: value.status,
      vendor: nullableText(value.vendor), plan: text(value.plan), cli_found: typeof value.cli_found === 'boolean' ? value.cli_found : null,
      version: nullableText(value.version), auth: nullableText(value.auth), sign_in: text(value.sign_in),
      detail: text(value.detail), hint: nullableText(value.hint), key_envs: value.key_envs,
      config: value.config === undefined ? undefined : parseConfig(value.config), credentials,
      models: Array.isArray(value.models) ? value.models.map(parseModel) : undefined };
  });
  const settings = isRecord(body.capabilities) && body.capabilities.settings === true;
  if (settings && (typeof body.revision !== 'string' || typeof body.catalog_revision !== 'string' || typeof body.settings_home !== 'string')) return invalid('settings');
  return { providers, instructions: { subscription: instructions.subscription, api: instructions.api, note: instructions.note },
    capabilities: body.capabilities === undefined ? undefined : { settings, launch: isRecord(body.capabilities) && body.capabilities.launch === true }, revision: text(body.revision), catalog_revision: text(body.catalog_revision), settings_home: text(body.settings_home) };
}
export async function fetchSettingsToken(): Promise<string> {
  const body = await request('/api/provider-settings', {}, globalThis.fetch);
  if (typeof body.csrf_token !== 'string' || !body.csrf_token) return invalid('settings');
  return body.csrf_token;
}
export async function mutateSettings(path: string, body: Record<string, unknown>, csrf: string): Promise<Record<string, unknown>> {
  if (!csrf) throw new Error('Local settings access is unavailable. Refresh providers.');
  return request(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Arena-CSRF': csrf }, body: JSON.stringify(body) }, globalThis.fetch);
}
export async function testProvider(id: string, selection?: TestSelection, csrf?: string): Promise<TestResult> {
  const body = await request(`/api/providers/${encodeURIComponent(id)}/test`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-Arena-CSRF': csrf } : {}) }, body: JSON.stringify(selection ?? {}),
  }, globalThis.fetch);
  if (typeof body.ok !== 'boolean') return invalid('provider test');
  return { ok: body.ok, message: text(body.message), status: text(body.status), error: text(body.error), tokens: parseTokens(body.tokens), source: text(body.source), http_status: count(body.http_status) };
}
const tokenKeys = { in: 'input', out: 'output', reasoning: 'reasoning', cache_read: 'cache_read', cache_write: 'cache_write' } as const;
function count(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}
function parseTokens(value: unknown): TestTokens | undefined {
  if (!isRecord(value)) return undefined;
  const tokens: TestTokens = {};
  for (const [wire, key] of Object.entries(tokenKeys)) {
    const n = count(value[wire]);
    if (n !== undefined) tokens[key] = n;
  }
  return Object.keys(tokens).length ? tokens : undefined;
}
export function parseLogin(body: Record<string, unknown>): LoginStatus {
  if (typeof body.id !== 'string' || typeof body.vendor !== 'string' || typeof body.state !== 'string') return invalid('account login');
  return { id: body.id, vendor: body.vendor, state: body.state, url: text(body.url), user_code: text(body.user_code), message: text(body.message) };
}
export async function fetchLogin(id: string): Promise<LoginStatus> {
  return parseLogin(await request(`/api/account-logins/${encodeURIComponent(id)}`, {}, globalThis.fetch));
}
export function parseDiscovery(body: Record<string, unknown>): { models: DiscoveredModel[]; message?: string } {
  if (!Array.isArray(body.models)) return invalid('model discovery');
  return { models: body.models.map((model: unknown) => {
    if (!isRecord(model) || typeof model.id !== 'string' || typeof model.source !== 'string') return invalid('model discovery');
    return { id: model.id, source: model.source };
  }), message: text(body.message) };
}
export interface LaunchContestant { label: string; model: string; runnable: boolean; kind: string; reason: string }
export interface LaunchSet { name: string; contestants: LaunchContestant[]; error?: string }
export interface LaunchModel { ref: string; provider: string; vendor: string; enabled: boolean }
export interface LaunchOptions {
  suites: { id: string; tasks: number; kinds: string[] }[]; sets: LaunchSet[]; models: LaunchModel[];
  docker_available: boolean; limits: { max_repeats: number; max_trials: number }; active_job: string | null;
}
export interface LaunchPlan {
  suite: string; set: string; repeats: number; tasks: number; contestants: number; trials: number;
  subscription_trials: number; scorers: string[]; startable: boolean; blockers: string[];
}
export interface LaunchJob {
  id: string; suite: string; set: string; repeats: number; trials: number; score_execution: boolean;
  state: string; run_id: string | null; started_at: number; finished_at: number | null; exit_code: number | null;
  message: string; log_tail?: string;
}
const list = <T,>(value: unknown, parse: (item: Record<string, unknown>) => T, name: string): T[] => {
  if (!Array.isArray(value)) return invalid(name);
  return value.map((item: unknown) => isRecord(item) ? parse(item) : invalid(name));
};
export function parseLaunchOptions(body: Record<string, unknown>): LaunchOptions {
  const limits = body.limits;
  if (!isRecord(limits) || typeof limits.max_repeats !== 'number' || typeof limits.max_trials !== 'number' || typeof body.docker_available !== 'boolean') return invalid('run options');
  return {
    suites: list(body.suites, (s) => {
      if (typeof s.id !== 'string' || typeof s.tasks !== 'number' || !strings(s.kinds)) return invalid('run options');
      return { id: s.id, tasks: s.tasks, kinds: s.kinds };
    }, 'run options'),
    sets: list(body.sets, (s) => {
      if (typeof s.name !== 'string') return invalid('run options');
      return { name: s.name, error: text(s.error), contestants: list(s.contestants, (c) => {
        if (typeof c.label !== 'string' || typeof c.model !== 'string' || typeof c.runnable !== 'boolean') return invalid('run options');
        return { label: c.label, model: c.model, runnable: c.runnable, kind: text(c.kind) ?? '', reason: text(c.reason) ?? '' };
      }, 'run options') };
    }, 'run options'),
    models: list(body.models, (m) => {
      if (typeof m.ref !== 'string' || typeof m.enabled !== 'boolean') return invalid('run options');
      return { ref: m.ref, provider: text(m.provider) ?? '', vendor: text(m.vendor) ?? '', enabled: m.enabled };
    }, 'run options'),
    docker_available: body.docker_available, limits: { max_repeats: limits.max_repeats, max_trials: limits.max_trials },
    active_job: nullableText(body.active_job),
  };
}
export function parseLaunchPlan(body: Record<string, unknown>): LaunchPlan {
  if (typeof body.suite !== 'string' || typeof body.set !== 'string' || typeof body.trials !== 'number' || typeof body.startable !== 'boolean' || !strings(body.blockers) || !strings(body.scorers)) return invalid('run plan');
  return { suite: body.suite, set: body.set, repeats: Number(body.repeats), tasks: Number(body.tasks), contestants: Number(body.contestants), trials: body.trials,
    subscription_trials: Number(body.subscription_trials), scorers: body.scorers, startable: body.startable, blockers: body.blockers };
}
export function parseLaunchJob(body: Record<string, unknown>): LaunchJob {
  if (typeof body.id !== 'string' || typeof body.state !== 'string' || typeof body.suite !== 'string' || typeof body.set !== 'string') return invalid('run');
  return { id: body.id, suite: body.suite, set: body.set, repeats: Number(body.repeats), trials: Number(body.trials), score_execution: body.score_execution === true,
    state: body.state, run_id: nullableText(body.run_id), started_at: Number(body.started_at), finished_at: typeof body.finished_at === 'number' ? body.finished_at : null,
    exit_code: typeof body.exit_code === 'number' ? body.exit_code : null, message: text(body.message) ?? '', log_tail: text(body.log_tail) };
}
export const launchApi = {
  options: async () => parseLaunchOptions(await request('/api/launch/options', {}, globalThis.fetch)),
  jobs: async () => list((await request('/api/launch/runs', {}, globalThis.fetch)).jobs, parseLaunchJob, 'run'),
  job: async (id: string) => parseLaunchJob(await request(`/api/launch/runs/${encodeURIComponent(id)}`, {}, globalThis.fetch)),
  saveSet: (name: string, models: string[], overwrite: boolean, csrf: string) => mutateSettings('/api/launch/sets', { name, models, overwrite }, csrf),
  plan: async (suite: string, set: string, repeats: number, csrf: string) => parseLaunchPlan(await mutateSettings('/api/launch/plan', { suite, set, repeats }, csrf)),
  start: async (suite: string, set: string, repeats: number, scoreExecution: boolean, csrf: string) =>
    parseLaunchJob(await mutateSettings('/api/launch/runs', { suite, set, repeats, score_execution: scoreExecution }, csrf)),
  cancel: async (id: string, csrf: string) => parseLaunchJob(await mutateSettings(`/api/launch/runs/${encodeURIComponent(id)}/cancel`, {}, csrf)),
  resume: async (id: string, csrf: string) => parseLaunchJob(await mutateSettings(`/api/launch/runs/${encodeURIComponent(id)}/resume`, {}, csrf)),
};
export type LaunchApi = typeof launchApi;
