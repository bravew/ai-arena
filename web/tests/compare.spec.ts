import { expect, test } from '@playwright/test';

test('registered report routes use the loaded bundle without URL supplied data', async ({ page }) => {
  await page.goto('/compare');
  await expect(page.getByRole('heading', { name: 'Compare trials', level: 2 })).toBeVisible();
  await expect(page.getByText('This fixture contains one run. Run A is')).toHaveCount(0);

  await page.goto('/trace');
  await expect(page.getByRole('heading', { name: 'Trace', level: 2 })).toBeVisible();

  await page.goto('/run-diff');
  await expect(page.getByRole('heading', { name: 'Run diff', level: 2 })).toBeVisible();
  await expect(page.getByText('This fixture contains one run. Run A is run-schema-001; a second run must be loaded before a comparison is available.')).toBeVisible();
});

test('URL parameters cannot select a different bundle or inject run data', async ({ page }) => {
  await page.goto('/run-diff?bundle=attacker-run&run=private-run&data=Injected');
  await expect(page.getByRole('heading', { name: 'Run diff', level: 2 })).toBeVisible();
  await expect(page.getByText('This fixture contains one run. Run A is run-schema-001; a second run must be loaded before a comparison is available.')).toBeVisible();
  await expect(page.getByText('attacker-run', { exact: true })).toHaveCount(0);
  await expect(page.getByText('private-run', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Injected', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Run A')).toHaveCount(0);
});
