import { useMemo } from 'react';
import type { Bundle } from '../../lib/schema';
import './kit-effect.css';

interface Pair {
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

function buildPairs(bundle: Bundle): Pair[] {
  const pairs: Pair[] = [];
  for (const trial of bundle.trials) {
    const contestant = bundle.contestants.find((item) => item.id === trial.contestant_id);
    if (!contestant || trial.flags.kit_unapplied) continue;
    const kit = bundle.kit_installs.find((item) => item.trial_id === trial.id);
    if (!kit || kit.kit_hash === 'none') continue;
    const baseline = bundle.trials.find((candidate) => candidate.task_id === trial.task_id && candidate.contestant_id !== trial.contestant_id && !candidate.flags.kit_unapplied && !bundle.kit_installs.some((item) => item.trial_id === candidate.id && item.kit_hash !== 'none'));
    if (!baseline) continue;
    const scores = (id: string) => bundle.scores.filter((score) => score.trial_id === id).map((score) => score.normalized);
    const baseScore = scores(baseline.id);
    const kitScore = scores(trial.id);
    if (!baseScore.length || !kitScore.length) continue;
    const baseCalls = bundle.calls.filter((call) => call.trial_id === baseline.id);
    const kitCalls = bundle.calls.filter((call) => call.trial_id === trial.id);
    const cost = (calls: typeof baseCalls) => calls.every((call) => call.cost_usd !== null) ? calls.reduce((sum, call) => sum + (call.cost_usd ?? 0), 0) : null;
    const skillEvents = bundle.sessions.filter((session) => session.trial_id === trial.id).flatMap((session) => session.turns.flatMap((turn) => turn.skill_events));
    pairs.push({ baseline: baseline.id, kit: trial.id, label: contestant.label ?? contestant.model, agent: contestant.scaffold?.id ?? contestant.id, task: trial.task_id, baselineScore: baseScore.reduce((a, b) => a + b, 0) / baseScore.length, kitScore: kitScore.reduce((a, b) => a + b, 0) / kitScore.length, difference: kitScore.reduce((a, b) => a + b, 0) / kitScore.length - baseScore.reduce((a, b) => a + b, 0) / baseScore.length, skillsInvoked: skillEvents.filter((event) => event.kind === 'invoked').length, skillsListed: skillEvents.filter((event) => event.kind === 'listed').length, costDelta: cost(kitCalls) === null || cost(baseCalls) === null ? null : (cost(kitCalls) ?? 0) - (cost(baseCalls) ?? 0) });
  }
  return pairs;
}

export function KitEffectView({ bundle }: { bundle: Bundle }) {
  const pairs = useMemo(() => buildPairs(bundle), [bundle]);
  const mean = pairs.length ? pairs.reduce((sum, pair) => sum + pair.difference, 0) / pairs.length : null;
  return <section className="panel kit-effect-view" aria-labelledby="kit-effect-title"><header className="kit-effect-heading"><div><div className="eyebrow">ABLATION · PAIRED TRIALS</div><h2 id="kit-effect-title">Kit effect</h2><p>Score changes compare the same task with and without an applied kit.</p></div></header>
    <p className="observational-note"><strong>Observational split</strong> · Invoked and not-invoked groups describe observed skill use; they do not establish that invocation caused the score difference.</p>
    {mean !== null && <div className="kit-effect-summary"><span>Mean paired score difference</span><strong className={mean >= 0 ? 'positive' : 'negative'}>{mean >= 0 ? '+' : ''}{mean.toFixed(3)}</strong><small>{pairs.length} paired task{pairs.length === 1 ? '' : 's'} · descriptive, no CI available</small></div>}
    {pairs.length === 0 ? <p className="summary-empty">No scored applied-kit and baseline trials are paired in this bundle.</p> : <div className="table-wrap"><table className="summary-table"><thead><tr><th>Model × agent</th><th>Task</th><th>Baseline</th><th>With kit</th><th>Difference</th><th>Skill uptake</th><th>Cost delta</th></tr></thead><tbody>{pairs.map((pair) => <tr key={`${pair.baseline}-${pair.kit}`}><td>{pair.label} · {pair.agent}</td><td>{pair.task}</td><td>{pair.baselineScore.toFixed(3)}</td><td>{pair.kitScore.toFixed(3)}</td><td>{pair.difference >= 0 ? '+' : ''}{pair.difference.toFixed(3)}</td><td>{pair.skillsInvoked} invoked / {pair.skillsListed} listed</td><td>{pair.costDelta === null ? 'Unavailable' : `${pair.costDelta >= 0 ? '+' : ''}$${pair.costDelta.toFixed(4)}`}</td></tr>)}</tbody></table></div>}
  </section>;
}
