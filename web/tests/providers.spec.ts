import { expect, test } from '@playwright/test';
import type { ProvidersSnapshot } from '../src/views/providers/http-source';

const local: ProvidersSnapshot = {
  revision: 'providers-r1', catalog_revision: 'catalog-r1', settings_home: '/temporary/arena', capabilities: { settings: true },
  instructions: { subscription: 'arena providers add claude', api: 'arena providers add api', note: 'Local settings' },
  providers: [
    { id: 'api', kind: 'api', enabled: true, runtime: 'gateway', vendor: null, cli_found: null, version: null, auth: null, status: 'configured', hint: null, key_envs: ['API_KEY'],
      config: { id: 'api', apis: { responses: 'https://provider.example/v1', chat: 'https://provider.example/v1' }, keys: [{ id: 'main', env: 'API_KEY', weight: 7 }], limits: { concurrency: 3, rpm: 20 }, headers: { 'x-extra': 'preserve' } },
      credentials: [{ id: 'main', env: 'API_KEY', configured: true, source: 'environment' }],
      models: [{ ref: 'api/model-a', protocols: ['responses', 'chat'], price_per_mtok: { in: 1, out: 2, cached_extra: 0.5 }, source: 'https://provider.example/pricing' }] },
    { id: 'plan', kind: 'subscription', enabled: true, runtime: 'local-cli', vendor: 'claude', plan: 'max', cli_found: true, version: '2.1.296', auth: 'unauthenticated', sign_in: 'missing', status: 'auth_required', hint: null, key_envs: [],
      config: { id: 'plan', keys: [], subscription: { vendor: 'claude', plan: 'max', runtime: 'local-cli', import: 'own-sign-in' }, limits: { concurrency: 2 } },
      models: [{ ref: 'plan/model-b', protocols: ['anthropic'], pricing: 'subscription' }] },
  ],
};
type RequestRecord = { path: string; body: Record<string, unknown>; csrf?: string };
async function fakeSettings(page: import('@playwright/test').Page, snapshot = local) {
  const requests: RequestRecord[] = [];
  let loginState = 'awaiting_code';
  let conflict = false;
  await page.route('**/api/**', async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    let body: unknown = {};
    if (request.method() === 'POST') {
      const posted: Record<string, unknown> = request.postDataJSON();
      requests.push({ path, body: posted, csrf: request.headers()['x-arena-csrf'] });
      if (conflict && path.endsWith('/config')) { await route.fulfill({ status: 409, json: { error: 'revision conflict' } }); return; }
    }
    if (path === '/api/providers') body = request.method() === 'GET' ? snapshot : { ok: true };
    else if (path === '/api/provider-settings') body = { csrf_token: 'fake-csrf' };
    else if (path.endsWith('/models/discover')) body = { models: [{ id: 'discovered', source: 'https://provider.example/models' }] };
    else if (path.endsWith('/login') || path.startsWith('/api/account-logins/')) {
      if (path.endsWith('/login')) loginState = 'awaiting_code';
      else if (path.endsWith('/cancel')) loginState = 'cancelled';
      else if (path.endsWith('/code')) loginState = 'succeeded';
      body = { id: 'login-1', vendor: 'claude', state: loginState, url: 'https://signin.example/authorize', user_code: 'FAKE-CODE' };
    } else if (path.endsWith('/test')) body = { ok: true, message: 'Fake explicit test completed.' };
    else if (request.method() === 'POST') body = { ok: true };
    else if (path === '/api/runs') body = { runs: [] };
    await route.fulfill({ status: 200, json: body });
  });
  return { requests, conflict: () => { conflict = true; } };
}

