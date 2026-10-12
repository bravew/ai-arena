import { useCallback, useEffect, useRef, useState } from 'react';
import { launchApi, type LaunchApi, type LaunchJob, type LaunchOptions, type LaunchPlan } from './http-source';
import { errorMessage } from './settings-forms';

const active = (job: LaunchJob) => job.state === 'running';
const stateLabels: Record<string, string> = { running: 'Running', succeeded: 'Finished', failed: 'Failed', cancelled: 'Cancelled' };

export function RunLauncher({ csrf, api = launchApi, pollMs = 2000 }: { csrf: string; api?: LaunchApi; pollMs?: number }) {
  const [options, setOptions] = useState<LaunchOptions>();
  const [jobs, setJobs] = useState<LaunchJob[]>([]);
  const [error, setError] = useState('');
  const [picked, setPicked] = useState<string[]>([]);
  const [name, setName] = useState('');
  const [saved, setSaved] = useState('');
  const [suite, setSuite] = useState('');
  const [setName_, setSetName] = useState('');
  const [repeats, setRepeats] = useState(1);
  const [docker, setDocker] = useState(false);
  const [plan, setPlan] = useState<LaunchPlan | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  const guard = useCallback(async (work: () => Promise<void>) => {
    setBusy(true); setError('');
    try { await work(); } catch (cause) { if (mounted.current) setError(errorMessage(cause)); } finally { if (mounted.current) setBusy(false); }
  }, []);
  const reload = useCallback(async () => {
    const [next, list] = await Promise.all([api.options(), api.jobs()]);
    if (!mounted.current) return;
    setOptions(next); setJobs(list);
    setSuite((s) => s || next.suites[0]?.id || '');
    setSetName((s) => s || next.sets.find((x) => x.contestants.length > 0 && x.contestants.every((c) => c.runnable))?.name || '');
  }, [api]);
  useEffect(() => {
    mounted.current = true;
    void reload().catch((cause: unknown) => { if (mounted.current) setError(errorMessage(cause)); });
    return () => { mounted.current = false; };
  }, [reload]);
  const running = jobs.some(active);
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => { void api.jobs().then((list) => { if (mounted.current) setJobs(list); }).catch(() => undefined); }, pollMs);
    return () => clearInterval(timer);
  }, [running, api, pollMs]);
  const select = (setter: (value: string) => void) => (value: string) => { setter(value); setPlan(null); setConfirmed(false); };
  const chosen = options?.sets.find((s) => s.name === setName_);
  const needsConfirm = !!plan && plan.subscription_trials > 0;
  const canStart = !!plan && plan.startable && (!needsConfirm || confirmed) && !running && !busy;
  const nameOk = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name);
  if (!options) return <section className="panel run-launcher" aria-label="Run a comparison">{error ? <p role="alert" className="providers-error">{error}</p> : <p role="status">Loading run options…</p>}</section>;
  return <section className="panel run-launcher" aria-labelledby="run-launcher-title">
    <div className="eyebrow">COMPARE</div><h3 id="run-launcher-title">Run a comparison</h3>
    <p className="run-note">Choose contestants, preview the plan, then start. Saving a set and previewing a plan use no quota. Starting a run does.</p>
    {error && <p role="alert" className="providers-error">{error}</p>}
    <fieldset className="provider-form-fields run-step"><legend>1. Define the contestants</legend>
      {options.models.length === 0 && <p className="run-note">No subscription models are in the catalog yet. Add them under a subscription provider below.</p>}
      <div className="provider-choice">{options.models.map((m) => <label key={m.ref}><input type="checkbox" disabled={!m.enabled} checked={picked.includes(m.ref)} onChange={(e) => setPicked(e.target.checked ? [...picked, m.ref] : picked.filter((r) => r !== m.ref))} />{m.ref}</label>)}</div>
      <label>Set name<input value={name} onChange={(e) => { setName(e.target.value); setSaved(''); }} placeholder="my-comparison" /></label>
      <div className="provider-actions"><button type="button" disabled={busy || !picked.length || !nameOk} onClick={() => void guard(async () => {
        await api.saveSet(name, picked, false, csrf); setSaved(`Saved contestant set sets/${name}.`); await reload(); select(setSetName)(`sets/${name}`);
      })}>Save contestant set</button></div>
      {saved && <p role="status" className="run-note">{saved}</p>}
    </fieldset>
    <fieldset className="provider-form-fields run-step"><legend>2. Plan the run</legend>
      <fieldset className="provider-choice"><legend>Suite</legend>{options.suites.map((s) => <label key={s.id}><input type="radio" name="run-suite" checked={suite === s.id} onChange={() => select(setSuite)(s.id)} />{s.id} · {s.tasks} task{s.tasks === 1 ? '' : 's'}</label>)}</fieldset>
      <fieldset className="provider-choice"><legend>Contestant set</legend>{options.sets.map((s) => <label key={s.name}><input type="radio" name="run-set" checked={setName_ === s.name} onChange={() => select(setSetName)(s.name)} />{s.name}</label>)}</fieldset>
      {chosen && <ul className="run-members">{chosen.contestants.map((c) => <li key={c.label + c.model}>{c.model}{c.runnable ? (c.kind === 'subscription' ? ' · subscription' : '') : ` · cannot run here: ${c.reason}`}</li>)}{chosen.error && <li>{chosen.error}</li>}</ul>}
      <label>Repeats (1 to {options.limits.max_repeats})<input type="number" min={1} max={options.limits.max_repeats} value={repeats} onChange={(e) => { setRepeats(Number(e.target.value)); setPlan(null); setConfirmed(false); }} /></label>
      <label className="provider-checkbox"><input type="checkbox" checked={docker} disabled={!options.docker_available} onChange={(e) => { setDocker(e.target.checked); setPlan(null); }} />Run execution scorers in Docker{!options.docker_available && ' (Docker was not found)'}</label>
      <div className="provider-actions"><button type="button" disabled={busy || !suite || !setName_} onClick={() => void guard(async () => { setPlan(await api.plan(suite, setName_, repeats, csrf)); setConfirmed(false); })}>Preview plan</button></div>
      {plan && <div className="run-plan" role="status"><strong>{plan.tasks} task{plan.tasks === 1 ? '' : 's'} × {plan.contestants} contestant{plan.contestants === 1 ? '' : 's'} × {plan.repeats} repeat{plan.repeats === 1 ? '' : 's'} = {plan.trials} trial{plan.trials === 1 ? '' : 's'}</strong>
        {plan.scorers.length > 0 && <p>Scorers: {plan.scorers.join(', ')}{plan.scorers.includes('execution') && !docker ? ' (execution scoring is skipped without Docker)' : ''}</p>}
        {plan.blockers.map((b) => <p key={b} className="run-blocker">{b}</p>)}
        {needsConfirm && <label className="provider-checkbox"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />I understand this makes {plan.subscription_trials} real request{plan.subscription_trials === 1 ? '' : 's'} and uses my subscription quota.</label>}
      </div>}
    </fieldset>
    <fieldset className="provider-form-fields run-step"><legend>3. Run the trials</legend>
      <div className="provider-actions"><button type="button" disabled={!canStart} onClick={() => void guard(async () => { await api.start(suite, setName_, repeats, docker, csrf); setPlan(null); setConfirmed(false); await reload(); })}>Start run</button></div>
      {running && <p className="run-note">A run is going. Only one run can go at a time.</p>}
      {jobs.length === 0 && <p className="run-note">No runs have been started from this page.</p>}
      <ul className="run-jobs">{jobs.map((job) => <RunJob key={job.id} job={job} api={api} busy={busy} onCancel={() => void guard(async () => { await api.cancel(job.id, csrf); await reload(); })} onResume={() => void guard(async () => { await api.resume(job.id, csrf); await reload(); })} />)}</ul>
    </fieldset>
  </section>;
}

