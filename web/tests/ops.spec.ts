import { expect, test } from '@playwright/test';

test('ops shows ledger filters, usage and latency charts, lanes, meters and hook stats', async ({ page }) => {
  await page.goto('/ops');
  await expect(page.getByRole('heading', { name: 'Calls ledger' })).toBeVisible();
  await expect(page.getByRole('table', { name: 'Calls ledger' })).toBeVisible();
  await expect(page.getByLabel('Filter by provider')).toBeVisible();
  await expect(page.getByRole('img', { name: 'Token usage series' })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Latency series' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Provider lanes' })).toBeVisible();
  await expect(page.getByText('anthropic/main')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Subscription meters' })).toBeVisible();
  await expect(page.getByText('weekly usage')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Hook stats' })).toBeVisible();

  await page.getByLabel('Filter by provider').fill('ANTHROPIC-MAX');
  await expect(page.getByRole('row', { name: /anthropic-max/ })).toHaveCount(2);
  await expect(page.getByRole('row', { name: /openai/ })).toHaveCount(0);

  await page.getByLabel('Filter by provider').fill('');
  await page.getByLabel('Filter by status').click();
  await page.getByRole('group', { name: 'Status options' }).getByRole('button', { name: '429' }).click();
  await expect(page.getByRole('row', { name: /429/ })).toBeVisible();
  await expect(page.getByRole('row', { name: /rate_limit/ })).toHaveCount(0);

  await page.getByLabel('Filter by status').click();
  await page.getByRole('group', { name: 'Status options' }).getByRole('button', { name: 'hook', exact: true }).click();
  await expect(page.getByRole('row', { name: /hook/ })).toBeVisible();

  await page.getByLabel('Filter by status').click();
  await page.getByRole('group', { name: 'Status options' }).getByRole('button', { name: 'All statuses' }).click();
  await page.getByLabel('Search calls').fill('NO-MATCH');
  await expect(page.getByText('No calls match these filters.')).toBeVisible();
  await expect(page.getByRole('row', { name: /call-/ })).toHaveCount(0);
});
