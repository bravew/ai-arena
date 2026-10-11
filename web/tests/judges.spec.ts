import { expect, test } from '@playwright/test';
import { spawnSync } from 'node:child_process';

test('judges view uploads a CLI report, filters judges and explains calibration', async ({ page }) => {
  const repo = process.env.ARENA_REPO;
  if (!repo) throw new Error('Playwright config must set ARENA_REPO to this worktree.');
  const cli = spawnSync('uv', ['run', 'arena', 'judges', 'report', 'fixtures/gold', '--json'], {
    cwd: repo,
    encoding: 'utf8',
  });
  expect(cli.status, cli.stderr).toBe(0);
  await page.goto('/judges');
  await page.getByLabel('Upload judge report JSON').setInputFiles({
    name: 'judges.json',
    mimeType: 'application/json',
    buffer: Buffer.from(cli.stdout),
  });
  await expect(page.getByRole('heading', { name: 'Judges', level: 2 })).toBeVisible();
  await expect(page.getByRole('rowheader', { name: 'judge-good' })).toBeVisible();
  await expect(page.getByRole('rowheader', { name: 'judge-biased' })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('below the κ target');
  await expect(page.getByText('i09', { exact: true })).toBeVisible();

  await page.getByLabel('Filter by judge').click();
  await page.getByRole('group', { name: 'Judge options' }).getByRole('button', { name: 'judge-good' }).click();
  await expect(page.getByRole('row', { name: /judge-good/ })).toBeVisible();
  await expect(page.getByRole('row', { name: /judge-biased/ })).toHaveCount(0);
  await expect(page.getByText('i09', { exact: true })).toBeVisible();
  await expect(page.getByText('i03', { exact: true })).toHaveCount(0);
});

test('judges view gives an explicit error for malformed JSON', async ({ page }) => {
  await page.goto('/judges');
  await page.getByLabel('Upload judge report JSON').setInputFiles({
    name: 'broken.json', mimeType: 'application/json', buffer: Buffer.from('{bad json'),
  });
  await expect(page.getByRole('alert')).toContainText('Could not parse JSON');
});

test('judges view rejects incomplete report JSON and renders null metrics as n/a', async ({ page }) => {
  await page.goto('/judges');
  const incomplete = JSON.stringify({
    threshold: 0.6,
    judges: [{
      judge_id: 'partial', judgments: 1, human_labeled: 1, cohens_kappa: null,
      missing_verdicts: 0, missing_human_labels: 0, missing_order_swaps: 0, missing_lengths: 0, missing_costs: 1,
      status: 'UNCALIBRATED',
    }],
    disagreements: [],
  });
  await page.getByLabel('Upload judge report JSON').setInputFiles({
    name: 'incomplete.json', mimeType: 'application/json', buffer: Buffer.from(incomplete),
  });
  await expect(page.getByRole('alert')).toContainText('Judge partial agreement must be a finite number.');
  await expect(page.getByRole('heading', { name: 'Calibration' })).toHaveCount(0);

  const validNullMetrics = JSON.stringify({
    threshold: 0.6,
    judges: [{
      judge_id: 'unlabeled', judgments: 1, human_labeled: 0, agreement: null, cohens_kappa: null,
      position_bias_rate: null, length_correlation: null, self_preference_rate: null,
      cost_per_judgment_usd: null, missing_verdicts: 1, missing_human_labels: 1,
      missing_order_swaps: 1, missing_lengths: 1, missing_costs: 1, status: 'UNCALIBRATED',
    }],
    disagreements: [],
  });
  await page.getByLabel('Upload judge report JSON').setInputFiles({
    name: 'null-metrics.json', mimeType: 'application/json', buffer: Buffer.from(validNullMetrics),
  });
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByRole('row', { name: /unlabeled/ })).toContainText('n/a');
});
