import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { spawn, type ChildProcess } from 'node:child_process';
import net from 'node:net';
import { resolve, join } from 'node:path';
import { expect, test } from '@playwright/test';
import type { Bundle, RunEvent } from '../src/lib/schema';

const fixtureEvents = readFileSync(resolve(process.cwd(), '../fixtures/events/events.jsonl'), 'utf8').trim().split(/\r?\n/);
const bundle = JSON.parse(readFileSync(resolve(process.cwd(), '../fixtures/bundles/schema-example.json'), 'utf8')) as Bundle & { provenance: { origin: string; verification: string; importer: string | null; importer_version: string | null; source_ref: string | null } };

async function freePort(): Promise<number> {
  const server = net.createServer();
  await new Promise<void>((resolveListen, reject) => server.listen(0, '127.0.0.1', resolveListen).once('error', reject));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('could not allocate port');
  await new Promise<void>((resolveClose, reject) => server.close((error) => error ? reject(error) : resolveClose()));
  return address.port;
}

async function waitForHealth(url: string, process: ChildProcess): Promise<void> {
  const deadline = Date.now() + 30_000;
  let stderr = '';
  process.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString(); });
  while (Date.now() < deadline) {
    if (process.exitCode !== null) throw new Error(`arena serve exited with code ${process.exitCode}: ${stderr}`);
    try {
      const response = await fetch(`${url}/api/health`);
      if (response.ok) return;
    } catch { /* Server has not started yet. */ }
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
  }
  throw new Error('arena serve did not become healthy');
}

test('fixture replay starts empty and applies only states present in replayed events', async ({ page }) => {
  await page.goto('/live');
  await expect(page.getByText(`0 / ${fixtureEvents.length} events`)).toBeVisible();
  await page.getByRole('button', { name: 'Play replay' }).click();
  await expect(page.getByText(`${fixtureEvents.length} / ${fixtureEvents.length} events`)).toBeVisible({ timeout: 10000 });
  await expect(page.locator('.live-summary > div').nth(0)).toContainText(`1 / ${bundle.trials.length}`);
  await expect(page.locator('.live-summary > div').nth(1)).toContainText(`0 / ${bundle.calls.length}`);
});

test('final ledger score is not shown before its score event is replayed', async ({ page }) => {
  await page.goto('/live');
  const initialScoreCells = page.getByRole('table', { name: 'Race chart facts' }).getByRole('row').locator('td').nth(2);
  await expect(initialScoreCells).toContainText('—');
});

test('static export loads its embedded bundle and replays its event log from file://', async ({ page }) => {
  const exportedBundle = { ...bundle, run: { ...bundle.run, id: 'exported-static-run' } };
  const exportedEvents = [
    { event_version: 1, seq: 1, ts: '2026-01-01T00:00:00Z', run_id: 'exported-static-run', kind: 'run_started', data: {} },
    { event_version: 1, seq: 2, ts: '2026-01-01T00:00:01Z', run_id: 'exported-static-run', kind: 'run_finished', data: { status: 'succeeded' } },
  ];
  const dist = resolve(process.cwd(), 'dist');
  let html = readFileSync(resolve(dist, 'index.html'), 'utf8');
  html = html.replace(
    /<script type="module" crossorigin src="\.\/(.+?)"><\/script>/,
    (_tag, asset: string) => {
      const code = readFileSync(resolve(dist, asset), 'utf8').replaceAll('</script', '<\\/script');
      return `<script type="module">${code}</script>`;
    },
  );
  html = html.replace(
    /<link rel="stylesheet" crossorigin href="\.\/(.+?)">/,
    (_tag, asset: string) => `<style>${readFileSync(resolve(dist, asset), 'utf8')}</style>`,
  );
  const data = JSON.stringify({ input: exportedBundle, events: exportedEvents }).replaceAll(
    '<',
    '\\u003c',
  );
  html = html.replace(
    '</body>',
    `<script id="arena-static-data" type="application/json">${data}</script></body>`,
  );
  const exportDir = resolve(process.cwd(), 'test-results', 'arena-static-export');
  mkdirSync(exportDir, { recursive: true });
  writeFileSync(resolve(exportDir, 'index.html'), html);

  await page.goto(`file://${resolve(exportDir, 'index.html')}#/live`);
  await expect(page.getByText('exported-static-run')).toBeVisible();
  await page.getByRole('button', { name: 'Play replay' }).click();
  await expect(page.getByText('2 / 2 events')).toBeVisible({ timeout: 10000 });
  await expect(page.locator('.live-caption')).toContainText('Run succeeded.');
});

