import { expect, test } from '@playwright/test';

test('casts 20 mock blind votes and refreshes leaderboard', async ({ page }) => {
  await page.goto('/src/views/arena/test-entry.html');

  for (let index = 1; index <= 20; index += 1) {
    await expect(page.getByRole('heading', { name: 'Blind comparison' })).toBeVisible();
    await expect(page.getByText(`Response A for task ${index}`)).toBeVisible();
    await expect(page.getByText(new RegExp(`contestant-pair-${index}-a`))).toHaveCount(0);
    await page.getByRole('button', { name: /A is better/ }).click();
    await expect(page.getByRole('heading', { name: 'Identities revealed' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Identities revealed' }).getByText(`contestant-pair-${index}-a`, { exact: true })).toBeVisible();
    await expect(page.getByRole('listitem')).toContainText(`${index} comparisons`);

    if (index < 20) await page.getByRole('button', { name: 'Next comparison' }).click();
  }

  await page.getByRole('button', { name: 'Next comparison' }).click();
  await expect(page.getByRole('status')).toContainText('completed all available comparisons');
});
