import { expect, test } from '@playwright/test';

test('sessions view exposes kit filtering and session timeline', async ({ page }) => {
  await page.goto('/sessions');
  await expect(page.getByRole('heading', { name: 'Sessions', level: 2 })).toBeVisible();
  await expect(page.getByLabel('Filter by kit')).toBeVisible();
  await page.getByLabel('Filter by kit').click();
  await page.getByRole('option', { name: 'sha256:kit-v3' }).click();
  await expect(page.getByRole('button', { name: /smoke.echo/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'smoke.echo', level: 3 })).toBeVisible();
  await expect(page.getByText('house-style', { exact: false })).toBeVisible();
  await expect(page.getByText('(loaded)')).toBeVisible();
});

test('kit effect view explains the observational skill split', async ({ page }) => {
  await page.goto('/kit-effect');
  await expect(page.getByRole('heading', { name: 'Kit effect', level: 2 })).toBeVisible();
  await expect(page.getByText(/Observational split/)).toBeVisible();
  await expect(page.getByText(/No scored applied-kit and baseline trials/)).toBeVisible();
});
