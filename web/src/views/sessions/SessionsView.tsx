import { useMemo, useState } from 'react';
import type { Bundle } from '../../lib/schema';
import { alignSessionTurns, getSessionRows, getTurnMetrics, kitLabel } from './session-data';
import './sessions.css';

export function SessionsView({ bundle }: { bundle: Bundle }) {
  const [kit, setKit] = useState('All kits');
  const [selected, setSelected] = useState<string | null>(bundle.sessions[0]?.id ?? null);
  const rows = useMemo(() => getSessionRows(bundle), [bundle]);
  const kits = [...new Set(rows.map((row) => kitLabel(bundle, row.session.trial_id)))];
  const filtered = kit === 'All kits' ? rows : rows.filter((row) => kitLabel(bundle, row.session.trial_id) === kit);
  const current = filtered.find((row) => row.session.id === selected) ?? filtered[0];
  const [kitMenuOpen, setKitMenuOpen] = useState(false);
  const [compareWith, setCompareWith] = useState('');
  const [compareMenuOpen, setCompareMenuOpen] = useState(false);
  const comparisonRows = rows.filter((row) => row.session.id !== current?.session.id && row.trial?.task_id === current?.trial?.task_id);
  const comparison = comparisonRows.find((row) => row.session.id === compareWith);
  return <section className="panel session-view" aria-labelledby="sessions-title">
    <header className="session-heading"><div><div className="eyebrow">SESSION OBSERVABILITY</div><h2 id="sessions-title">Sessions</h2><p>Turns, tools, skills and gateway usage for every agent session.</p></div>
      <div className="session-filter"><span>Kit</span><div className="kit-filter"><button type="button" aria-label="Filter by kit" aria-expanded={kitMenuOpen} onClick={() => setKitMenuOpen((open) => !open)}>{kit} <span aria-hidden="true">⌄</span></button>{kitMenuOpen && <div role="listbox" aria-label="Kits" className="kit-options">{['All kits', ...kits].map((value) => <button key={value} type="button" role="option" aria-selected={kit === value} onClick={() => { setKit(value); setKitMenuOpen(false); }}>{value}</button>)}</div>}</div></div>
    </header>
    <div className="session-layout"><aside className="session-list" aria-label="Sessions">
      <div className="eyebrow">{filtered.length} SESSIONS</div>
      {filtered.map((row) => <button key={row.session.id} type="button" className={`session-choice${current?.session.id === row.session.id ? ' selected' : ''}`} onClick={() => setSelected(row.session.id)}>
        <strong>{row.trial?.task_id ?? row.session.trial_id}</strong><span>{row.contestantLabel}</span><small>{row.session.agent} · {kitLabel(bundle, row.session.trial_id)}</small>
      </button>)}
      {filtered.length === 0 && <p className="summary-empty">No sessions match this kit.</p>}
    </aside>
    {current ? <article className="session-timeline"><header className="timeline-heading"><div><div className="eyebrow">{current.session.agent} · {current.session.status}</div><h3>{current.trial?.task_id ?? current.session.trial_id}</h3><p>{current.contestantLabel} · {kitLabel(bundle, current.session.trial_id)}</p></div><div className="session-totals"><strong>{current.totalTokens.toLocaleString()} tokens</strong><span>{current.totalCost === null ? 'Cost unavailable' : `$${current.totalCost.toFixed(4)}`}</span>{current.partial && <span className="partial-badge">partial</span>}</div></header>
      <div className="session-compare"><span>Compare with</span><button type="button" aria-label="Compare with session" aria-expanded={compareMenuOpen} onClick={() => setCompareMenuOpen((open) => !open)}>{comparison?.session.id ?? 'Select a session'} <span aria-hidden="true">⌄</span></button>{compareMenuOpen && <div className="compare-options" role="listbox" aria-label="Comparison sessions">{comparisonRows.map((row) => <button key={row.session.id} type="button" role="option" aria-selected={compareWith === row.session.id} onClick={() => { setCompareWith(row.session.id); setCompareMenuOpen(false); }}>{row.session.agent} · {row.session.id}</button>)}</div>}</div>
      {comparison && <SessionComparison left={current} right={comparison} bundle={bundle} />}
      <ol className="turn-list">{current.session.turns.map((turn, index) => {
        const metrics = getTurnMetrics(bundle, turn.call_ids);
        return <li className="turn-card" key={`${current.session.id}-${index}`}><div className="turn-index"><span>TURN</span><strong>{index + 1}</strong></div><div className="turn-content"><div className="turn-metrics">{metrics.tokens.toLocaleString()} tokens · {metrics.cost === null ? 'cost unavailable' : `$${metrics.cost.toFixed(4)}`}{turn.unmetered && <span className="partial-badge">unmetered</span>}</div>
          {metrics.calls.map((call) => <div className="turn-call" key={call.id}><span>{call.model_asked}</span><small>{call.total_ms} ms · {call.ttft_ms === null || call.ttft_ms === undefined ? 'TTFT unavailable' : `${call.ttft_ms} ms TTFT`}</small></div>)}
          {turn.tool_calls.length > 0 && <div className="turn-detail"><b>Tools</b> {turn.tool_calls.map((tool) => <span key={`${tool.name}-${tool.args_digest}`}>{tool.name}{tool.exit_status !== null && tool.exit_status !== 0 ? ' · failed' : ''}</span>)}</div>}
          {turn.mcp_calls.length > 0 && <div className="turn-detail"><b>MCP</b> {turn.mcp_calls.map((call) => <span key={`${call.server}-${call.tool}`}>{call.server}.{call.tool} · {call.status}</span>)}</div>}
          {turn.skill_events.length > 0 && <div className="turn-detail skill-events"><b>Skills</b>{turn.skill_events.map((event, i) => <span key={`${event.skill}-${event.kind}-${i}`} title={`${event.kind} ${event.skill}`}><i aria-hidden="true">{event.kind === 'listed' ? '○' : event.kind === 'loaded' ? '◐' : '●'}</i> {event.skill} <small>({event.kind})</small></span>)}</div>}
          {turn.files.length > 0 && <div className="turn-detail"><b>Files</b>{turn.files.map((file) => <span key={file.path}>{file.path} <small>+{file.added} −{file.removed}</small></span>)}</div>}
        </div></li>;
      })}</ol>
    </article> : <p className="summary-empty">Select a session to inspect its turns.</p>}</div>
  </section>;
}

