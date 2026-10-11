import type { Bundle } from '../../lib/schema';

export interface KitEffectPair {
  baseline: string;
  kit: string;
  label: string;
  agent: string;
  task: string;
  difference: number;
  baselineScore: number;
  kitScore: number;
  skillsInvoked: number;
  skillsListed: number;
  costDelta: number | null;
}

export function buildPairs(bundle: Bundle): KitEffectPair[] {
  const pairCandidates = bundle.trials.flatMap((trial) => {
    const contestant = bundle.contestants.find((item) => item.id === trial.contestant_id);
    const kit = bundle.kit_installs.find((item) => item.trial_id === trial.id);
    if (!contestant || trial.flags.kit_unapplied || !kit || kit.kit_hash === 'none' || kit.kit_hash !== contestant.kit_hash) return [];
    const baselines = bundle.trials.filter((candidate) => {
      if (candidate.id === trial.id || candidate.run_id !== trial.run_id || candidate.task_id !== trial.task_id || candidate.attempt !== trial.attempt || candidate.flags.kit_unapplied) return false;
      const candidateContestant = bundle.contestants.find((item) => item.id === candidate.contestant_id);
      if (!candidateContestant || candidateContestant.kit_hash !== 'none') return false;
      if (bundle.kit_installs.some((item) => item.trial_id === candidate.id && item.kit_hash !== 'none')) return false;
      return sameConfiguration(contestant, candidateContestant);
    });
    return baselines.length === 1 ? [{ trial, contestant, baseline: baselines[0]! }] : [];
  });
  const baselineUseCount = new Map<string, number>();
  for (const candidate of pairCandidates) baselineUseCount.set(candidate.baseline.id, (baselineUseCount.get(candidate.baseline.id) ?? 0) + 1);

  return pairCandidates.flatMap(({ trial, contestant, baseline }) => {
    if (baselineUseCount.get(baseline.id) !== 1) return [];
    const scores = (id: string) => bundle.scores.filter((score) => score.trial_id === id).map((score) => score.normalized);
    const baseScore = scores(baseline.id);
    const kitScore = scores(trial.id);
    if (!baseScore.length || !kitScore.length) return [];
    const baseCalls = bundle.calls.filter((call) => call.trial_id === baseline.id);
    const kitCalls = bundle.calls.filter((call) => call.trial_id === trial.id);
    const cost = (calls: typeof baseCalls) => calls.every((call) => call.cost_usd !== null) ? calls.reduce((sum, call) => sum + (call.cost_usd ?? 0), 0) : null;
    const skillEvents = bundle.sessions.filter((session) => session.trial_id === trial.id).flatMap((session) => session.turns.flatMap((turn) => turn.skill_events));
    const baselineScore = baseScore.reduce((a, b) => a + b, 0) / baseScore.length;
    const kitScoreMean = kitScore.reduce((a, b) => a + b, 0) / kitScore.length;
    return [{ baseline: baseline.id, kit: trial.id, label: contestant.label ?? contestant.model, agent: contestant.scaffold?.id ?? contestant.id, task: trial.task_id, baselineScore, kitScore: kitScoreMean, difference: kitScoreMean - baselineScore, skillsInvoked: skillEvents.filter((event) => event.kind === 'invoked').length, skillsListed: skillEvents.filter((event) => event.kind === 'listed').length, costDelta: cost(kitCalls) === null || cost(baseCalls) === null ? null : (cost(kitCalls) ?? 0) - (cost(baseCalls) ?? 0) }];
  });
}

function sameConfiguration(left: Bundle['contestants'][number], right: Bundle['contestants'][number]): boolean {
  return left.model === right.model
    && left.scaffold?.id === right.scaffold?.id
    && left.scaffold?.version === right.scaffold?.version
    && left.scaffold_prompt === right.scaffold_prompt
    && left.orchestration === right.orchestration
    && left.prompt_version === right.prompt_version
    && JSON.stringify(left.params) === JSON.stringify(right.params)
    && JSON.stringify(left.scaffold?.settings ?? null) === JSON.stringify(right.scaffold?.settings ?? null)
    && JSON.stringify([...left.hooks].sort()) === JSON.stringify([...right.hooks].sort());
}
