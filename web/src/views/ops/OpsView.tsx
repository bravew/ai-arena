import { useEffect, useMemo, useState } from 'react';
import type { Call } from '../../lib/schema';
import { fixtureOps, type OpsDataSource, type UsagePoint } from './fixtures';
import './ops.css';

export function OpsView({ source = fixtureOps }: { source?: OpsDataSource }) {
  const [provider, setProvider] = useState('');
  const [status, setStatus] = useState('');
  const [statusMenuOpen, setStatusMenuOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const calls = source.calls();
  const filtered = useMemo(() => calls.filter((call) => (!provider || call.provider.includes(provider)) && (!status || String(call.status ?? 'pending') === status) && (!search || `${call.id} ${call.model_asked} ${call.account_id}`.toLowerCase().includes(search.toLowerCase()))), [calls, provider, search, status]);
  const usage = source.usage();
  const lanes = source.lanes();
  const nowSeconds = now / 1000;
  const resetsIn = (until: number) => {
    const remaining = Math.max(0, Math.ceil(until - nowSeconds));
    return `${Math.floor(remaining / 3600)}h ${Math.floor((remaining % 3600) / 60)}m`;
  };

  return <div className="ops-view">
    <section className="panel ops-panel" aria-labelledby="ops-ledger-title">
      <div className="ops-heading"><div><div className="eyebrow">GATEWAY · CALLS</div><h2 id="ops-ledger-title">Calls ledger</h2><p>Per-call attribution, token use, status and latency.</p></div><span className="count-pill">{filtered.length} / {calls.length} calls</span></div>
      <div className="ops-filters">
        <label>Provider<input aria-label="Filter by provider" value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="All providers" /></label>
        <div className="status-filter"><span>Status</span><button type="button" className="status-filter-trigger" aria-label="Filter by status" aria-expanded={statusMenuOpen} onClick={() => setStatusMenuOpen((open) => !open)}>{status || 'All statuses'} <span aria-hidden="true">⌄</span></button>{statusMenuOpen && <div className="status-filter-options" role="group" aria-label="Status options">{[['', 'All statuses'], ['200', '200'], ['429', '429'], ['pending', 'pending']].map(([value, label]) => <button key={value} type="button" aria-pressed={status === value} onClick={() => { setStatus(value!); setStatusMenuOpen(false); }}>{label}</button>)}</div>}</div>
        <label>Search<input aria-label="Search calls" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Model, call or account" /></label>
      </div>
      <div className="table-wrap"><table className="ops-table" aria-label="Calls ledger"><thead><tr><th>Seq</th><th>Provider / account</th><th>Model</th><th>Status</th><th>Tokens · in / out</th><th>Cost</th><th>Queue</th><th>Latency</th></tr></thead><tbody>{filtered.map((call) => <CallRow key={call.id} call={call} />)}{filtered.length === 0 && <tr><td colSpan={8} className="empty-row">No calls match these filters.</td></tr>}</tbody></table></div>
    </section>
    <section className="ops-grid">
      <article className="panel ops-panel"><div className="ops-heading"><div><div className="eyebrow">PRECOMPUTED SERIES</div><h2>Usage and latency</h2></div></div><div className="series-grid"><SeriesChart title="Token usage series" points={usage} value={(p) => p.tokens} unit="tokens" /><SeriesChart title="Latency series" points={usage} value={(p) => p.latencyMs} unit="ms" /></div></article>
      <article className="panel ops-panel"><div className="ops-heading"><div><div className="eyebrow">CAPACITY</div><h2>Provider lanes</h2></div></div><div className="lane-list">{Object.entries(lanes).map(([name, lane]) => <div className="lane-row" key={name}><div className="lane-top"><strong>{name}</strong>{lane.resting_until !== null && lane.resting_until > nowSeconds ? <span className="status-chip status-danger">Rest · {lane.rest_class} · {resetsIn(lane.resting_until)}</span> : <span className="status-chip status-success">Ready</span>}</div><div className="lane-meter" role="meter" aria-label={`${name} in flight`} aria-valuemin={0} aria-valuemax={lane.concurrency} aria-valuenow={lane.in_flight}><i style={{ width: `${Math.min(100, 100 * lane.in_flight / Math.max(1, lane.concurrency))}%` }} /></div><small>{lane.in_flight} in-flight / {lane.concurrency} limit <span>·</span> {lane.queued} queued</small></div>)}</div></article>
    </section>
    <section className="ops-grid">
      <article className="panel ops-panel"><div className="ops-heading"><div><div className="eyebrow">ACCOUNT QUOTAS</div><h2>Subscription meters</h2></div></div><div className="meter-list">{source.meters().map((meter) => { const resets = Math.max(0, new Date(meter.resetsAt).getTime() - now); return <div className="meter-row" key={`${meter.account}-${meter.model}`}><div className="lane-top"><strong>{meter.label}</strong><span>{meter.account}</span></div><small>{meter.model} · {Math.ceil(resets / 86_400_000)}d {Math.floor(resets % 86_400_000 / 3_600_000)}h until reset</small><div className="lane-meter" role="meter" aria-label={`${meter.label} usage`} aria-valuemin={0} aria-valuemax={meter.limit} aria-valuenow={meter.used}><i style={{ width: `${Math.min(100, 100 * meter.used / Math.max(1, meter.limit))}%` }} /></div><small>{meter.used.toLocaleString()} / {meter.limit.toLocaleString()} tokens</small></div>; })}</div></article>
      <article className="panel ops-panel"><div className="ops-heading"><div><div className="eyebrow">FAIL-CLOSED CALLABLES</div><h2>Hook stats</h2></div></div><div className="table-wrap"><table className="ops-table" aria-label="Hook stats"><thead><tr><th>Hook</th><th>Calls</th><th>Failures</th><th>Average</th></tr></thead><tbody>{source.hooks().map((hook) => <tr key={hook.hook}><td className="mono">{hook.hook}</td><td>{hook.calls}</td><td>{hook.failures}</td><td>{hook.averageUs.toFixed(0)} µs</td></tr>)}</tbody></table></div></article>
    </section>
  </div>;
}

function CallRow({ call }: { call: Call }) {
  const tokenCount = call.tokens.in + call.tokens.out + call.tokens.reasoning + call.tokens.cache_read + call.tokens.cache_write;
  return <tr><td className="mono">{call.seq}</td><td><strong>{call.provider}</strong><small>{call.account_id}</small></td><td>{call.model_asked}{call.swapped && <small>served {call.model_served}</small>}</td><td><span className={`status-chip ${call.status !== null && call.status >= 200 && call.status < 300 ? 'status-success' : 'status-danger'}`}>{call.status ?? call.error_class ?? 'pending'}</span></td><td>{tokenCount.toLocaleString()} <small>{call.tokens.in.toLocaleString()} / {call.tokens.out.toLocaleString()}</small></td><td>{call.cost_usd === null ? 'flat' : `$${call.cost_usd.toFixed(4)}`}</td><td>{call.queue_ms} ms</td><td>{call.total_ms.toLocaleString()} ms</td></tr>;
}

function SeriesChart({ title, points, value, unit }: { title: string; points: UsagePoint[]; value: (point: UsagePoint) => number; unit: string }) {
  const max = Math.max(1, ...points.map(value));
  return <div><h3>{unit === 'tokens' ? 'Usage · tokens' : 'Latency · total ms'}</h3><div className="ops-chart" role="img" aria-label={title}>{points.map((point) => <div className="ops-bar-column" key={point.label} title={`${point.label}: ${value(point).toLocaleString()} ${unit}`} tabIndex={0} aria-label={`${point.label}: ${value(point).toLocaleString()} ${unit}`}><i style={{ height: `${Math.max(3, 100 * value(point) / max)}%` }} /><small>{point.label}</small></div>)}</div><table className="ops-series-table"><caption>{title} values</caption><thead><tr><th>Time</th><th>{unit}</th></tr></thead><tbody>{points.map((point) => <tr key={point.label}><th>{point.label}</th><td>{value(point).toLocaleString()}</td></tr>)}</tbody></table></div>;
}
