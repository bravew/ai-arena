import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import fixtureText from '../../../../fixtures/events/events.jsonl?raw';
import { validateEventStream } from '../../lib/schema';
import type { RunEvent } from '../../lib/schema';
import type { LiveBundle } from './model';
import type { Contestant } from '../../lib/schema';
import { contestantColor } from '../../theme';
import { ledgerTotals, LongPollSource, parseLiveBundle, reduceEvents, ReplaySource, type LiveSource } from './model';
import './live.css';

const fixtureResult = validateEventStream(fixtureText);
const fixtureEvents = fixtureResult.ok ? fixtureResult.value : [];
const emptyEvents: RunEvent[] = [];

export function LiveView({ bundle: input, replayEvents }: { bundle: unknown; replayEvents?: RunEvent[] }) {
  const bundle = parseLiveBundle(input);
  if (!bundle) return <section className="panel" role="alert"><h2>Live data unavailable</h2><p>The loaded bundle does not contain the version 2 live ledger fields.</p></section>;
  const initialEvents = replayEvents ??
    (window.location.protocol === 'file:' ? emptyEvents : fixtureEvents);
  return <LiveRun bundle={bundle} replayEvents={initialEvents} />;
}

function LiveRun({ bundle, replayEvents }: { bundle: LiveBundle; replayEvents: RunEvent[] }) {
  const [source, setSource] = useState<LiveSource>(() => new ReplaySource(replayEvents));
  const [events, setEvents] = useState<RunEvent[]>(replayEvents);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rankMode, setRankMode] = useState(false);
  const [sourceError, setSourceError] = useState<string>();
  const frameRef = useRef<number | undefined>(undefined);
  const frameCountRef = useRef(0);
  const [observedFrames, setObservedFrames] = useState(0);
  const stageRef = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const [documentVisible, setDocumentVisible] = useState(() => document.visibilityState !== 'hidden');
  const prefersReducedMotion = useReducedMotion();
  const totals = ledgerTotals(bundle);
  const snapshot = useMemo(() => reduceEvents(events.slice(0, cursor)), [cursor, events]);
  const contestants = bundle.contestants;
  const trials = [...snapshot.trials.entries()].map(([id, trial]) => ({ id, ...trial }));
  const currentEvents = events.slice(0, cursor);
  const boardTrials = [...trials, ...bundle.trials.filter((trial) => !snapshot.trials.has(trial.id) && currentEvents.some((event) => event.ref === trial.id && (event.kind === 'trial_queued' || event.kind === 'trial_started'))).map((trial) => ({ id: trial.id, contestantId: trial.contestant_id, taskId: trial.task_id, status: 'queued' }))];
  const completed = trials.filter((trial) => ['succeeded', 'failed', 'errored', 'timeout', 'skipped'].includes(trial.status)).length;

  useEffect(() => {
    void source.read().then(setEvents).catch((error: unknown) => setSourceError(error instanceof Error ? error.message : String(error)));
    const unsubscribe = source.subscribe?.((incoming) => setEvents((previous) => mergeEvents(previous, incoming)), (error) => setSourceError(error.message));
    return () => unsubscribe?.();
  }, [source]);

  useEffect(() => {
    if (!playing || prefersReducedMotion || cursor >= events.length) return;
    const timer = window.setTimeout(() => setCursor((value) => Math.min(events.length, value + 1)), 350);
    return () => window.clearTimeout(timer);
  }, [cursor, events.length, playing, prefersReducedMotion]);

  useEffect(() => {
    const visibilityChanged = () => setDocumentVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => document.removeEventListener('visibilitychange', visibilityChanged);
  }, []);

  useEffect(() => {
    const node = stageRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry?.isIntersecting ?? false));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (prefersReducedMotion || !visible || !documentVisible || !playing) {
      if (frameRef.current !== undefined) cancelAnimationFrame(frameRef.current);
      frameRef.current = undefined;
      return;
    }
    let active = true;
    const animate = () => {
      if (!active) return;
      frameCountRef.current += 1;
      setObservedFrames(frameCountRef.current);
      frameRef.current = requestAnimationFrame(animate);
    };
    frameRef.current = requestAnimationFrame(animate);
    const visibilityChanged = () => {
      if (document.visibilityState === 'hidden' && frameRef.current !== undefined) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = undefined;
      }
    };
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => {
      active = false;
      document.removeEventListener('visibilitychange', visibilityChanged);
      if (frameRef.current !== undefined) cancelAnimationFrame(frameRef.current);
      frameRef.current = undefined;
    };
  }, [documentVisible, playing, prefersReducedMotion, visible]);

  const play = () => {
    if (prefersReducedMotion) { setCursor(events.length); setPlaying(false); return; }
    if (cursor >= events.length) setCursor(0);
    setPlaying(true);
  };
  const useReplay = () => {
    const selectedEvents = replayEvents.length === 0 && window.location.protocol !== 'file:'
      ? fixtureEvents
      : replayEvents;
    setSource(new ReplaySource(selectedEvents));
    setEvents(selectedEvents);
    setCursor(0);
    setPlaying(false);
    setSourceError(undefined);
  };
  const useLive = () => {
    const live = new URLSearchParams(window.location.search).get('source');
    if (!live) { setSourceError('Add ?source=<arena-origin> to connect to a running arena.'); return; }
    setCursor(0); setEvents([]); setPlaying(false); setSourceError(undefined); setSource(new LongPollSource(live, bundle.run.id));
  };

  return <div className="live-view">
    <section className="live-heading"><div><div className="eyebrow">RUN · {bundle.run.id}</div><h2 id="live-title">Live run</h2><p>Event replay and live run activity.</p></div><div className="live-controls"><button type="button" aria-pressed={source instanceof ReplaySource} onClick={useReplay}>Replay</button><button type="button" aria-pressed={!(source instanceof ReplaySource)} onClick={useLive}>Connect live</button></div></section>
    <section className="live-summary" aria-label="Run counters"><div><strong>{completed} / {totals.trials}</strong><span>trials completed</span></div><div><strong>{snapshot.calls.size} / {totals.calls}</strong><span>model calls</span></div><div><strong>{snapshot.trials.size}</strong><span>trials observed</span></div><div><strong>{currentEvents.length} / {events.length}</strong><span>events replayed</span></div></section>
    <p className="live-caption" aria-live="polite" aria-atomic="true">{snapshot.latestCaption}</p>
    {sourceError && <p className="live-error" role="alert">{sourceError}</p>}
    <section className="panel live-stage" ref={stageRef} role="region" aria-label="Animated stage" data-frame-count={prefersReducedMotion || !visible || !documentVisible || !playing ? 0 : observedFrames}>
      <header className="live-panel-heading"><div><div className="eyebrow">CALL ROUTING</div><h3>Run stage</h3></div><span className="live-state">{snapshot.complete ? 'Run complete' : source instanceof ReplaySource ? 'Replay' : 'Live source'}</span></header>
      <div className="stage-map">
        <div className="stage-entities"><strong>Contestants</strong>{contestants.map((c) => <div className="stage-entity" key={c.id}><i style={{ '--series-color': contestantColor(c.id) } as CSSProperties} aria-hidden="true" />{c.label ?? c.id}<small>{trials.filter((t) => t.contestantId === c.id && ['succeeded', 'failed', 'errored', 'timeout', 'skipped'].includes(t.status)).length} complete</small></div>)}</div>
        <svg className="stage-wires" viewBox="0 0 400 180" preserveAspectRatio="none" aria-hidden="true"><path d="M0 32 C140 32 130 90 200 90 S260 32 400 32"/><path d="M0 90 C140 90 130 90 200 90 S260 90 400 90"/><path d="M0 148 C140 148 130 90 200 90 S260 148 400 148"/></svg>
        <div className="stage-hub"><span className="hub-mark" aria-hidden="true">A</span><strong>Gateway</strong><small>Run event stream</small></div>
        <div className="stage-lanes"><strong>Provider lanes</strong>{Array.from(new Set(bundle.calls.map((call) => `${call.provider} / ${call.account_id}`))).map((lane) => <div className="stage-entity" key={lane}><i aria-hidden="true" />{lane}<small>from ledger</small></div>)}{bundle.calls.length === 0 && <p>Lane state appears as events arrive.</p>}</div>
        <div className="stage-flight" aria-hidden="true">{snapshot.calls.size ? '•' : ''}</div>
      </div>
      <p className="stage-footnote">{prefersReducedMotion ? 'Reduced motion: calls update immediately.' : 'Flights follow recorded call events; stage facts are listed in the tables below.'}</p>
    </section>
    <section className="live-panels">
      <section className="panel" role="region" aria-label="Race chart"><header className="live-panel-heading"><div><div className="eyebrow">PROGRESS</div><h3>Score vs. completed trials</h3></div><div className="live-controls"><button type="button" aria-pressed={!rankMode} onClick={() => setRankMode(false)}>Score</button><button type="button" aria-pressed={rankMode} onClick={() => setRankMode(true)}>Rank</button></div></header><RaceChart bundle={bundle} events={currentEvents} rankMode={rankMode} /><div className="table-wrap"><table aria-label="Race chart facts"><thead><tr><th>Contestant</th><th>Completed trials</th><th>Mean score</th><th>Rank</th></tr></thead><tbody>{raceRows(bundle, currentEvents).map((row) => <tr key={row.id}><td><span className="live-legend"><i style={{ '--series-color': contestantColor(row.id) } as CSSProperties} />{row.label}</span></td><td>{row.completed}</td><td>{row.score === null ? '—' : row.score.toFixed(3)}</td><td>{row.rank ?? '—'}</td></tr>)}</tbody></table></div></section>
      <section className="panel" role="region" aria-label="Trial board"><header className="live-panel-heading"><div><div className="eyebrow">TASK × CONTESTANT</div><h3>Trial board</h3></div><span className="live-state">{completed} completed</span></header><TrialBoard trials={boardTrials} contestants={contestants} /><div className="table-wrap"><table aria-label="Trial board facts"><thead><tr><th>Task</th><th>Contestant</th><th>Attempt</th><th>Status</th></tr></thead><tbody>{bundle.trials.map((trial) => { const live = snapshot.trials.get(trial.id); return <tr key={trial.id}><td>{trial.task_id}</td><td>{contestants.find((c) => c.id === trial.contestant_id)?.label ?? trial.contestant_id}</td><td>#{trial.attempt}</td><td>{live?.status ?? 'queued'}</td></tr>; })}</tbody></table></div></section>
    </section>
    <section className="panel live-replay"><header className="live-panel-heading"><div><div className="eyebrow">EVENT LOG</div><h3>Replay controls</h3></div><div className="live-controls"><button type="button" onClick={play} aria-label={playing ? 'Pause replay' : 'Play replay'}>{playing ? 'Pause replay' : 'Play replay'}</button><button type="button" onClick={() => { setPlaying(false); setCursor(0); }} aria-label="Reset replay">Reset replay</button></div></header><label htmlFor="replay-position"><span>{cursor} / {events.length} events</span></label><input id="replay-position" type="range" min="0" max={events.length} value={Math.min(cursor, events.length)} onChange={(event) => { setPlaying(false); setCursor(Number(event.currentTarget.value)); }} /><ol className="event-list">{snapshot.events.slice(-5).reverse().map((event) => <li key={event.seq}><span>#{event.seq}</span><strong>{event.kind.replaceAll('_', ' ')}</strong><time>{event.ts}</time></li>)}</ol></section>
  </div>;
}

function RaceChart({ bundle, events, rankMode }: { bundle: LiveBundle; events: RunEvent[]; rankMode: boolean }) {
  const rows = raceRows(bundle, events);
  const scores = rows.map((row) => row.score).filter((value): value is number => value !== null);
  const maxCompleted = Math.max(1, ...rows.map((row) => row.completed));
  const minScore = Math.min(0, ...scores);
  const maxScore = Math.max(1, ...scores);
  const points = rows.flatMap((row, i) => {
    const y = rankMode ? 18 + i * (112 / Math.max(1, rows.length - 1)) : 130 - ((row.score ?? 0) - minScore) / Math.max(0.001, maxScore - minScore) * 110;
    return [{ ...row, x: 45 + row.completed / maxCompleted * 430, y }];
  });
  return <div className="race-chart"><svg viewBox="0 0 500 170" role="img" aria-label={rankMode ? 'Contestant rank by completed trials' : 'Mean score by completed trials'}><path className="chart-axis" d="M42 12 V132 H480"/><path className="chart-gridline" d="M42 72 H480"/><text x="44" y="155">0</text><text x="450" y="155">{maxCompleted} completed</text><text x="5" y="18">{rankMode ? '1' : maxScore.toFixed(1)}</text><text x="5" y="132">{rankMode ? String(rows.length) : minScore.toFixed(1)}</text>{points.map((point) => <g key={point.id}><circle cx={point.x} cy={point.y} r="5" fill={contestantColor(point.id)}><title>{point.label}: {point.score?.toFixed(3) ?? 'no score'}, {point.completed} completed trials</title></circle><text x={point.x + 7} y={point.y - 7}>{point.label.slice(0, 14)}</text></g>)}</svg><div className="race-legend" aria-label="Contestants">{rows.map((row) => <span className="live-legend" key={row.id}><i style={{ '--series-color': contestantColor(row.id) } as CSSProperties} />{row.label}</span>)}</div></div>;
}

function TrialBoard({ trials, contestants }: { trials: { id: string; contestantId: string; taskId: string; status: string }[]; contestants: Contestant[] }) {
  const tasks = [...new Set(trials.map((trial) => trial.taskId).filter(Boolean))];
  return <div className="trial-grid" role="img" aria-label="Trial status board">{tasks.map((task) => <div className="trial-board-row" key={task}><strong>{task || 'Task'}</strong>{contestants.map((contestant) => { const item = trials.find((trial) => trial.taskId === task && trial.contestantId === contestant.id); return <span className={`trial-cell trial-${item?.status ?? 'queued'}`} key={contestant.id} title={`${contestant.label ?? contestant.id}: ${item?.status ?? 'no trial'}`} aria-label={`${contestant.label ?? contestant.id}: ${item?.status ?? 'no trial'}`}>{statusMark(item?.status)}</span>; })}</div>)}</div>;
}

function statusMark(status: string | undefined): string { return status === 'succeeded' ? '✓' : status === 'running' ? '●' : status === 'failed' || status === 'errored' ? '!' : status ? '◌' : '—'; }

function raceRows(bundle: LiveBundle, events: RunEvent[]) {
  const snapshot = reduceEvents(events);
  const trialStates = snapshot.trials;
  const scoresByTrial = new Map<string, number[]>();
  for (const event of events) if (event.kind === 'score_added' && event.ref) {
    const value = event.data?.['normalized'];
    if (typeof value === 'number') scoresByTrial.set(event.ref, [...(scoresByTrial.get(event.ref) ?? []), value]);
  }
  const rows = bundle.contestants.map((contestant) => {
    const contestantTrials = bundle.trials.filter((trial) => trial.contestant_id === contestant.id);
    const completed = contestantTrials.filter((trial) => ['succeeded', 'failed', 'errored', 'timeout', 'skipped'].includes(trialStates.get(trial.id)?.status ?? 'queued')).length;
    const values = contestantTrials.flatMap((trial) => scoresByTrial.get(trial.id) ?? []);
    return { id: contestant.id, label: contestant.label ?? contestant.id, completed, score: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null, rank: null as number | null };
  });
  [...rows].sort((a, b) => (b.score ?? -Infinity) - (a.score ?? -Infinity)).forEach((row, index) => { row.rank = row.score === null ? null : index + 1; });
  return rows;
}

function mergeEvents(previous: RunEvent[], incoming: RunEvent[]): RunEvent[] {
  const bySequence = new Map(previous.map((event) => [event.seq, event]));
  for (const event of incoming) bySequence.set(event.seq, event);
  return [...bySequence.values()].sort((a, b) => a.seq - b.seq);
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const change = () => setReduced(query.matches);
    query.addEventListener('change', change);
    return () => query.removeEventListener('change', change);
  }, []);
  return reduced;
}
