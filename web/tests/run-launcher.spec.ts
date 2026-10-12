import { expect, test } from '@playwright/test';

const options = {
  suites: [{ id: 'smoke', tasks: 4, kinds: ['codegen'] }],
  sets: [{ name: 'sets/sonnet-vs-luna', contestants: [
    { label: 'sonnet', model: 'anthropic-max/claude-sonnet-5-5', runnable: true, kind: 'subscription', reason: '' },
    { label: 'luna', model: 'openai-plus/gpt-6-luna', runnable: true, kind: 'subscription', reason: '' }] }],
  models: [
    { ref: 'anthropic-max/claude-sonnet-5-5', provider: 'anthropic-max', vendor: 'claude', enabled: true },
    { ref: 'openai-plus/gpt-6-luna', provider: 'openai-plus', vendor: 'chatgpt', enabled: true }],
  docker_available: false, limits: { max_repeats: 10, max_trials: 200 }, active_job: null,
};
const plan = { suite: 'smoke', set: 'sets/sonnet-vs-luna', repeats: 1, tasks: 4, contestants: 2, trials: 8, subscription_trials: 8, scorers: ['execution'], startable: true, blockers: [] };
const job = { id: '0123456789abcdef', suite: 'smoke', set: 'sets/sonnet-vs-luna', repeats: 1, trials: 8, score_execution: false, state: 'running', run_id: null, started_at: 1, finished_at: null, exit_code: null, message: '' };

test('defines contestants, previews the plan and starts a run only after quota confirmation', async ({ page }) => {
  const posts: { path: string; body: Record<string, unknown>; csrf?: string }[] = [];
  let started = false;
  let saved = false;
  await page.route('**/api/**', async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (request.method() === 'POST') posts.push({ path, body: request.postDataJSON(), csrf: request.headers()['x-arena-csrf'] });
    let body: unknown = {};
    if (path === '/api/providers') body = { providers: [], instructions: { subscription: 's', api: 'a', note: 'n' }, revision: 'r', catalog_revision: 'c', settings_home: '/tmp/arena', capabilities: { settings: true, launch: true } };
    else if (path === '/api/provider-settings') body = { csrf_token: 'fake-csrf' };
    else if (path === '/api/launch/options') body = saved ? { ...options, sets: [...options.sets, { name: 'sets/mine', contestants: [options.sets[0]!.contestants[0]!] }] } : options;
    else if (path === '/api/launch/runs' && request.method() === 'GET') body = { jobs: started ? [job] : [] };
    else if (path === '/api/launch/runs') { started = true; body = job; }
    else if (path === '/api/launch/plan') body = plan;
    else if (path === '/api/launch/sets') { saved = true; body = { ok: true, name: 'sets/mine' }; }
    else if (path === '/api/runs') body = { runs: [] };
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto('/providers');
  const launcher = page.getByRole('region', { name: 'Run a comparison' });
  await expect(launcher).toBeVisible();

  await launcher.getByLabel('anthropic-max/claude-sonnet-5-5').check();
  await launcher.getByLabel('Set name').fill('mine');
  await launcher.getByRole('button', { name: 'Save contestant set' }).click();
  await expect(launcher.getByText('Saved contestant set sets/mine.')).toBeVisible();
  expect(posts.find((p) => p.path === '/api/launch/sets')).toMatchObject({ csrf: 'fake-csrf', body: { name: 'mine', models: ['anthropic-max/claude-sonnet-5-5'], overwrite: false } });

  await launcher.getByRole('button', { name: 'Preview plan' }).click();
  await expect(launcher.getByText('4 tasks × 2 contestants × 1 repeat = 8 trials')).toBeVisible();
  const start = launcher.getByRole('button', { name: 'Start run' });
  await expect(start).toBeDisabled();
  await launcher.getByLabel(/uses my subscription quota/).check();
  await start.click();
  await expect(launcher.getByRole('button', { name: 'Cancel run' })).toBeVisible();
  expect(posts.find((p) => p.path === '/api/launch/runs')).toMatchObject({ csrf: 'fake-csrf', body: { suite: 'smoke', set: 'sets/mine', repeats: 1, score_execution: false } });
});
