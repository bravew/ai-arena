import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ArenaView } from './ArenaView';
import '../../theme/tokens.css';
import '../../app/shell.css';

const pairs = Array.from({ length: 20 }, (_, index) => ({
  pair_id: `pair-${index + 1}`,
  task_id: `task-${index + 1}`,
  left: { content: `Response A for task ${index + 1}` },
  right: { content: `Response B for task ${index + 1}` },
}));
const votes: Array<{ pair_id: string; choice: string }> = [];
let next = 0;

const root = document.getElementById('root');
if (!root) throw new Error('Missing #root');
createRoot(root).render(<StrictMode><main className="main-content"><ArenaView dataSource={{
  nextPair: async () => pairs[next] ?? null,
  castVote: async (pairId, choice) => {
    votes.push({ pair_id: pairId, choice });
    next += 1;
    return {
      vote: { choice },
      revealed: { [`trial-${pairId}-a`]: `contestant-${pairId}-a`, [`trial-${pairId}-b`]: `contestant-${pairId}-b` },
    };
  },
  leaderboard: async () => [{ contestant_id: 'contestant-pair-1-a', rating: 1.2 + votes.length / 100, low: 0.8, high: 1.6, comparisons: votes.length }],
}} /></main></StrictMode>);
