import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import type { Bundle, Trial } from '../../lib/schema';
import { contestantColor } from '../../theme';
import './compare.css';

export function CompareView({ bundle }: { bundle: Bundle }) {
  const [params] = useSearchParams();
  const [hideNames, setHideNames] = useState(false);
  const [diff, setDiff] = useState(false);
  const taskId = params.get('task');
  const trials = useMemo(() => {
    const requested = params.get('trials')?.split(',').filter(Boolean) ?? [];
    const matching = bundle.trials.filter((trial) => (!taskId || trial.task_id === taskId) && (!requested.length || requested.includes(trial.id)));
    return matching.slice(0, 4);
  }, [bundle, params, taskId]);
  const attempts = [...new Set(bundle.trials.filter((t) => t.task_id === (taskId ?? trials[0]?.task_id)).map((t) => t.attempt))].sort((a, b) => a - b);
  const [attempt, setAttempt] = useState<number | null>(null);
  const shown = trials.filter((trial) => attempt === null || trial.attempt === attempt);

  return <section className="panel compare-view" aria-labelledby="compare-title">
    <header className="compare-heading"><div><div className="eyebrow">SIDE BY SIDE · {taskId ?? trials[0]?.task_id ?? 'NO TRIALS'}</div><h2 id="compare-title">Compare trials</h2><p>Inspect outputs, scores and changes across contestants.</p></div><div className="compare-actions"><button type="button" aria-pressed={hideNames} onClick={() => setHideNames((v) => !v)}>{hideNames ? 'Show names' : 'Hide names'}</button><button type="button" aria-pressed={diff} onClick={() => setDiff((v) => !v)}>{diff ? 'Show artifacts' : 'Diff A ↔ B'}</button><div className="compare-repeats" role="group" aria-label="Repeat"><span>Repeat</span><button type="button" aria-pressed={attempt === null} onClick={() => setAttempt(null)}>All</button>{attempts.map((n) => <button key={n} type="button" aria-pressed={attempt === n} onClick={() => setAttempt(n)}>{n}</button>)}</div></div></header>
    {shown.length === 0 ? <p className="summary-empty">No trials are available in this bundle.</p> : <div className="compare-columns">{shown.map((trial, index) => <TrialColumn key={trial.id} bundle={bundle} trial={trial} index={index} hideNames={hideNames} diff={diff} />)}</div>}
  </section>;
}

function TrialColumn({ bundle, trial, index, hideNames, diff }: { bundle: Bundle; trial: Trial; index: number; hideNames: boolean; diff: boolean }) {
  const contestant = bundle.contestants.find((item) => item.id === trial.contestant_id);
  const artifact = bundle.artifacts.find((item) => 'trial_id' in item && item.trial_id === trial.id);
  const scores = bundle.scores.filter((score) => score.trial_id === trial.id);
  const html = artifact?.render_hint === 'html-sandbox';
  const trialSessions = bundle.sessions.filter((session) => session.trial_id === trial.id);
  const changedFiles = trialSessions.flatMap((session) => session.turns.flatMap((turn) => turn.files));
  const testResults = trialSessions.flatMap((session) => session.turns.flatMap((turn) => turn.tool_calls.filter((tool) => /test|pytest|vitest|jest|cargo test/i.test(tool.name)).map((tool) => ({ name: tool.name, status: tool.exit_status }))));
  const identity = hideNames ? `Trial ${String.fromCharCode(65 + index)}` : contestant?.label ?? contestant?.id ?? trial.contestant_id;
  const columnHeading = hideNames ? identity : `${contestant?.model ?? 'Unknown model'} · ${contestant?.scaffold?.id ?? 'direct'} · ${contestant?.kit_hash ?? 'no kit'}`;
  return <article className="compare-column"><h3 className="compare-identity" style={{ '--contestant-color': contestantColor(trial.contestant_id) } as React.CSSProperties}><i aria-hidden="true" />{columnHeading}</h3><div className="compare-meta">{hideNames ? 'Identity hidden' : identity}</div><div className="compare-tags"><span>{trial.status}</span><span>Attempt {trial.attempt}</span></div>
    <section className="compare-artifact" aria-label={`Artifact for ${identity}`}>{diff ? <div className="compare-repo-diff"><h4>Repository diff · file tree</h4>{changedFiles.length ? <ul>{changedFiles.map((file, fileIndex) => <li key={`${file.path}-${fileIndex}`}><details><summary>{file.path} <span>+{file.added} −{file.removed}</span></summary><pre>Diff content is not present in this bundle.</pre></details></li>)}</ul> : artifact?.render_hint === 'diff' ? <pre>{artifact.path}</pre> : <p>No per-file repository diff is recorded.</p>}<h4>Test results</h4>{testResults.length ? <ul>{testResults.map((result, resultIndex) => <li key={`${result.name}-${resultIndex}`}>{result.name}: {result.status === 0 ? 'passed' : result.status === null ? 'unknown' : `failed (${result.status})`}</li>)}</ul> : <p>No test results recorded.</p>}</div> : html ? <iframe title={`Sandboxed HTML artifact for ${identity}`} sandbox="" srcDoc="" /> : artifact ? <pre>{artifact.path}</pre> : <p>No artifact recorded for this trial.</p>}</section>
    {scores.length > 0 && <div className="compare-scores">{scores.map((score) => <div key={`${score.scorer_id}-${score.scorer_version}`}><span>{score.scorer_id}</span><strong>{score.normalized.toFixed(2)}</strong><span>{score.passed === null ? 'Not evaluated' : score.passed ? 'Passed' : 'Failed'}</span></div>)}</div>}
    <div className="compare-metrics">{bundle.calls.filter((call) => call.trial_id === trial.id).reduce((sum, call) => sum + call.tokens.in + call.tokens.out + call.tokens.reasoning + call.tokens.cache_read + call.tokens.cache_write, 0)} tokens · {bundle.calls.filter((call) => call.trial_id === trial.id).reduce((sum, call) => sum + call.total_ms, 0)} ms</div></article>;
}
