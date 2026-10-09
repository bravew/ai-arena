import { expect, test } from '@playwright/test';

test('routes keep unknown bundle data on the shared placeholder', async ({ page }) => {
  for (const [path, title] of [['/compare', 'Compare trials'], ['/trace', 'Trace'], ['/run-diff', 'Run diff']] as const) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: title, level: 2 })).toBeVisible();
    await expect(page.getByText('This view is ready for the report bundle and plugs into the shared shell.')).toBeVisible();
  }
});

test('URL parameters cannot select a different bundle or inject run data', async ({ page }) => {
  await page.goto('/run-diff?bundle=attacker-run&run=private-run&data=Injected');
  await expect(page.getByRole('heading', { name: 'Run diff', level: 2 })).toBeVisible();
  await expect(page.getByText('This view is ready for the report bundle and plugs into the shared shell.')).toBeVisible();
  await expect(page.getByText('attacker-run', { exact: true })).toHaveCount(0);
  await expect(page.getByText('private-run', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Injected', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Run A')).toHaveCount(0);
});

