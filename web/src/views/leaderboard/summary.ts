import type { Bundle, Contestant } from '../../lib/schema';

export interface ContestantSummary {
  contestant: Contestant;
  meanScore: number | null;
  confidenceInterval: { low: number; high: number } | null;
  passK: number | null;
  costPerTask: number | 'flat' | null;
  tokensPerTask: number | null;
  p50LatencyMs: number | null;
  scoreByTrial: Map<string, number>;
  pairedDifferenceFromLeader: { low: number; high: number } | null;
}

export interface MatrixCell {
  contestant: Contestant;
  score: number | null;
  confidenceInterval: { low: number; high: number } | null;
  trialIds: string[];
}

export interface MatrixRow {
  taskId: string;
  variance: number;
  cells: MatrixCell[];
}

const mean = (values: number[]): number | null => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;

function quantile(values: number[], probability: number): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.min(ordered.length - 1, Math.floor((ordered.length - 1) * probability))] ?? null;
}

function clusterBootstrapInterval(clusters: number[][], seed: string): { low: number; high: number } | null {
  if (clusters.length < 2 || clusters.some((cluster) => cluster.length === 0)) return null;
  let state = [...seed].reduce((hash, char) => Math.imul(hash ^ (char.codePointAt(0) ?? 0), 16777619) >>> 0, 2166136261);
  const random = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
  const estimates: number[] = [];
  const iterations = 2000;
  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const sampledTasks: number[] = [];
    for (let task = 0; task < clusters.length; task += 1) {
      const selected = clusters[Math.floor(random() * clusters.length)]!;
      const sampledRepeats = Array.from({ length: selected.length }, () => selected[Math.floor(random() * selected.length)]!);
      sampledTasks.push(mean(sampledRepeats) ?? 0);
    }
    estimates.push(mean(sampledTasks) ?? 0);
  }
  const low = quantile(estimates, 0.025);
  const high = quantile(estimates, 0.975);
  return low === null || high === null ? null : { low, high };
}

function trialScores(bundle: Bundle, trials: Bundle['trials']): Map<string, number> {
  const trialIds = new Set(trials.map((trial) => trial.id));
  const scoreValuesByTrial = new Map<string, number[]>();
  for (const score of bundle.scores) {
    if (!trialIds.has(score.trial_id)) continue;
    const values = scoreValuesByTrial.get(score.trial_id) ?? [];
    values.push(score.normalized);
    scoreValuesByTrial.set(score.trial_id, values);
  }
  return new Map([...scoreValuesByTrial].flatMap(([trialId, values]) => {
    const score = mean(values);
    return score === null ? [] : [[trialId, score] as const];
  }));
}

function tasksByTrialScore(bundle: Bundle, trials: Bundle['trials'], scores: Map<string, number>): Map<string, number[]> {
  const tasks = new Map<string, number[]>();
  for (const trial of trials) {
    const score = scores.get(trial.id);
    if (score === undefined) continue;
    const values = tasks.get(trial.task_id) ?? [];
    values.push(score);
    tasks.set(trial.task_id, values);
  }
  return tasks;
}

function taskClusters(tasks: Map<string, number[]>): number[][] {
  return [...tasks.keys()].sort().map((task) => tasks.get(task) ?? []);
}

function pairedTaskClusters(
  bundle: Bundle,
  leader: ContestantSummary,
  summary: ContestantSummary,
  leaderTrials: Bundle['trials'],
): number[][] {
  const paired = new Map<string, number[]>();
  for (const trial of leaderTrials) {
    const other = bundle.trials.find((candidate) => candidate.contestant_id === summary.contestant.id && candidate.task_id === trial.task_id && candidate.attempt === trial.attempt);
    const lhs = leader.scoreByTrial.get(trial.id);
    const rhs = other ? summary.scoreByTrial.get(other.id) : undefined;
    if (lhs === undefined || rhs === undefined) continue;
    const values = paired.get(trial.task_id) ?? [];
    values.push(rhs - lhs);
    paired.set(trial.task_id, values);
  }
  return taskClusters(paired);
}

function passedTrial(bundle: Bundle, trial: Bundle['trials'][number]): boolean {
  const scored = bundle.scores.filter((score) => score.trial_id === trial.id && score.passed !== null);
  return scored.length ? scored.every((score) => score.passed === true) : trial.status === 'succeeded';
}