test('live transport rejects an invalid event batch', async ({ page }) => {
  await page.route('**/api/runs/*/events?*', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ ...JSON.parse(fixtureEvents[0]!), seq: 'bad' }]) });
  });
  await page.goto('/live?source=https%3A%2F%2Farena.example');
  await page.getByRole('button', { name: 'Connect live' }).click();
  await expect(page.getByRole('alert')).toContainText('Invalid live event batch');
});

test('live batches survive a late initial read and replay resets the cursor', async ({ page }) => {
  let releaseInitial!: () => void;
  const initial = new Promise<void>((resolveInitial) => { releaseInitial = resolveInitial; });
  await page.route('**/api/runs/*/events?*', async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('wait') === '0') {
      await initial;
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([JSON.parse(fixtureEvents[0]!)]) });
    } else {
      await new Promise((resolveWait) => setTimeout(resolveWait, 100));
      const events = url.searchParams.get('after') === '0' ? fixtureEvents.slice(0, 2).map((line) => JSON.parse(line)) : [];
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(events) });
    }
  });
  await page.goto('/live?source=https%3A%2F%2Farena.example');
  await page.getByRole('button', { name: 'Connect live' }).click();
  await expect(page.locator('.live-caption')).toHaveText('Trial trial-kit queued.');
  const initialResponse = page.waitForResponse((response) => new URL(response.url()).searchParams.get('wait') === '0');
  releaseInitial();
  await initialResponse;
  await expect(page.locator('.live-summary > div').nth(3)).toContainText('2 / 2');
  await page.getByRole('button', { name: 'Replay', exact: true }).click();
  await expect(page.locator('.live-caption')).toHaveText('Waiting for run events.');
  await expect(page.getByText(`0 / ${fixtureEvents.length} events`)).toBeVisible();
});

test('live view exposes the stage, race chart, trial board, replay and accessible event caption', async ({ page }) => {
  await page.goto('/live');
  await expect(page.getByRole('heading', { name: 'Live run', level: 2 })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Animated stage' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Race chart' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Trial board' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Play replay' })).toBeVisible();
  await expect(page.locator('[aria-live="polite"]')).toBeVisible();
  await expect(page.getByRole('table', { name: 'Trial board facts' })).toBeVisible();
});

test('live animation stops while hidden and resumes when visible', async ({ page }) => {
  await page.goto('/live');
  await page.getByRole('button', { name: 'Play replay' }).click();
  await page.evaluate(() => Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }));
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForTimeout(100);
  expect(await page.locator('[data-frame-count]').getAttribute('data-frame-count')).toBe('0');
  await page.evaluate(() => Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }));
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(async () => Number(await page.locator('[data-frame-count]').getAttribute('data-frame-count'))).toBeGreaterThan(0);
});

