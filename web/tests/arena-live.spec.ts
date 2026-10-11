import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

// Needs `pnpm e2e:live`: a real `arena serve` over a home seeded with 20 two-contestant tasks.
test('casts 20 blind votes against the real server, then identities and the leaderboard appear', async ({ page }) => {
  await page.goto('/vote');
  for (let index = 1; index <= 20; index += 1) {
    await expect(page.getByRole('group', { name: 'Choose a comparison result' })).toBeVisible();
    // Before the vote, neither contestant name is anywhere on the page.
    await expect(page.getByText(/alpha|beta/)).toHaveCount(0);
    await page.keyboard.press('a');
    await expect(page.getByRole('heading', { name: 'Identities revealed' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Identities revealed' })).toContainText(/alpha/);
    // The human leaderboard counts every vote for both contestants and refreshes after each one.
    const board = page.getByRole('region', { name: 'Human leaderboard' }).getByRole('listitem');
    await expect(board).toHaveCount(2);
    await expect(board.first()).toContainText(`${index} comparisons`);
    await expect(board.last()).toContainText(`${index} comparisons`);
    if (index < 20) await page.getByRole('button', { name: 'Next comparison' }).click();
  }
  await page.getByRole('button', { name: 'Next comparison' }).click();
  await expect(page.getByRole('status')).toContainText('completed all available comparisons');

  // The votes reached the data home that feeds the gold set.
  const home = process.env.ARENA_VOTE_HOME;
  expect(home).toBeTruthy();
  const lines = readFileSync(path.join(home!, 'votes.jsonl'), 'utf8').trim().split('\n');
  expect(lines).toHaveLength(20);
});

test('Ops reads seeded ledger calls and arena-counted subscription usage from arena serve', async ({ page }) => {
  await page.goto('/ops');
  await expect(page.getByRole('heading', { name: 'Calls ledger' })).toBeVisible();
  await expect(page.getByRole('row', { name: /anthropic-max.*claude-opus-5-5.*200/ })).toBeVisible();
  await expect(page.getByRole('row', { name: /openai.*gpt-6.*200/ })).toBeVisible();
  await expect(page.getByText(/lanes: unavailable/)).toBeVisible();
  await expect(page.getByText(/arena-counted/).first()).toBeVisible();
  await expect(page.getByText('0123abcd').first()).toBeVisible();
});