test('providers panel shows detected state and can run a fake CLI connection check', async ({ page }) => {
  await page.route('**/api/providers', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      providers: [{ id: 'claude-plan', kind: 'subscription', enabled: true, runtime: 'local-cli', vendor: 'claude', plan: 'max', cli_found: true, binary: '/fake/claude', version: '2.1.296', auth: 'authenticated', sign_in: 'stored', status: 'ok', detail: 'ready', hint: null, key_envs: [] }],
      instructions: { subscription: 'arena providers add claude|codex', api: 'arena providers add anthropic --env API_KEY', note: 'Name the environment variable only; never paste a key in the viewer.' },
    }),
  }));
  await page.route('**/api/providers/claude-plan/test', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ ok: true, message: 'One capped model call was sent through the vendor CLI and recorded.', source: 'cli_reported', tokens: { in: 2979, out: 5, cache_read: 11008 } }),
  }));
  await page.goto('/providers');
  await expect(page.getByRole('heading', { name: 'Providers', level: 2 })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'claude-plan' })).toBeVisible();
  await expect(page.getByText('Saved in Arena')).toBeVisible();
  await expect(page.getByText(/uses subscription quota/)).toBeVisible();
  await page.getByRole('button', { name: 'Test connection' }).click();
  await expect(page.getByText(/One capped model call was sent/)).toBeVisible();
  await expect(page.getByText('Test passed')).toBeVisible();
  await expect(page.getByText(/2,979 input · 5 output · 11,008 cache read/)).toBeVisible();
});

test('edits providers without dropping extras, creates API/subscription configs, and recovers from conflicts', async ({ page }) => {
  const mock = await fakeSettings(page);
  await page.goto('/providers');
  await page.getByRole('button', { name: 'Edit provider api', exact: true }).click();
  const editor = page.getByRole('form', { name: 'Edit api', exact: true });
  await editor.getByLabel('Concurrency', { exact: true }).fill('6');
  await editor.getByRole('button', { name: 'Save provider', exact: true }).click();
  await expect(editor).not.toBeVisible();
  expect(mock.requests[0]).toEqual({ path: '/api/providers/api/config', csrf: 'fake-csrf', body: { revision: 'providers-r1', provider: { ...local.providers[0]!.config, enabled: true, subscription: null, limits: { concurrency: 6, rpm: 20 } } } });
  await page.getByRole('button', { name: 'Add provider', exact: true }).click();
  const create = page.getByRole('form', { name: 'Add provider', exact: true });
  await create.getByLabel('Provider ID', { exact: true }).fill('new-api');
  await create.getByLabel('chat endpoint', { exact: true }).fill('https://api.example/v1');
  await create.getByLabel('Environment variable 1', { exact: true }).fill('NEW_API_KEY');
  await create.getByRole('button', { name: 'Save provider', exact: true }).click();
  await expect(create).not.toBeVisible();
  expect(mock.requests.at(-1)?.body.provider).toMatchObject({ id: 'new-api', keys: [{ id: 'main', env: 'NEW_API_KEY' }], apis: { chat: 'https://api.example/v1' } });
  await page.getByRole('button', { name: 'Add provider', exact: true }).click();
  await create.getByLabel('Provider ID', { exact: true }).fill('new-plan');
  await create.getByRole('radio', { name: 'subscription', exact: true }).check();
  await create.getByRole('radio', { name: 'chatgpt', exact: true }).check();
  await create.getByLabel('Plan', { exact: true }).fill('plus');
  await create.getByRole('button', { name: 'Save provider', exact: true }).click();
  await expect(create).not.toBeVisible();
  expect(mock.requests.at(-1)?.body.provider).toMatchObject({ id: 'new-plan', keys: [], subscription: { vendor: 'chatgpt', plan: 'plus', runtime: 'local-cli' } });
  mock.conflict();
  await page.getByRole('button', { name: 'Edit provider api', exact: true }).click();
  await editor.getByRole('button', { name: 'Save provider', exact: true }).click();
  await expect(editor.getByRole('alert')).toContainText('Refresh providers');
  await expect(editor).toBeVisible();
});

