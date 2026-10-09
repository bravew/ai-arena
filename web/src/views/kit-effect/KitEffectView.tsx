import { useMemo } from 'react';
import type { Bundle } from '../../lib/schema';
import { buildPairs } from './kit-effect-data';
import './kit-effect.css';

export function KitEffectView({ bundle }: { bundle: Bundle }) {
  const pairs = useMemo(() => buildPairs(bundle), [bundle]);
  const mean = pairs.length ? pairs.reduce((sum, pair) => sum + pair.difference, 0) / pairs.length : null;
  return <section className="panel kit-effect-view" aria-labelledby="kit-effect-title"><header className="kit-effect-heading"><div><div className="eyebrow">ABLATION · PAIRED TRIALS</div><h2 id="kit-effect-title">Kit effect</h2><p>Score changes compare the same task with and without an applied kit.</p></div></header>
    <p className="observational-note"><strong>Observational split</strong> · Invoked and not-invoked groups describe observed skill use; they do not establish that invocation caused the score difference.</p>
    {mean !== null && <div className="kit-effect-summary"><span>Mean paired score difference</span><strong className={mean >= 0 ? 'positive' : 'negative'}>{mean >= 0 ? '+' : ''}{mean.toFixed(3)}</strong><small>{pairs.length} paired task{pairs.length === 1 ? '' : 's'} · descriptive, no CI available</small></div>}
    {pairs.length === 0 ? <p className="summary-empty">No scored applied-kit and baseline trials are paired in this bundle.</p> : <div className="table-wrap"><table className="summary-table"><thead><tr><th>Model × agent</th><th>Task</th><th>Baseline</th><th>With kit</th><th>Difference</th><th>Skill uptake</th><th>Cost delta</th></tr></thead><tbody>{pairs.map((pair) => <tr key={`${pair.baseline}-${pair.kit}`}><td>{pair.label} · {pair.agent}</td><td>{pair.task}</td><td>{pair.baselineScore.toFixed(3)}</td><td>{pair.kitScore.toFixed(3)}</td><td>{pair.difference >= 0 ? '+' : ''}{pair.difference.toFixed(3)}</td><td>{pair.skillsInvoked} invoked / {pair.skillsListed} listed</td><td>{pair.costDelta === null ? 'Unavailable' : `${pair.costDelta >= 0 ? '+' : ''}$${pair.costDelta.toFixed(4)}`}</td></tr>)}</tbody></table></div>}
  </section>;
}
