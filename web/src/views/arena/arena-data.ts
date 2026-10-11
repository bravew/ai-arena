export type VoteChoice = 'A' | 'B' | 'tie' | 'both_bad';

export interface BlindSubmission {
  content: string;
}

/** Contains only data safe to show before a vote is cast. */
export interface BlindPair {
  pair_id: string;
  task_id: string;
  left: BlindSubmission;
  right: BlindSubmission;
}

export interface VoteReveal {
  vote: {
    choice: VoteChoice;
  };
  revealed: Record<string, string>;
}

export interface HumanLeaderboardEntry {
  contestant_id: string;
  rating: number;
  low: number;
  high: number;
  comparisons: number;
}

/** The implementation owns all persistence and transport details. */
export interface ArenaDataSource {
  nextPair(): Promise<BlindPair | null>;
  castVote(pairId: string, choice: VoteChoice): Promise<VoteReveal>;
  leaderboard(): Promise<readonly HumanLeaderboardEntry[]>;
}
