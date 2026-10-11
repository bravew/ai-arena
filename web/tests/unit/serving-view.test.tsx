import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Bundle } from '../../src/lib/schema';
import { schemaExample } from '../../src/lib/schema/fixture';
import { LeaderboardView } from '../../src/views/leaderboard/LeaderboardView';
import { ParetoView } from '../../src/views/pareto/ParetoView';

const bundle = schemaExample as Bundle;
const scored: Bundle = {
  ...bundle,
  scores: [...bundle.scores, ...bundle.trials.filter((trial) => !bundle.scores.some((score) => score.trial_id === trial.id)).map((trial) => ({ ...bundle.scores[0]!, trial_id: trial.id }))],
};

describe('serving mode labels', () => {
  it('shows the subscription gateway and flat cost in the leaderboard', () => {
    render(<LeaderboardView bundle={scored} />);
    expect(screen.getByRole('columnheader', { name: 'Served by' })).toBeTruthy();
    expect(screen.getByText('subscription (gateway)')).toBeTruthy();
    expect(screen.getByText('flat')).toBeTruthy();
  });

  it('explains token comparison and labels the subscription in Pareto', () => {
    render(<ParetoView bundle={scored} />);
    expect(screen.getByText(/subscription pricing at a flat fee/)).toBeTruthy();
    expect(screen.getAllByRole('columnheader', { name: 'Served by' })).toHaveLength(2);
    expect(screen.getAllByText('subscription (gateway)').length).toBeGreaterThan(0);
  });
});
