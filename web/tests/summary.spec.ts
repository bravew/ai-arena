import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import type { Bundle } from '../src/lib/schema';
import { calculateSummaries, getParetoFrontier, sortMatrixRows } from '../src/views/leaderboard/summary';
import { contestantColor } from '../src/theme';

const bundle = JSON.parse(readFileSync(resolve(process.cwd(), '../fixtures/bundles/schema-example.json'), 'utf8')) as Bundle;
const scoredBundle: Bundle = {
  ...bundle,
  scores: [...bundle.scores, ...bundle.trials.filter((trial) => trial.contestant_id !== 'c-ablation-base').map((trial) => ({ ...bundle.scores[0]!, trial_id: trial.id }))],
};
const repeatedBundle: Bundle = {
  ...scoredBundle,
  trials: [...scoredBundle.trials,
    ...bundle.trials.map((trial) => ({ ...trial, id: `${trial.id}-repeat`, attempt: 2 })),
    ...bundle.trials.map((trial) => ({ ...trial, id: `${trial.id}-second`, task_id: 'smoke.second' })),
  ],
  scores: [...scoredBundle.scores,
    ...bundle.trials.map((trial) => ({ ...bundle.scores[0]!, trial_id: `${trial.id}-repeat`, normalized: trial.contestant_id === 'c-ablation-base' ? 0.8 : 1, value: trial.contestant_id === 'c-ablation-base' ? 0.8 : 1 })),
    ...bundle.trials.map((trial) => ({ ...bundle.scores[0]!, trial_id: `${trial.id}-second` })),
  ],
  calls: [...scoredBundle.calls, ...bundle.calls.map((call) => ({ ...call, id: `${call.id}-repeat`, trial_id: call.trial_id ? `${call.trial_id}-repeat` : call.trial_id })), ...bundle.calls.map((call) => ({ ...call, id: `${call.id}-second`, trial_id: call.trial_id ? `${call.trial_id}-second` : call.trial_id }))],
};

test('summary averages tasks equally and returns no CI for one task', () => {
  expect(calculateSummaries(bundle).find((item) => item.contestant.id === 'c-ablation-base')).toMatchObject({ meanScore: 1, confidenceInterval: null, passK: 1 });
});

test('repeated trials produce cluster-bootstrap intervals and paired comparisons', () => {
  const summaries = calculateSummaries(repeatedBundle);
  const baseline = summaries.find((item) => item.contestant.id === 'c-ablation-base');
  expect(baseline?.pairedDifferenceFromLeader).not.toBeNull();
  expect(baseline?.confidenceInterval).not.toBeNull();
});

test('pass^k requires every repeat on each task to pass', () => {
  const mixed: Bundle = { ...repeatedBundle, scores: repeatedBundle.scores.map((score) => score.trial_id === 'trial-kit-repeat' ? { ...score, passed: false, normalized: 0, value: 0 } : score) };
  expect(calculateSummaries(mixed).find((item) => item.contestant.id === 'c-ablation-kit')?.passK).toBe(0.5);
});

test('flat-fee contestants remain eligible on the tokens per task Pareto frontier', () => {
  const subscription = calculateSummaries(scoredBundle).find((item) => item.contestant.id === 'c-subscription');
  expect(subscription).toMatchObject({ costPerTask: 'flat', tokensPerTask: 152 });
  expect(getParetoFrontier(calculateSummaries(scoredBundle), 'tokens').map((item) => item.contestant.id)).toContain('c-subscription');
});

test('matrix cells average multiple scorer rows per trial before estimating uncertainty', () => {
  const multiScorer: Bundle = { ...bundle, scores: [...bundle.scores, { ...bundle.scores[0]!, scorer_id: 'second-scorer', normalized: 0.5, value: 0.5 }] };
  const cell = sortMatrixRows(multiScorer)[0]?.cells.find((item) => item.contestant.id === 'c-ablation-base');
  expect(cell).toMatchObject({ score: 0.75, confidenceInterval: null, trialIds: ['trial-base'] });
});

test('matrix cell retains trial IDs needed to open Compare', () => {
  expect(sortMatrixRows(bundle)[0]?.cells.find((item) => item.contestant.id === 'c-ablation-base')).toMatchObject({ score: 1, trialIds: ['trial-base'] });
});

test('empty data stays empty and contestant colors are stable by identity', () => {
  const empty: Bundle = { ...bundle, contestants: [], trials: [], calls: [], scores: [] };
  expect(calculateSummaries(empty)).toEqual([]);
  expect(getParetoFrontier(calculateSummaries(empty))).toEqual([]);
  expect(sortMatrixRows(empty)).toEqual([]);
  expect(contestantColor('c-ablation-base')).toBe(contestantColor('c-ablation-base'));
});

test('summary routes resolve in the shared application shell', async ({ page }) => {
  await page.goto('/leaderboard');
  await expect(page.getByRole('heading', { name: 'Leaderboard', level: 2 })).toBeVisible();
  await page.goto('/pareto');
  await expect(page.getByRole('heading', { name: 'Pareto frontier', level: 2 })).toBeVisible();
  await page.goto('/matrix');
  await expect(page.getByRole('heading', { name: 'Task matrix', level: 2 })).toBeVisible();
});
