import { Link } from 'react-router';
import type { CSSProperties } from 'react';
import { contestantColor } from '../theme';
import { useBundleState } from './state';

export function HomePage() {
  const { bundle, validation } = useBundleState();
  if (validation?.ok === false) return <ValidationError issues={validation.issues} />;
  const contestants = bundle?.contestants ?? [];
  const trials = bundle?.trials ?? [];
  const calls = bundle?.calls ?? [];

  return (
    <>
      <section className="welcome-row">
        <div>
          <div className="eyebrow">RUN · {bundle?.run.id ?? 'NO BUNDLE'}</div>
          <h2>{bundle?.run.suite_id ?? 'Load a report bundle'}</h2>
          <p>Compare models and agents with a shared view of every run.</p>
        </div>
        <div className="run-state"><span className="state-dot" aria-hidden="true" /> {bundle?.run.status ?? 'Waiting for data'}</div>
      </section>

      <section className="metric-grid" aria-label="Run summary">
        <Metric label="Contestants" value={String(contestants.length).padStart(2, '0')} note="in this run" />
        <Metric label="Trials" value={String(trials.length).padStart(2, '0')} note={`${trials.filter((t) => t.status === 'succeeded').length} succeeded`} />
        <Metric label="Model calls" value={String(calls.length).padStart(2, '0')} note="across all trials" />
        <Metric label="Schema" value="v1" note="bundle + event stream" />
      </section>

      <section className="content-grid">
        <article className="panel quick-panel">
          <div className="panel-heading">
            <div><div className="eyebrow">START EXPLORING</div><h3>Run views</h3></div>
            <span className="panel-icon" aria-hidden="true">↗</span>
          </div>
          <div className="view-grid">
            <ViewLink path="/leaderboard" title="Leaderboard" desc="Scores, confidence and cost" />
            <ViewLink path="/matrix" title="Task matrix" desc="Compare tasks by contestant" />
            <ViewLink path="/compare" title="Side by side" desc="Inspect outputs together" />
            <ViewLink path="/sessions" title="Sessions" desc="Turns, tools and skills" />
          </div>
        </article>

        <article className="panel contestant-panel">
          <div className="panel-heading">
            <div><div className="eyebrow">IN THE ARENA</div><h3>Contestants</h3></div>
            <span className="count-pill">{contestants.length} total</span>
          </div>
          <ul className="contestant-list">
            {contestants.map((contestant) => (
              <li key={contestant.id}>
                <span className="contestant-swatch" style={{ '--contestant-color': contestantColor(contestant.id) } as CSSProperties} aria-hidden="true" />
                <span className="contestant-detail"><strong>{contestant.label ?? contestant.id}</strong><small>{contestant.model} <span aria-hidden="true">·</span> {contestant.scaffold?.id ?? 'direct'}</small></span>
                <span className="subtle-arrow" aria-hidden="true">→</span>
              </li>
            ))}
            {contestants.length === 0 && <li className="empty-row">Load a valid bundle to see contestants.</li>}
          </ul>
        </article>
      </section>

      <section className="panel recent-panel">
        <div className="panel-heading">
          <div><div className="eyebrow">LATEST ACTIVITY</div><h3>Recent trials</h3></div>
          <Link className="text-link" to="/live">Open live view <span aria-hidden="true">→</span></Link>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Task</th><th>Contestant</th><th>Attempt</th><th>Status</th></tr></thead>
            <tbody>
              {trials.slice(0, 5).map((trial) => {
                const contestant = contestants.find((c) => c.id === trial.contestant_id);
                return (
                  <tr key={trial.id}>
                    <td className="mono">{trial.task_id}</td>
                    <td>{contestant?.label ?? trial.contestant_id}</td>
                    <td>#{trial.attempt}</td>
                    <td><Status value={trial.status} /></td>
                  </tr>
                );
              })}
              {trials.length === 0 && <tr><td colSpan={4} className="empty-row">No trials in this bundle yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

export function PlaceholderPage({ title }: { title: string }) {
  return <section className="panel placeholder-panel"><div className="eyebrow">VIEWER WORKSPACE</div><h2>{title}</h2><p>This view is ready for the report bundle and plugs into the shared shell.</p></section>;
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return <article className="metric-card"><span className="metric-label">{label}</span><strong>{value}</strong><span className="metric-note">{note}</span></article>;
}

function ViewLink({ path, title, desc }: { path: string; title: string; desc: string }) {
  return <Link className="view-link" to={path}><span><strong>{title}</strong><small>{desc}</small></span><span aria-hidden="true">↗</span></Link>;
}

function Status({ value }: { value: string }) {
  const kind = value === 'succeeded' ? 'success' : value === 'running' ? 'running' : value === 'failed' || value === 'errored' || value === 'timeout' ? 'danger' : 'queued';
  return <span className={`status-chip status-${kind}`}><i aria-hidden="true" />{value}</span>;
}

function ValidationError({ issues }: { issues: { path: string; message: string }[] }) {
  return <section className="panel validation-error" role="alert"><div className="eyebrow">BUNDLE ERROR</div><h2>Could not read this report bundle</h2><p>Check the bundle against the published schema before opening the viewer.</p><ul>{issues.slice(0, 12).map((i, n) => <li key={`${i.path}-${n}`}><code>{i.path || '/'}</code> {i.message}</li>)}</ul></section>;
}

