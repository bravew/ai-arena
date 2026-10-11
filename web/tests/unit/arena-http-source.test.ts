import { describe, expect, it, vi } from 'vitest';
import { createHttpArenaSource } from '../../src/views/arena/http-source';

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('createHttpArenaSource', () => {
  it('returns null when the server has no more comparisons', async () => {
    const fetcher = vi.fn(async () => new Response(null, { status: 204 }));
    expect(await createHttpArenaSource('', fetcher).nextPair()).toBeNull();
    expect(fetcher).toHaveBeenCalledWith('/api/votes/pair', { credentials: 'same-origin' });
  });

  it('posts the vote as JSON and returns the reveal', async () => {
    const reveal = { vote: { choice: 'A' }, revealed: { t1: 'alpha', t2: 'beta' } };
    const fetcher = vi.fn<typeof fetch>(async () => json(201, reveal));
    const result = await createHttpArenaSource('', fetcher).castVote('p1', 'A');
    expect(result).toEqual(reveal);
    const init = fetcher.mock.calls[0]![1]!;
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ pair_id: 'p1', choice: 'A' });
  });

  it('surfaces the server error message when a vote is rejected', async () => {
    const fetcher = vi.fn(async () => json(409, { error: 'voter already voted on this pair' }));
    await expect(createHttpArenaSource('', fetcher).castVote('p1', 'A')).rejects.toThrow(
      'recording the vote failed (409): voter already voted on this pair',
    );
  });

  it('reports a non-JSON failure by status alone', async () => {
    const fetcher = vi.fn(async () => new Response('boom', { status: 500 }));
    await expect(createHttpArenaSource('', fetcher).leaderboard()).rejects.toThrow(
      'loading the leaderboard failed (500)',
    );
  });
});
