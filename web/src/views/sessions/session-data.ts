import type { Bundle, Call, Session, Trial } from '../../lib/schema';

export interface SessionRow {
  session: Session;
  trial: Trial | undefined;
  contestantLabel: string;
  kitLabel: string;
  calls: Call[];
  totalTokens: number;
  totalCost: number | null;
  partial: boolean;
}

export function getSessionRows(bundle: Bundle): SessionRow[] {
  return bundle.sessions.map((session) => {
    const trial = bundle.trials.find((item) => item.id === session.trial_id);
    const contestant = bundle.contestants.find((item) => item.id === trial?.contestant_id);
    const calls = bundle.calls.filter((call) => session.turns.some((turn) => turn.call_ids.includes(call.id)));
    const costs = calls.map((call) => call.cost_usd);
    return {
      session,
      trial,
      contestantLabel: contestant?.label ?? contestant?.id ?? 'Unknown contestant',
      kitLabel: contestant?.kit_hash ?? 'Unknown kit',
      calls,
      totalTokens: calls.reduce((sum, call) => sum + call.tokens.in + call.tokens.out + call.tokens.reasoning + call.tokens.cache_read + call.tokens.cache_write, 0),
      totalCost: costs.length > 0 && costs.every((cost) => cost !== null) ? costs.reduce<number>((sum, cost) => sum + (cost ?? 0), 0) : null,
      partial: session.status === 'partial',
    };
  });
}

export function getTurnMetrics(bundle: Bundle, callIds: string[]) {
  const calls = bundle.calls.filter((call) => callIds.includes(call.id));
  const costValues = calls.map((call) => call.cost_usd);
  return {
    calls,
    tokens: calls.reduce((sum, call) => sum + call.tokens.in + call.tokens.out + call.tokens.reasoning + call.tokens.cache_read + call.tokens.cache_write, 0),
    cost: costValues.length > 0 && costValues.every((cost) => cost !== null) ? costValues.reduce<number>((sum, cost) => sum + (cost ?? 0), 0) : null,
  };
}

export function alignSessionTurns(left: Session, right: Session) {
  return Array.from({ length: Math.max(left.turns.length, right.turns.length) }, (_, index) => ({
    turn: index + 1,
    left: left.turns[index],
    right: right.turns[index],
  }));
}

export function kitLabel(bundle: Bundle, trialId: string): string {
  const install = bundle.kit_installs.find((item) => item.trial_id === trialId);
  return install?.kit_hash ?? 'No kit';
}
