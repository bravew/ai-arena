import { useCallback, useEffect, useState } from 'react';
import type { ArenaDataSource, BlindPair, HumanLeaderboardEntry, VoteChoice, VoteReveal } from './arena-data';

const choices: ReadonlyArray<{ choice: VoteChoice; label: string; shortcut: string }> = [
  { choice: 'A', label: 'A is better', shortcut: 'A' },
  { choice: 'B', label: 'B is better', shortcut: 'B' },
  { choice: 'tie', label: 'Tie', shortcut: 'T' },
  { choice: 'both_bad', label: 'Both bad', shortcut: 'X' },
];

interface ArenaViewProps {
  dataSource: ArenaDataSource;
}

type ViewState = 'loading' | 'ready' | 'voting' | 'revealed' | 'complete' | 'error';

export function ArenaView({ dataSource }: ArenaViewProps) {
  const [pair, setPair] = useState<BlindPair | null>(null);
  const [reveal, setReveal] = useState<VoteReveal | null>(null);
  const [leaderboard, setLeaderboard] = useState<readonly HumanLeaderboardEntry[] | null>(null);
  const [state, setState] = useState<ViewState>('loading');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void dataSource.nextPair().then((next) => {
      if (!active) return;
      setPair(next);
      setState(next ? 'ready' : 'complete');
    }).catch((cause: unknown) => {
      if (!active) return;
      setPair(null);
      setState('error');
      setError(errorMessage(cause, 'Could not load the next comparison.'));
    });
    return () => { active = false; };
  }, [dataSource]);

  const castVote = useCallback(async (choice: VoteChoice) => {
    if (!pair || state !== 'ready') return;
    setState('voting');
    setError(null);
    try {
      const result = await dataSource.castVote(pair.pair_id, choice);
      setReveal(result);
      setState('revealed');
      try {
        setLeaderboard(await dataSource.leaderboard());
      } catch (cause) {
        setError(errorMessage(cause, 'Vote recorded; human leaderboard is temporarily unavailable.'));
      }
    } catch (cause) {
      setState('ready');
      setError(errorMessage(cause, 'Could not record this vote. Please try again.'));
    }
  }, [dataSource, pair, state]);

  const loadPair = useCallback(async () => {
    setState('loading');
    setError(null);
    setReveal(null);
    setLeaderboard(null);
    try {
      const next = await dataSource.nextPair();
      setPair(next);
      setState(next ? 'ready' : 'complete');
    } catch (cause) {
      setPair(null);
      setState('error');
      setError(errorMessage(cause, 'Could not load the next comparison.'));
    }
  }, [dataSource]);

  useEffect(() => {
    if (state !== 'ready') return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.repeat) return;
      const keyChoices: Record<string, VoteChoice> = { a: 'A', b: 'B', t: 'tie', x: 'both_bad' };
      const choice = keyChoices[event.key.toLowerCase()];
      if (choice) {
        event.preventDefault();
        void castVote(choice);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [castVote, state]);

  return <section className="panel arena-view" aria-labelledby="arena-title">
    <header className="arena-heading">
      <div><div className="eyebrow">HUMAN EVALUATION</div><h2 id="arena-title">Blind comparison</h2><p>Choose the stronger response before identities are revealed.</p></div>
    </header>
    {state === 'loading' && <p role="status">Loading a blind comparison…</p>}
    {state === 'error' && <div role="alert"><p>{error}</p><button type="button" onClick={() => void loadPair()}>Try again</button></div>}
    {state === 'complete' && <p role="status">You’ve completed all available comparisons.</p>}
    {pair && (state === 'ready' || state === 'voting' || state === 'revealed') && <>
      <div className="arena-context"><span>Task</span><strong>{pair.task_id}</strong><span>{state === 'revealed' ? 'Vote recorded' : 'Identities hidden'}</span></div>
      <div className="arena-pairs">
        <article className="arena-submission"><h3>Response A</h3><pre>{pair.left.content}</pre></article>
        <article className="arena-submission"><h3>Response B</h3><pre>{pair.right.content}</pre></article>
      </div>
      {error && state === 'ready' && <p role="alert">{error}</p>}
      {state === 'ready' && <div className="arena-choices" role="group" aria-label="Choose a comparison result">
        {choices.map(({ choice, label, shortcut }) => <button key={choice} type="button" onClick={() => void castVote(choice)}>{label} <kbd>{shortcut}</kbd></button>)}
      </div>}
      {state === 'voting' && <p role="status">Recording your vote…</p>}
      {state === 'revealed' && <>
        <section className="arena-reveal" aria-labelledby="arena-reveal-title"><h3 id="arena-reveal-title">Identities revealed</h3><p>Your choice: <strong>{choiceLabel(reveal?.vote.choice)}</strong></p>
          {Object.entries(reveal?.revealed ?? {}).map(([trialId, contestant]) => <p key={trialId}><span>{trialId}</span> · <strong>{contestant}</strong></p>)}
        </section>
        {error && <p role="status">{error}</p>}
        {leaderboard && <section className="arena-leaderboard" aria-labelledby="arena-leaderboard-title"><h3 id="arena-leaderboard-title">Human leaderboard</h3>{leaderboard.length ? <ol>{leaderboard.map((entry) => <li key={entry.contestant_id}><strong>{entry.contestant_id}</strong><span>{entry.rating.toFixed(2)} ({entry.low.toFixed(2)}–{entry.high.toFixed(2)})</span><small>{entry.comparisons} comparisons</small></li>)}</ol> : <p>No human ratings yet.</p>}</section>}
        <button type="button" onClick={() => void loadPair()}>Next comparison</button>
      </>}
    </>}
  </section>;
}

function choiceLabel(choice: VoteChoice | undefined): string {
  return choices.find((item) => item.choice === choice)?.label ?? 'Unknown';
}

function errorMessage(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}
