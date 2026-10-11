import { expect, test } from '@playwright/test';
import type { ArenaDataSource, VoteChoice } from '../src/views/arena/arena-data';

test('casts 20 mock blind votes and refreshes leaderboard', async ({ page }) => {
  await page.addInitScript(() => {
    const pairs = Array.from({ length: 20 }, (_, index) => ({
      pair_id: `pair-${index + 1}`,
      task_id: `task-${index + 1}`,
      left: { content: `Response A for task ${index + 1}` },
      right: { content: `Response B for task ${index + 1}` },
    }));
    const votes: { pair_id: string; choice: VoteChoice }[] = [];
    let next = 0;
    const source: ArenaDataSource = {
      nextPair: async () => pairs[next] ?? null,
      castVote: async (pairId, choice) => {
        votes.push({ pair_id: pairId, choice });
        next += 1;
        return {
          vote: { choice },
          revealed: {
            [`trial-${pairId}-a`]: `contestant-${pairId}-a`,
            [`trial-${pairId}-b`]: `contestant-${pairId}-b`,
          },
        };
      },
      leaderboard: async () => [
        {
          contestant_id: `contestant-pair-${votes.length}-a`,
          rating: 1.2 + votes.length / 100,
          low: 0.8,
          high: 1.6,
          comparisons: votes.length,
        },
      ],
    };
    Object.defineProperty(window, '__arenaDataSource', { value: source });
  });

  await page.goto('/vote');
  for (let index = 1; index <= 20; index += 1) {
    await expect(page.getByRole('heading', { name: 'Blind comparison' })).toBeVisible();
    await expect(page.getByText(`Response A for task ${index}`)).toBeVisible();
    await expect(page.getByText(new RegExp(`contestant-pair-${index}-a`))).toHaveCount(0);
    await page.getByRole('button', { name: /A is better/ }).click();
    await expect(page.getByRole('heading', { name: 'Identities revealed' })).toBeVisible();
    await expect(
      page.getByRole('region', { name: 'Identities revealed' }).getByText(`contestant-pair-${index}-a`, { exact: true }),
    ).toBeVisible();
    const leaderboard = page.getByRole('region', { name: 'Human leaderboard' });
    await expect(leaderboard).toContainText(`${index} comparisons`);
    await expect(leaderboard).toContainText(`contestant-pair-${index}-a`);
    if (index < 20) await page.getByRole('button', { name: 'Next comparison' }).click();
  }
  await page.getByRole('button', { name: 'Next comparison' }).click();
  await expect(page.getByRole('status')).toContainText('completed all available comparisons');
});
