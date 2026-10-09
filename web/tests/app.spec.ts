import { expect, test } from '@playwright/test';

test('serves the overview shell and navigates to a viewer route', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'smoke', level: 2 })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Contestants' })).toBeVisible();
  await expect(page.getByRole('row', { name: /smoke.echo OpenCode · no kit/ })).toBeVisible();
  await page.getByRole('link', { name: /Leaderboard/ }).first().click();
  await expect(page.getByRole('heading', { name: 'Leaderboard', level: 2 })).toBeVisible();
  await expect(page).toHaveURL(/\/leaderboard$/);
});

test('shows status by dot and text, preserves contestant identity color, and toggles theme', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('succeeded', { exact: true }).first()).toBeVisible();
  const swatch = page.locator('.contestant-swatch').first();
  const color = await swatch.evaluate((node) => getComputedStyle(node).backgroundColor);
  await page.reload();
  await expect(page.locator('.contestant-swatch').first()).toHaveCSS('background-color', color);
  await page.getByRole('button', { name: 'Switch to dark theme' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.getByRole('button', { name: 'Switch to light theme' })).toBeVisible();
});

test('keeps native select out of the rendered shell', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('select')).toHaveCount(0);
});

test('validates an imported bundle and presents schema errors clearly', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Choose report bundle JSON').setInputFiles({
    name: 'broken.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify({ bundle_version: 1 })),
  });
  await expect(page.getByRole('alert')).toContainText('Could not read this report bundle');
  await expect(page.getByRole('alert')).toContainText('contestants');
  await page.getByLabel('Choose report bundle JSON').setInputFiles({
    name: 'broken.json',
    mimeType: 'application/json',
    buffer: Buffer.from('{no json'),
  });
  await expect(page.getByRole('alert')).toContainText('invalid JSON');
});