test('write-only keys clear after submit and forgetting removes only the Arena copy', async ({ page }) => {
  const mock = await fakeSettings(page);
  await page.goto('/providers');
  const card = page.getByRole('article', { name: 'api provider', exact: true });
  await card.getByLabel('Key value for main', { exact: true }).fill('fake-key-value-for-test');
  await card.getByRole('button', { name: 'Save key', exact: true }).click();
  await expect(card.getByLabel('Key value for main')).toHaveValue('');
  await expect(card.getByText('Key saved.', { exact: true })).toBeVisible();
  expect(mock.requests[0]).toEqual({ path: '/api/providers/api/keys/main/save', csrf: 'fake-csrf', body: { value: 'fake-key-value-for-test' } });
  await card.getByRole('button', { name: 'Forget Arena key copy', exact: true }).click();
  await expect(card.getByText('Arena key copy forgotten.', { exact: true })).toBeVisible();
  expect(mock.requests.at(-1)?.body).toEqual({});
  const storage = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage }, text: document.body.innerText }));
  expect(JSON.stringify(storage)).not.toContain('fake-key-value-for-test');
});

test('discovery requires review, explicit API prices and a source; model test is explicit', async ({ page }) => {
  const mock = await fakeSettings(page);
  await page.goto('/providers');
  const card = page.getByRole('article', { name: 'api provider', exact: true });
  await card.getByRole('button', { name: 'Discover models', exact: true }).click();
  await expect(card.getByRole('button', { name: 'Review discovered', exact: true })).toBeVisible();
  expect(mock.requests).toHaveLength(1);
  await card.getByRole('button', { name: 'Review discovered', exact: true }).click();
  const form = card.getByRole('form', { name: 'Add model', exact: true });
  await expect(form.getByLabel('Input price per million tokens', { exact: true })).toHaveValue('');
  await form.getByLabel('Input price per million tokens', { exact: true }).fill('3.5');
  await form.getByLabel('Output price per million tokens', { exact: true }).fill('7');
  await form.getByRole('button', { name: 'Save model', exact: true }).click();
  await expect(form).not.toBeVisible();
  expect(mock.requests.at(-1)).toEqual({ path: '/api/providers/api/models', csrf: 'fake-csrf', body: { revision: 'catalog-r1', model: { ref: 'api/discovered', protocols: ['responses', 'chat'], price_per_mtok: { in: 3.5, out: 7 }, source: 'https://provider.example/models' } } });
  expect(mock.requests.some((r) => r.path.endsWith('/test'))).toBe(false);
  const connection = card.getByRole('region', { name: 'api connection test', exact: true });
  await connection.getByRole('radio', { name: 'api/model-a', exact: true }).check();
  await connection.getByRole('radio', { name: 'responses', exact: true }).check();
  await connection.getByRole('radio', { name: 'main', exact: true }).check();
  await connection.getByRole('button', { name: 'Test connection', exact: true }).click();
  await expect(card.getByText('Fake explicit test completed.')).toBeVisible();
  expect(mock.requests.at(-1)?.body).toEqual({ model: 'api/model-a', protocol: 'responses', key_id: 'main' });
  await card.getByRole('button', { name: 'Edit model api/model-a', exact: true }).click();
  const edit = card.getByRole('form', { name: 'Edit model api/model-a', exact: true });
  await edit.getByLabel('Input price per million tokens', { exact: true }).fill('8');
  await edit.getByRole('button', { name: 'Save model', exact: true }).click();
  await expect(edit).not.toBeVisible();
  expect(mock.requests.at(-1)?.body.model).toMatchObject({ price_per_mtok: { in: 8, out: 2, cached_extra: 0.5 } });
});