test('Live reports p95 frame cadence with 200 calls in flight', async ({ page }) => {
  const events: RunEvent[] = Array.from({ length: 200 }, (_, index) => ({
    event_version: 1,
    seq: index + 1,
    ts: '2026-10-08T10:00:01Z',
    run_id: bundle.run.id,
    kind: 'call_try',
    ref: `call-${index + 1}`,
    data: { trial_id: bundle.trials[0]!.id },
  }));
  await page.route('**/api/runs/*/events?*', async (route) => {
    const after = Number(new URL(route.request().url()).searchParams.get('after'));
    if (after > 0) await new Promise((resolveWait) => setTimeout(resolveWait, 100));
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(after === 0 ? events : []) });
  });
  await page.goto('/live?source=https%3A%2F%2Farena.example');
  await page.getByRole('button', { name: 'Connect live' }).click();
  await expect(page.locator('.live-summary > div').nth(3)).toContainText('200 / 200');
  await page.getByRole('button', { name: 'Play replay' }).click();
  const { p95, sampleCount } = await page.evaluate(async () => {
    const samples: number[] = [];
    let previous = performance.now();
    for (let frame = 0; frame < 240; frame += 1) {
      await new Promise<void>((resolveFrame) => requestAnimationFrame(() => {
        const now = performance.now();
        samples.push(now - previous);
        previous = now;
        resolveFrame();
      }));
    }
    const ordered = samples.slice(1).sort((left, right) => left - right);
    return { p95: ordered[Math.ceil(ordered.length * 0.95) - 1]!, sampleCount: ordered.length };
  });
  expect(sampleCount).toBe(239);
  expect(p95).toBeGreaterThan(0);
  await expect(page.locator('.live-summary > div').nth(1)).toContainText('200 /');
  console.log(`${test.info().project.name}: 200 calls in flight, p95 ${p95.toFixed(2)} ms (${sampleCount} frame intervals)`);
  test.info().annotations.push({ type: 'frame-p95-ms', description: `${p95.toFixed(2)} across ${sampleCount} samples` });
});

test('Live receives events from a real loopback arena serve long-poll', async ({ page }) => {
  const home = process.env.ARENA_TESTENV_HOME;
  if (!home) throw new Error('Playwright temporary HOME is unavailable');
  const apiPort = await freePort();
  const artifactPort = await freePort();
  const arenaHome = join(home, `live-server-${apiPort}`);
  const eventsPath = join(arenaHome, 'runs', bundle.run.id, 'events.jsonl');
  mkdirSync(join(arenaHome, 'runs', bundle.run.id), { recursive: true });
  writeFileSync(eventsPath, fixtureEvents.slice(0, 1).join('\n') + '\n');
  const repoRoot = resolve(process.cwd(), '..');
  const server = spawn('uv', ['run', 'arena', 'serve', '--host', '127.0.0.1', '--port', String(apiPort), '--artifact-port', String(artifactPort), '--home', arenaHome], {
    cwd: repoRoot,
    env: { ...process.env, ARENA_RUN_KEY: undefined, ARENA_TRUSTED_PROXY: undefined },
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  const origin = `http://127.0.0.1:${apiPort}`;
  try {
    await waitForHealth(origin, server);
    await page.goto(origin);
    await page.getByRole('navigation').getByRole('link', { name: 'Live' }).click();
    await page.evaluate((source) => window.history.replaceState(null, '', `/live?source=${encodeURIComponent(source)}`), origin);
    await expect(page.locator('[aria-live="polite"]')).toHaveText('Waiting for run events.');
    const polling = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return url.pathname.endsWith('/events') && url.searchParams.get('after') === '1';
    });
    await page.getByRole('button', { name: 'Connect live' }).click();
    await polling;
    appendFileSync(eventsPath, fixtureEvents.slice(1, 2).join('\n') + '\n');
    await expect(page.locator('.live-caption')).toHaveText('Trial trial-kit queued.', { timeout: 10_000 });
    await expect(page.locator('.live-summary > div').nth(3)).toContainText('2 / 2');
    await page.getByRole('link', { name: 'Overview' }).click();
    await expect(page.getByRole('heading', { name: 'smoke', level: 2 })).toBeVisible();
  } finally {
    server.kill('SIGTERM');
    await new Promise<void>((resolveClose) => {
      if (server.exitCode !== null) resolveClose();
      else server.once('exit', () => resolveClose());
      setTimeout(() => { server.kill('SIGKILL'); resolveClose(); }, 3000).unref();
    });
  }
});

test('live event stream can be replayed through the view controls', async ({ page }) => {
  await page.goto('/live');
  await page.getByRole('button', { name: 'Play replay' }).click();
  await expect(page.getByRole('button', { name: 'Pause replay' })).toBeVisible();
  await page.getByRole('button', { name: 'Pause replay' }).click();
  await page.getByRole('button', { name: 'Reset replay' }).click();
  await expect(page.getByText('0 / 7 events')).toBeVisible();
});