function RunJob({ job, api, busy, onCancel, onResume }: { job: LaunchJob; api: LaunchApi; busy: boolean; onCancel: () => void; onResume: () => void }) {
  const [log, setLog] = useState<string>();
  const [logError, setLogError] = useState('');
  return <li className={`run-job run-job-${job.state}`} aria-label={`Run ${job.suite} ${job.set}`}>
    <div className="run-job-head"><strong>{stateLabels[job.state] ?? job.state}</strong><span>{job.suite} · {job.set} · {job.trials} trial{job.trials === 1 ? '' : 's'}</span></div>
    {job.message && !active(job) && <p>{job.message}</p>}
    {job.run_id && <p className="run-note">Run id <code>{job.run_id}</code></p>}
    <div className="provider-actions">
      {active(job) && <button type="button" disabled={busy} onClick={onCancel}>Cancel run</button>}
      {!active(job) && job.state !== 'succeeded' && job.run_id && <button type="button" disabled={busy} onClick={onResume}>Resume run</button>}
      <button type="button" onClick={() => void api.job(job.id).then((full) => { setLog(full.log_tail ?? ''); setLogError(''); }).catch((cause: unknown) => setLogError(errorMessage(cause)))}>Show log</button>
      {job.run_id && !active(job) && job.state === 'succeeded' && <><a href="/leaderboard">Leaderboard</a><a href="/trace">Trace</a></>}
      {job.run_id && <a href="/live">Live</a>}
    </div>
    {logError && <p role="alert" className="providers-error">{logError}</p>}
    {log !== undefined && <pre className="run-log" aria-label="Run log">{log || 'The run has not written anything yet.'}</pre>}
  </li>;
}
