import type { Bundle } from '../../lib/schema';
import './rundiff.css';

export function RunDiffView({ bundle }: { bundle: Bundle }) {
  return <section className="panel rundiff-view" aria-labelledby="rundiff-title"><div className="eyebrow">RUN A ↔ RUN B</div><h2 id="rundiff-title">Run diff</h2><p>This fixture contains one run. Run A is {bundle.run.id}; a second run must be loaded before a comparison is available.</p><div className="rundiff-single-run"><strong>{bundle.run.id}</strong><span>{bundle.run.suite_id}</span><span>{bundle.trials.length} trials</span></div><div className="table-wrap"><table className="summary-table rundiff-table"><thead><tr><th>Task</th><th>Outcome</th><th>Trials</th></tr></thead><tbody>{[...new Set(bundle.trials.map((trial) => trial.task_id))].sort().map((taskId) => { const trials = bundle.trials.filter((trial) => trial.task_id === taskId); return <tr key={taskId}><th scope="row">{taskId}</th><td>{trials.some((trial) => trial.status === 'succeeded') ? 'Succeeded' : [...new Set(trials.map((trial) => trial.status))].join(', ')}</td><td>{trials.length}</td></tr>; })}</tbody></table></div></section>;
}