test('an existing local subscription sign-in needs no OAuth step, key or import', async ({ page }) => {
  const signedIn: ProvidersSnapshot = {
    ...local,
    providers: local.providers.map((p) => p.id === 'plan' ? { ...p, auth: 'authenticated', sign_in: 'cli_discovered', status: 'ok' } : p),
  };
  const mock = await fakeSettings(page, signedIn);
  await page.goto('/providers');
  const card = page.getByRole('article', { name: 'plan provider', exact: true });
  await expect(card.getByText(/already on this computer\. No OAuth step or API key is needed/)).toBeVisible();
  await expect(card.getByRole('button', { name: 'Sign in', exact: true })).toBeHidden();
  await expect(card.getByRole('button', { name: 'Import macOS Keychain', exact: true })).toBeHidden();
  await card.getByText('Use a different account').click();
  await expect(card.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  expect(mock.requests).toEqual([]);
});

test('subscription import, sign-in polling, code completion, cancellation and disconnect use safe account routes', async ({ page }) => {
  const mock = await fakeSettings(page);
  await page.goto('/providers');
  const card = page.getByRole('article', { name: 'plan provider', exact: true });
  await card.getByRole('button', { name: 'Connect existing', exact: true }).click();
  await expect(card.getByText('Arena account copy updated.', { exact: true })).toBeVisible();
  expect(mock.requests.at(-1)).toEqual({ path: '/api/accounts/claude/import', csrf: 'fake-csrf', body: { keychain: false } });
  await card.getByRole('button', { name: 'Import macOS Keychain', exact: true }).click();
  await expect.poll(() => mock.requests.at(-1)?.body).toEqual({ keychain: true });
  await expect(card.getByRole('button', { name: 'Sign in', exact: true })).toBeEnabled();
  await card.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(card.getByRole('link', { name: 'Open vendor sign-in' })).toHaveAttribute('href', 'https://signin.example/authorize');
  await expect(card.getByText('FAKE-CODE', { exact: true })).toBeVisible();
  await card.getByLabel('Sign-in code', { exact: true }).fill('fake-authorization-code');
  await card.getByRole('button', { name: 'Submit code', exact: true }).click();
  await expect(card.getByText('Sign-in: succeeded', { exact: true })).toBeVisible();
  // A browser sign-in that stays pending offers a cancel route; the poll must not mask it.
  const pending = { id: 'login-2', vendor: 'claude', state: 'awaiting_browser', url: 'https://signin.example/authorize' };
  await page.route('**/api/accounts/claude/login', (route) => route.fulfill({ json: pending }));
  await page.route('**/api/account-logins/login-2', (route) => route.fulfill({ json: pending }));
  await card.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(card.getByText('Sign-in: awaiting browser', { exact: true })).toBeVisible();
  await card.getByRole('button', { name: 'Cancel sign-in', exact: true }).click();
  await expect(card.getByText('Sign-in: cancelled', { exact: true })).toBeVisible();
  await card.getByRole('button', { name: 'Disconnect Arena copy', exact: true }).click();
  await expect.poll(() => mock.requests.at(-1)?.path).toBe('/api/accounts/claude/forget');
  await card.getByRole('button', { name: 'Add model manually', exact: true }).click();
  const form = card.getByRole('form', { name: 'Add model', exact: true });
  await form.getByLabel('Model ref', { exact: true }).fill('plan/new-model');
  await form.getByRole('checkbox', { name: 'anthropic', exact: true }).check();
  await form.getByRole('button', { name: 'Save model', exact: true }).click();
  await expect(form).not.toBeVisible();
  expect(mock.requests.at(-1)?.body.model).toEqual({ ref: 'plan/new-model', protocols: ['anthropic'], pricing: 'subscription' });
});

test('remote settings capability false hides mutations but retains explicit connection testing', async ({ page }) => {
  const mock = await fakeSettings(page, { ...local, capabilities: { settings: false } });
  await page.goto('/providers');
  await expect(page.getByRole('heading', { name: 'Provider settings are read-only' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add provider', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Key value for main')).toHaveCount(0);
  await page.getByRole('article', { name: 'api provider', exact: true }).getByRole('button', { name: 'Test connection', exact: true }).click();
  await expect.poll(() => mock.requests.length).toBe(1);
  expect(mock.requests[0]?.path).toBe('/api/providers/api/test');
});
