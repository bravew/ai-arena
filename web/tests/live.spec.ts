import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import type { Bundle, RunEvent } from '../src/lib/schema';
import { ledgerTotals, reduceEvents } from '../src/views/live/model';

const events = readFileSync(resolve(process.cwd(), '../fixtures/events/events.jsonl'), 'utf8').trim().split(/\r?\n/).map((line) => JSON.parse(line) as RunEvent);
const bundle = JSON.parse(readFileSync(resolve(process.cwd(), '../fixtures/bundles/schema-example.json'), 'utf8')) as Bundle & { provenance: { origin: string; verification: string; importer: string | null; importer_version: string | null; source_ref: string | null } };

test('fixture replay finishes with counters matching the bundle ledger', () => {
  const replay = reduceEvents(events, bundle);
  expect(replay.complete).toBe(true);
  expect(replay.trials.size).toBe(ledgerTotals(bundle).trials);
  expect(replay.calls.size).toBe(ledgerTotals(bundle).calls);
});

test('reduced motion applies events immediately without changing final state', () => {
  expect(reduceEvents(events)).toEqual(reduceEvents([...events]));
});

test('replay preserves the latest event caption and terminal trial status', () => {
  const replay = reduceEvents(events, bundle);
  expect(replay.latestCaption).toBe('Run succeeded.');
  expect(replay.trials.get('trial-kit')?.status).toBe('succeeded');
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

test('live animation runs no frames while the document is hidden', async ({ page }) => {
  await page.goto('/live');
  await page.evaluate(() => Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }));
  await page.waitForTimeout(100);
  expect(await page.locator('[data-frame-count]').getAttribute('data-frame-count')).toBe('0');
});

test('live event stream can be replayed through the view controls', async ({ page }) => {
  await page.goto('/live');
  await page.getByRole('button', { name: 'Play replay' }).click();
  await expect(page.getByRole('button', { name: 'Pause replay' })).toBeVisible();
  await page.getByRole('button', { name: 'Pause replay' }).click();
  await page.getByRole('button', { name: 'Reset replay' }).click();
  await expect(page.getByText('0 / 7 events')).toBeVisible();
});
