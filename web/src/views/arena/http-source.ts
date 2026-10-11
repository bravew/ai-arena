import type {
  ArenaDataSource,
  BlindPair,
  HumanLeaderboardEntry,
  VoteChoice,
  VoteReveal,
} from './arena-data';

type Fetcher = typeof fetch;

async function failure(response: Response, action: string): Promise<Error> {
  let detail = '';
  try {
    const body = (await response.json()) as { error?: unknown };
    if (typeof body.error === 'string') detail = `: ${body.error}`;
  } catch {
    // The status line is enough when the body is not JSON.
  }
  return new Error(`${action} failed (${response.status})${detail}`);
}

/** Talks to the viewer server's `/api/votes` routes; the voter id lives in an HttpOnly cookie. */
export function createHttpArenaSource(
  base = '',
  fetcher: Fetcher = (...args) => globalThis.fetch(...args),
): ArenaDataSource {
  const credentials: RequestCredentials = 'same-origin';
  return {
    async nextPair(): Promise<BlindPair | null> {
      const response = await fetcher(`${base}/api/votes/pair`, { credentials });
      if (response.status === 204) return null;
      if (!response.ok) throw await failure(response, 'loading the next comparison');
      return (await response.json()) as BlindPair;
    },
    async castVote(pairId: string, choice: VoteChoice): Promise<VoteReveal> {
      const response = await fetcher(`${base}/api/votes`, {
        method: 'POST',
        credentials,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pair_id: pairId, choice }),
      });
      if (!response.ok) throw await failure(response, 'recording the vote');
      return (await response.json()) as VoteReveal;
    },
    async leaderboard(): Promise<readonly HumanLeaderboardEntry[]> {
      const response = await fetcher(`${base}/api/votes/leaderboard`, { credentials });
      if (!response.ok) throw await failure(response, 'loading the leaderboard');
      return (await response.json()) as HumanLeaderboardEntry[];
    },
  };
}
