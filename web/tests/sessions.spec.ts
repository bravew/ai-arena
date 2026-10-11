import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { Bundle } from '../src/lib/schema';
import { buildPairs } from '../src/views/kit-effect/kit-effect-data';

const fixture = JSON.parse(readFileSync(resolve(process.cwd(), '../fixtures/bundles/schema-example.json'), 'utf8')) as Bundle;
const sessionsFixture = JSON.parse(readFileSync(resolve(process.cwd(), 'tests/fixtures/sessions-kit-effect.json'), 'utf8')) as Bundle;

function pairedFixture(): Bundle {
  const baseContestant = fixture.contestants[0]!;
  const kitContestant = fixture.contestants[1]!;
  const baseTrial = fixture.trials[0]!;
  const kitTrial = fixture.trials[1]!;
  return {
    ...fixture,
    contestants: [baseContestant, { ...baseContestant, id: kitContestant.id, label: kitContestant.label, kit_hash: kitContestant.kit_hash }],
    trials: [baseTrial, kitTrial],
    kit_installs: [{ trial_id: kitTrial.id, kit_hash: kitContestant.kit_hash, written: [], refused: [] }],
    scores: [
      { ...fixture.scores[0]!, trial_id: baseTrial.id, normalized: 0.4 },
      { ...fixture.scores[0]!, trial_id: kitTrial.id, normalized: 0.9 },
    ],
  };
}

test('sessions view exposes kit filtering and session timeline', async ({ page }) => {
  await page.goto('/sessions');
  await page.getByLabel('Choose report bundle JSON').setInputFiles({
    name: 'sessions-kit-effect.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(sessionsFixture)),
  });
  await expect(page.getByRole('heading', { name: 'Sessions', level: 2 })).toBeVisible();
  await expect(page.getByLabel('Filter by kit')).toBeVisible();
  await page.getByLabel('Filter by kit').click();
  await page.getByRole('option', { name: 'team-coding@3' }).click();
  await expect(page.getByRole('button', { name: /smoke.echo/ }).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'smoke.echo', level: 3 })).toBeVisible();
  await expect(page.getByRole('article').getByText(/OpenCode · team-coding@3 · team-coding@3/)).toBeVisible();
  await expect(page.locator('.skill-events').first()).toContainText('tdd-loop');
  await expect(page.getByText('(listed)')).toBeVisible();
  await expect(page.getByText('(loaded)')).toBeVisible();
  await expect(page.getByText('(invoked)')).toBeVisible();
  await page.getByLabel('Compare with session').click();
  await page.getByRole('option', { name: /session-base-1/ }).click();
  await expect(page.getByRole('region', { name: 'Session comparison aligned by turn' })).toBeVisible();
  await expect(page.getByText('Compare sessions · aligned by turn')).toBeVisible();
  await expect(page.getByText('Turn 2', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /smoke.echo OpenCode · team-coding@3 team-coding@3 · team-coding@3/ }).nth(1).click();
  await expect(page.getByText('partial', { exact: true })).toBeVisible();
  await page.getByLabel('Compare with session').click();
  await page.getByRole('option', { name: /session-kit-1/ }).click();
  await expect(page.getByRole('region', { name: 'Session comparison aligned by turn' })).toContainText('No turn');
});

test('kit effect pairs only a unique baseline with identical model agent and config', () => {
  const bundle = pairedFixture();
  expect(buildPairs(bundle).map((pair) => pair.baseline)).toEqual(['trial-base']);

  const ambiguous: Bundle = { ...bundle, trials: [...bundle.trials, { ...bundle.trials[0]!, id: 'trial-base-repeat' }] };
  expect(buildPairs(ambiguous)).toEqual([]);

  const sharedBaseline: Bundle = {
    ...bundle,
    trials: [...bundle.trials, { ...bundle.trials[1]!, id: 'trial-kit-repeat', attempt: 1 }],
    kit_installs: [...bundle.kit_installs, { ...bundle.kit_installs[0]!, trial_id: 'trial-kit-repeat' }],
    scores: [...bundle.scores, { ...bundle.scores[1]!, trial_id: 'trial-kit-repeat' }],
  };
  expect(buildPairs(sharedBaseline)).toEqual([]);

  const mismatchedModel: Bundle = {
    ...bundle,
    contestants: bundle.contestants.map((contestant) => contestant.id === 'c-ablation-base' ? { ...contestant, model: 'mock/other' } : contestant),
  };
  expect(buildPairs(mismatchedModel)).toEqual([]);

  const mismatchedAgent: Bundle = {
    ...bundle,
    contestants: bundle.contestants.map((contestant) => contestant.id === 'c-ablation-base' ? { ...contestant, scaffold: { ...contestant.scaffold!, id: 'other-agent' } } : contestant),
  };
  expect(buildPairs(mismatchedAgent)).toEqual([]);

  const mismatchedConfig: Bundle = {
    ...bundle,
    contestants: bundle.contestants.map((contestant) => contestant.id === 'c-ablation-base' ? { ...contestant, params: { temperature: 0.8 } } : contestant),
  };
  expect(buildPairs(mismatchedConfig)).toEqual([]);
});

test('kit effect view shows the planted lift and observational skill split', async ({ page }) => {
  await page.goto('/kit-effect');
  await page.getByLabel('Choose report bundle JSON').setInputFiles({
    name: 'sessions-kit-effect.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(sessionsFixture)),
  });
  await expect(page.getByRole('heading', { name: 'Kit effect', level: 2 })).toBeVisible();
  await expect(page.getByText(/Observational split/)).toBeVisible();
  await expect(page.locator('.kit-effect-summary > strong')).toHaveText('+0.150');
  await expect(page.locator('.kit-effect-summary small')).toHaveText('2 paired tasks · descriptive, no CI available');
  await expect(page.locator('.summary-table tbody tr').first()).toContainText('1 invoked / 1 listed');
  await expect(page.locator('.summary-table tbody tr').nth(1)).toContainText('1 invoked / 1 listed');
});
