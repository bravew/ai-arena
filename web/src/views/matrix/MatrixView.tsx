import { useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import '../leaderboard/summary.css';
import { Link } from 'react-router';
import type { Bundle } from '../../lib/schema';
import { contestantColor } from '../../theme';
import { sortMatrixRows } from '../leaderboard/summary';

export function MatrixView({ bundle }: { bundle: Bundle }) {
  const [query, setQuery] = useState('');
  const [sortByVariance, setSortByVariance] = useState(true);
  const rows = useMemo(() => sortMatrixRows(bundle), [bundle]);
  const visibleRows = rows.filter((row) => row.taskId.toLowerCase().includes(query.toLowerCase()));
  if (sortByVariance) visibleRows.sort((a, b) => b.variance - a.variance || a.taskId.localeCompare(b.taskId));
  else visibleRows.sort((a, b) => a.taskId.localeCompare(b.taskId));

  return <section className="panel summary-view" aria-labelledby="matrix-title">
    <div className="summary-heading"><div><div className="eyebrow">TASK × CONTESTANT</div><h2 id="matrix-title">Task matrix</h2><p>Find tasks that separate contestant scores, then open any scored cell in Compare.</p></div></div>
    <div className="matrix-controls"><label htmlFor="matrix-task-filter">Filter tasks</label><input id="matrix-task-filter" type="search" value={query} onChange={(event) => setQuery(event.currentTarget.value)} placeholder="Search task IDs" /><button type="button" aria-pressed={sortByVariance} onClick={() => setSortByVariance((value) => !value)}>{sortByVariance ? 'Sorted by variance' : 'Sort by variance'}</button></div>
    {visibleRows.length === 0 ? <p className="summary-empty">{rows.length ? 'No tasks match this filter.' : 'No tasks are available in this bundle.'}</p> : <div className="table-wrap matrix-wrap"><table className="summary-table matrix-table"><thead><tr><th scope="col">Task · variance {sortByVariance ? '↓' : '↕'}</th>{bundle.contestants.map((contestant) => <th scope="col" key={contestant.id}><span className="summary-identity"><i style={{ backgroundColor: contestantColor(contestant.id) }} aria-hidden="true" />{contestant.label ?? contestant.id}</span></th>)}</tr></thead><tbody>{visibleRows.map((row) => <tr key={row.taskId}><th scope="row"><span className="mono">{row.taskId}</span><small>{row.variance.toFixed(4)} variance</small></th>{row.cells.map((cell) => {
      const average = cell.score;
      const ci = cell.confidenceInterval;
      return <td key={cell.contestant.id} className="matrix-cell">{average === null ? <span className="matrix-missing" aria-label="No score">—</span> : <Link className="matrix-score" to={`/compare?task=${encodeURIComponent(row.taskId)}&contestant=${encodeURIComponent(cell.contestant.id)}&trials=${encodeURIComponent(cell.trialIds.join(','))}`} style={{ '--score-intensity': Math.max(0.12, Math.min(0.82, Math.abs(average))) } as CSSProperties} title={`${cell.contestant.label ?? cell.contestant.id}: ${average.toFixed(3)}; ${ci === null ? 'CI unavailable' : `95% CI ${ci.low.toFixed(3)} to ${ci.high.toFixed(3)}`}`} aria-label={`Open Compare for ${row.taskId}, ${cell.contestant.label ?? cell.contestant.id}, score ${average.toFixed(3)}`}><strong>{average.toFixed(2)}</strong><small>{ci === null ? 'CI unavailable' : `${ci.low.toFixed(2)}–${ci.high.toFixed(2)}`}</small></Link>}</td>;
    })}</tr>)}</tbody></table></div>}
  </section>;
}
