import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import type { Bundle } from '../src/lib/schema';

const fixtureEvents = readFileSync(resolve(process.cwd(), '../fixtures/events/events.jsonl'), 'utf8').trim().split(/\r?\n/);
const bundle = JSON.parse(readFileSync(resolve(process.cwd(), '../fixtures/bundles/schema-example.json'), 'utf8')) as Bundle & { provenance: { origin: string; verification: string; importer: string | null; importer_version: string | null; source_ref: string | null } };

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

test('live event stream can be replayed through the view controls', async ({ page }) => {
  await page.goto('/live');
  await page.getByRole('button', { name: 'Play replay' }).click();
  await expect(page.getByRole('button', { name: 'Pause replay' })).toBeVisible();
  await page.getByRole('button', { name: 'Pause replay' }).click();
  await page.getByRole('button', { name: 'Reset replay' }).click();
  await expect(page.getByText('0 / 7 events')).toBeVisible();
});