function SessionComparison({ left, right, bundle }: { left: ReturnType<typeof getSessionRows>[number]; right: ReturnType<typeof getSessionRows>[number]; bundle: Bundle }) {
  return <section className="session-comparison" aria-label="Session comparison aligned by turn">
    <h4>Compare sessions · aligned by turn</h4>
    <div className="comparison-headings"><span aria-hidden="true" /><strong>{left.session.agent} · {left.session.status}</strong><strong>{right.session.agent} · {right.session.status}</strong></div>
    {alignSessionTurns(left.session, right.session).map(({ turn, left: leftTurn, right: rightTurn }) => {
      const leftMetrics = leftTurn ? getTurnMetrics(bundle, leftTurn.call_ids) : null;
      const rightMetrics = rightTurn ? getTurnMetrics(bundle, rightTurn.call_ids) : null;
      return <div className="comparison-row" key={turn}><span>Turn {turn}</span><span>{leftTurn ? `${leftMetrics!.tokens.toLocaleString()} tokens · ${leftTurn.skill_events.map((event) => `${event.skill} (${event.kind})`).join(', ') || 'No skill events'}` : 'No turn'}</span><span>{rightTurn ? `${rightMetrics!.tokens.toLocaleString()} tokens · ${rightTurn.skill_events.map((event) => `${event.skill} (${event.kind})`).join(', ') || 'No skill events'}` : 'No turn'}</span></div>;
    })}
  </section>;
}
