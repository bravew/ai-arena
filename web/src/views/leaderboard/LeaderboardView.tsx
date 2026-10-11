import { useMemo, useState } from 'react';
import './summary.css';
import type { Bundle } from '../../lib/schema';
import { contestantColor } from '../../theme';
import { calculateSummaries } from './summary';

export function LeaderboardView({ bundle }: { bundle: Bundle }) {
  const [mode, setMode] = useState<'score' | 'judge' | 'human'>('score');
  const summaries = useMemo(() => calculateSummaries(bundle), [bundle]);
  const ranked = [...summaries].sort((a, b) => (b.meanScore ?? -Infinity) - (a.meanScore ?? -Infinity));

  return <section className="panel summary-view" aria-labelledby="leaderboard-title">
    <div className="summary-heading"><div><div className="eyebrow">RUN SUMMARY</div><h2 id="leaderboard-title">Leaderboard</h2><p>Scores include uncertainty; small samples stay unranked.</p></div>
      <div className="summary-tabs" role="group" aria-label="Leaderboard measure">
        {(['score', 'judge', 'human'] as const).map((item) => <button key={item} type="button" aria-pressed={mode === item} onClick={() => setMode(item)}>{item === 'score' ? 'Pointwise score' : item === 'judge' ? 'BT rating · judge' : 'BT rating · human'}</button>)}
      </div>
    </div>
    {mode !== 'score' && <p className="summary-note" role="status">{mode === 'judge' ? 'Judge Bradley–Terry ratings are not included in this bundle.' : 'Human Bradley–Terry ratings are not included in this bundle.'}</p>}
    {summaries.length === 0 ? <p className="summary-empty">No contestants in this bundle.</p> : <div className="table-wrap"><table className="summary-table">
      <thead><tr><th scope="col">Rank</th><th scope="col">Contestant</th><th scope="col">Mean score · 95% CI</th><th scope="col">Pass^k</th><th scope="col">Cost / task</th><th scope="col">p50 latency</th><th scope="col">Difference from #1</th></tr></thead>
      <tbody>{ranked.map((item, index) => {
        const score = mode === 'score' ? item.meanScore : null;
        const interval = mode === 'score' ? item.confidenceInterval : null;
        const difference = item.pairedDifferenceFromLeader;
        const comparison = index === 0 ? 'Leader' : difference === null ? 'Insufficient paired trials' : difference.low <= 0 && difference.high >= 0 ? 'No detectable difference' : 'Detectable difference';
        return <tr key={item.contestant.id}>
          <td>{item.meanScore === null || mode !== 'score' ? '—' : index + 1}</td>
          <td><span className="summary-identity"><i style={{ backgroundColor: contestantColor(item.contestant.id) }} aria-hidden="true" />{item.contestant.label ?? item.contestant.id}</span></td>
          <td>{score === null ? '—' : <><strong>{score.toFixed(3)}</strong><span className="ci-value">{interval ? `${interval.low.toFixed(3)} – ${interval.high.toFixed(3)}` : 'CI unavailable · need ≥2 scored trials'}</span>{interval && <span className="ci-track" aria-label={`95% confidence interval ${interval.low.toFixed(3)} to ${interval.high.toFixed(3)}`}><i style={{ left: `${Math.max(0, Math.min(100, interval.low * 100))}%`, width: `${Math.max(1, Math.min(100, (interval.high - interval.low) * 100))}%` }} /></span>}</>}</td>
          <td>{item.passK === null ? '—' : `${(item.passK * 100).toFixed(0)}%`}</td><td>{item.costPerTask === null ? '—' : item.costPerTask === 'flat' ? 'flat' : `$${item.costPerTask.toFixed(4)}`}</td><td>{item.p50LatencyMs === null ? '—' : `${(item.p50LatencyMs / 1000).toFixed(2)} s`}</td>
          <td>{score === null ? 'Not enough data' : comparison}</td>
        </tr>;
      })}</tbody></table></div>}
  </section>;
}
