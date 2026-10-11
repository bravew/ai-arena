import { useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import '../leaderboard/summary.css';
import type { Bundle } from '../../lib/schema';
import { contestantColor } from '../../theme';
import { calculateSummaries, getParetoFrontier } from '../leaderboard/summary';

export function ParetoView({ bundle }: { bundle: Bundle }) {
  const [axis, setAxis] = useState<'tokens' | 'latency'>('tokens');
  const summaries = useMemo(() => calculateSummaries(bundle), [bundle]);
  const frontier = getParetoFrontier(summaries, axis);
  const plotted = summaries.filter((item) => item.meanScore !== null && (axis === 'tokens' ? item.tokensPerTask !== null : item.p50LatencyMs !== null));
  const maxX = Math.max(1, ...plotted.map((item) => axis === 'tokens' ? item.tokensPerTask ?? 0 : item.p50LatencyMs ?? 0));
  const allFlat = plotted.length > 0 && plotted.every((item) => item.costPerTask === 'flat');
  return <section className="panel summary-view" aria-labelledby="pareto-title">
    <div className="summary-heading"><div><div className="eyebrow">QUALITY × EFFICIENCY</div><h2 id="pareto-title">Pareto frontier</h2><p>Higher quality and lower resource use are preferred.</p></div>
      <div className="summary-tabs" role="group" aria-label="Efficiency axis">{(['tokens', 'latency'] as const).map((item) => <button key={item} aria-pressed={axis === item} onClick={() => setAxis(item)} type="button">{item === 'tokens' ? 'Tokens / task' : 'p50 latency'}</button>)}</div>
    </div>
    {allFlat && <p className="summary-note">All contestants use flat-fee pricing; the frontier uses tokens per task.</p>}
    {plotted.length === 0 ? <p className="summary-empty">No scored contestants with usage data are available for this axis.</p> : <>
      <div className="pareto-plot" role="img" aria-label={`Pareto plot of quality by ${axis === 'tokens' ? 'tokens per task' : 'median latency'}`}>
        <span className="plot-y-label">Quality · mean normalized score</span><span className="plot-x-label">{axis === 'tokens' ? 'Tokens per task →' : 'p50 latency in milliseconds →'}</span>
        {plotted.map((item) => {
          const x = axis === 'tokens' ? item.tokensPerTask ?? 0 : item.p50LatencyMs ?? 0;
          const y = item.meanScore ?? 0;
          const isFrontier = frontier.includes(item);
          return <span key={item.contestant.id} className={`pareto-point${isFrontier ? ' is-frontier' : ''}`} style={{ left: `${6 + (x / maxX) * 82}%`, bottom: `${12 + Math.max(0, Math.min(100, y * 75))}%`, '--point-color': contestantColor(item.contestant.id) } as CSSProperties} title={`${item.contestant.label ?? item.contestant.id}: quality ${y.toFixed(3)}, ${axis === 'tokens' ? `${Math.round(x)} tokens/task` : `${Math.round(x)} ms`}${item.costPerTask === 'flat' ? ', flat' : ''}`} aria-label={`${item.contestant.label ?? item.contestant.id}${isFrontier ? ', on frontier' : ''}`}><i aria-hidden="true" />{isFrontier && <small>{item.contestant.label ?? item.contestant.id}</small>}</span>;
        })}
      </div>
      <ul className="summary-legend" aria-label="Contestants">{plotted.map((item) => <li key={item.contestant.id}><i style={{ backgroundColor: contestantColor(item.contestant.id) }} aria-hidden="true" />{item.contestant.label ?? item.contestant.id}{item.costPerTask === 'flat' ? ' · flat' : ''}</li>)}</ul>
      <div className="table-wrap"><table className="summary-table"><thead><tr><th scope="col">Contestant</th><th scope="col">Quality · mean score</th><th scope="col">95% CI</th><th scope="col">Tokens / task</th><th scope="col">Price / task</th><th scope="col">p50 latency</th><th scope="col">Frontier</th></tr></thead><tbody>{plotted.map((item) => <tr key={item.contestant.id}><td>{item.contestant.label ?? item.contestant.id}</td><td>{item.meanScore?.toFixed(3)}</td><td>{item.confidenceInterval ? `${item.confidenceInterval.low.toFixed(3)} – ${item.confidenceInterval.high.toFixed(3)}` : 'CI unavailable'}</td><td>{item.tokensPerTask?.toFixed(0) ?? '—'}</td><td>{item.costPerTask === 'flat' ? 'flat' : item.costPerTask === null ? '—' : `$${item.costPerTask.toFixed(4)}`}</td><td>{item.p50LatencyMs === null ? '—' : `${item.p50LatencyMs.toFixed(0)} ms`}</td><td>{frontier.includes(item) ? 'On frontier' : '—'}</td></tr>)}</tbody></table></div>
    </>}
  </section>;
}
