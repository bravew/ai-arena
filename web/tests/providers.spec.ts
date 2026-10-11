import { expect, test } from '@playwright/test';

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
    body: JSON.stringify({ ok: true, message: 'One capped model call was sent through the vendor CLI and recorded.', source: 'cli_reported', tokens: { in: 3, out: 1 } }),
  }));
  await page.goto('/providers');
  await expect(page.getByRole('heading', { name: 'Providers', level: 2 })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'claude-plan' })).toBeVisible();
  await expect(page.getByText('Saved in Arena')).toBeVisible();
  await expect(page.getByText('This sends one capped model request.')).toBeVisible();
  await page.getByRole('button', { name: 'Test connection' }).click();
  await expect(page.getByText(/One capped model call was sent/)).toBeVisible();
});
