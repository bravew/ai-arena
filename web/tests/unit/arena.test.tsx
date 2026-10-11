import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ArenaView } from '../../src/views/arena/ArenaView';
import type { ArenaDataSource, BlindPair, HumanLeaderboardEntry, VoteReveal } from '../../src/views/arena/arena-data';

const pair: BlindPair = {
  pair_id: 'pair-1',
  task_id: 'task-1',
  left: { content: 'Answer from left contestant.' },
  right: { content: 'Answer from right contestant.' },
};
const board: readonly HumanLeaderboardEntry[] = [{ contestant_id: 'winner-model', rating: 1.2, low: 0.8, high: 1.6, comparisons: 3 }];
const reveal: VoteReveal = { vote: { choice: 'A' }, revealed: { trialA: 'winner-model', trialB: 'other-model' } };

function source(overrides: Partial<ArenaDataSource> = {}): ArenaDataSource {
  return {
    nextPair: vi.fn(async () => pair),
    castVote: vi.fn(async () => reveal),
    leaderboard: vi.fn(async () => board),
    ...overrides,
  };
}

afterEach(cleanup);

describe('ArenaView', () => {
  it('keeps identities hidden until a vote, then reveals and loads the leaderboard', async () => {
    const data = source();
    render(<ArenaView dataSource={data} />);

    expect(await screen.findByText('Answer from left contestant.')).toBeTruthy();
    expect(screen.queryByText('winner-model')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /A is better/i }));

    expect(await screen.findByRole('heading', { name: 'Identities revealed' })).toBeTruthy();
    expect(screen.getAllByText('winner-model')).toHaveLength(2);
    expect(await screen.findByRole('heading', { name: 'Human leaderboard' })).toBeTruthy();
    expect(data.castVote).toHaveBeenCalledWith('pair-1', 'A');
    expect(data.leaderboard).toHaveBeenCalledOnce();
  });

  it.each([
    ['A', 'A'], ['B', 'B'], ['T', 'tie'], ['X', 'both_bad'],
  ] as const)('supports keyboard shortcut %s', async (key, choice) => {
    const data = source();
    render(<ArenaView dataSource={data} />);
    await screen.findByText('Answer from left contestant.');
    fireEvent.keyDown(window, { key });
    await waitFor(() => expect(data.castVote).toHaveBeenCalledWith('pair-1', choice));
  });

  it('offers retry when loading the next pair fails', async () => {
    const data = source({ nextPair: vi.fn().mockRejectedValue(new Error('network unavailable')) });
    render(<ArenaView dataSource={data} />);
    expect((await screen.findByRole('alert')).textContent).toContain('network unavailable');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(data.nextPair).toHaveBeenCalledTimes(2));
  });

  it('shows completion when there are no pairs', async () => {
    render(<ArenaView dataSource={source({ nextPair: vi.fn(async () => null) })} />);
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('completed all available comparisons'));
  });
});