export function calculateSummaries(bundle: Bundle): ContestantSummary[] {
  const summaries: ContestantSummary[] = bundle.contestants.map((contestant) => {
    const trials = bundle.trials.filter((trial) => trial.contestant_id === contestant.id);
    const scores = trialScores(bundle, trials);
    const tasks = tasksByTrialScore(bundle, trials, scores);
    const taskMeans = [...tasks.values()].map((values) => mean(values)).filter((value): value is number => value !== null);
    const average = mean(taskMeans);
    const calls = bundle.calls.filter((call) => call.purpose === 'contestant' && call.trial_id && trials.some((trial) => trial.id === call.trial_id));
    const taskCount = new Set(trials.map((trial) => trial.task_id)).size;
    const costKnown = calls.filter((call) => call.cost_usd !== null);
    const tokenCount = calls.reduce((sum, call) => sum + call.tokens.in + call.tokens.out + call.tokens.reasoning + call.tokens.cache_read + call.tokens.cache_write, 0);
    const durations = trials.flatMap((trial) => {
      if (!trial.started_at || !trial.ended_at) return [];
      const duration = Date.parse(trial.ended_at) - Date.parse(trial.started_at);
      return Number.isFinite(duration) && duration >= 0 ? [duration] : [];
    });
    const passByTask = new Map<string, boolean[]>();
    for (const trial of trials) {
      const outcomes = passByTask.get(trial.task_id) ?? [];
      outcomes.push(passedTrial(bundle, trial));
      passByTask.set(trial.task_id, outcomes);
    }

    return {
      contestant,
      meanScore: average,
      confidenceInterval: clusterBootstrapInterval(taskClusters(tasks), `${contestant.id}:score`),
      passK: passByTask.size ? [...passByTask.values()].filter((outcomes) => outcomes.length > 0 && outcomes.every(Boolean)).length / passByTask.size : null,
      costPerTask: calls.length === 0 || taskCount === 0 ? null : costKnown.length === 0 ? 'flat' : costKnown.reduce((sum, call) => sum + (call.cost_usd ?? 0), 0) / taskCount,
      tokensPerTask: taskCount === 0 || calls.length === 0 ? null : tokenCount / taskCount,
      p50LatencyMs: quantile(durations, 0.5),
      scoreByTrial: scores,
      pairedDifferenceFromLeader: null,
    };
  });
  const leader = [...summaries].filter((item) => item.meanScore !== null).sort((a, b) => (b.meanScore ?? -Infinity) - (a.meanScore ?? -Infinity))[0];
  if (!leader) return summaries;
  const leaderTrials = bundle.trials.filter((trial) => trial.contestant_id === leader.contestant.id);
  for (const summary of summaries) {
    if (summary === leader) continue;
    const differences = pairedTaskClusters(bundle, leader, summary, leaderTrials);
    const interval = clusterBootstrapInterval(differences, `${leader.contestant.id}:${summary.contestant.id}:paired`);
    if (interval) summary.pairedDifferenceFromLeader = interval;
  }
  return summaries;
}

export function getParetoFrontier(summaries: ContestantSummary[], axis: 'tokens' | 'latency' = 'tokens'): ContestantSummary[] {
  const eligible = summaries.filter((item) => item.meanScore !== null && (axis === 'tokens' ? item.tokensPerTask !== null : item.p50LatencyMs !== null));
  return eligible.filter((candidate) => !eligible.some((other) => {
    if (candidate === other) return false;
    const candidateCost = axis === 'tokens' ? candidate.tokensPerTask ?? Infinity : candidate.p50LatencyMs ?? Infinity;
    const otherCost = axis === 'tokens' ? other.tokensPerTask ?? Infinity : other.p50LatencyMs ?? Infinity;
    return other.meanScore! >= candidate.meanScore! && otherCost <= candidateCost
      && (other.meanScore! > candidate.meanScore! || otherCost < candidateCost);
  }));
}

export function sortMatrixRows(bundle: Bundle): MatrixRow[] {
  return [...new Set(bundle.trials.map((trial) => trial.task_id))].map((taskId) => {
    const taskTrials = bundle.trials.filter((trial) => trial.task_id === taskId);
    const cells = bundle.contestants.map((contestant) => {
      const trials = taskTrials.filter((trial) => trial.contestant_id === contestant.id);
      const scores = trialScores(bundle, trials);
      const observations = trials.flatMap((trial) => {
        const value = scores.get(trial.id);
        return value === undefined ? [] : [value];
      });
      const average = mean(observations);
      const confidenceInterval = observations.length < 2 ? null : {
        low: Math.min(...observations),
        high: Math.max(...observations),
      };
      return {
        contestant,
        score: average,
        confidenceInterval,
        trialIds: trials.map((trial) => trial.id),
      };
    });
    const taskMeans = cells.flatMap((cell) => cell.score === null ? [] : [cell.score]);
    const average = mean(taskMeans) ?? 0;
    const variance = taskMeans.length > 1 ? taskMeans.reduce((sum, value) => sum + (value - average) ** 2, 0) / taskMeans.length : 0;
    return { taskId, variance, cells };
  }).sort((a, b) => b.variance - a.variance || a.taskId.localeCompare(b.taskId));
}
