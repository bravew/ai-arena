import { useMemo, useState } from 'react';
import './judges.css';

type Metric = number | null;
type Judge = {
  judge_id: string;
  judgments: number;
  human_labeled: number;
  agreement: Metric;
  cohens_kappa: Metric;
  position_bias_rate: Metric;
  length_correlation: Metric;
  self_preference_rate: Metric;
  cost_per_judgment_usd: Metric;
  missing_verdicts: number;
  missing_human_labels: number;
  missing_order_swaps: number;
  missing_lengths: number;
  missing_costs: number;
  status: 'calibrated' | 'UNCALIBRATED';
};
type Disagreement = {
  judge_id: string;
  item_id: string;
  judge_verdict: string;
  human_verdict: string;
  severity: number;
};
type JudgesReport = { threshold: number; judges: Judge[]; disagreements: Disagreement[] };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`${label} must be a finite number.`);
  return value;
}

function nullableMetric(value: unknown, label: string, minimum: number, maximum: number): Metric {
  if (value === null) return null;
  const metric = finiteNumber(value, label);
  if (metric < minimum || metric > maximum) throw new Error(`${label} must be between ${minimum} and ${maximum}, or null.`);
  return metric;
}

function count(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    throw new Error(`${label} must be a non-negative integer.`);
  }
  return value;
}

function parseReport(value: unknown): JudgesReport {
  if (!isRecord(value) || !Array.isArray(value.judges) || !Array.isArray(value.disagreements)) {
    throw new Error('Expected threshold, judges, and disagreements fields from `arena judges report --json`.');
  }
  const threshold = finiteNumber(value.threshold, 'threshold');
  if (threshold < 0 || threshold > 1) throw new Error('threshold must be between 0 and 1.');

  const judgeIds = new Set<string>();
  const judges = value.judges.map((rawJudge, index): Judge => {
    if (!isRecord(rawJudge) || typeof rawJudge.judge_id !== 'string' || rawJudge.judge_id.length === 0) {
      throw new Error(`Judge ${index + 1} must have a non-empty judge_id.`);
    }
    const { judge_id: judgeId } = rawJudge;
    if (judgeIds.has(judgeId)) throw new Error(`Duplicate judge_id ${JSON.stringify(judgeId)}.`);
    judgeIds.add(judgeId);

    const judgments = count(rawJudge.judgments, `Judge ${judgeId} judgments`);
    if (judgments === 0) throw new Error(`Judge ${judgeId} judgments must be greater than zero.`);
    const human_labeled = count(rawJudge.human_labeled, `Judge ${judgeId} human_labeled`);
    if (human_labeled > judgments) throw new Error(`Judge ${judgeId} human_labeled cannot exceed judgments.`);
    const countFields = {
      missing_verdicts: count(rawJudge.missing_verdicts, `Judge ${judgeId} missing_verdicts`),
      missing_human_labels: count(rawJudge.missing_human_labels, `Judge ${judgeId} missing_human_labels`),
      missing_order_swaps: count(rawJudge.missing_order_swaps, `Judge ${judgeId} missing_order_swaps`),
      missing_lengths: count(rawJudge.missing_lengths, `Judge ${judgeId} missing_lengths`),
      missing_costs: count(rawJudge.missing_costs, `Judge ${judgeId} missing_costs`),
    };
    for (const [field, amount] of Object.entries(countFields)) {
      if (amount > judgments) throw new Error(`Judge ${judgeId} ${field} cannot exceed judgments.`);
    }
    const agreement = nullableMetric(rawJudge.agreement, `Judge ${judgeId} agreement`, 0, 1);
    const cohens_kappa = nullableMetric(rawJudge.cohens_kappa, `Judge ${judgeId} cohens_kappa`, -1, 1);
    const position_bias_rate = nullableMetric(rawJudge.position_bias_rate, `Judge ${judgeId} position_bias_rate`, 0, 1);
    const length_correlation = nullableMetric(rawJudge.length_correlation, `Judge ${judgeId} length_correlation`, -1, 1);
    const self_preference_rate = nullableMetric(rawJudge.self_preference_rate, `Judge ${judgeId} self_preference_rate`, 0, 1);
    const cost_per_judgment_usd = nullableMetric(rawJudge.cost_per_judgment_usd, `Judge ${judgeId} cost_per_judgment_usd`, 0, Number.MAX_VALUE);
    const expectedStatus = cohens_kappa !== null && cohens_kappa >= threshold ? 'calibrated' : 'UNCALIBRATED';
    if (rawJudge.status !== expectedStatus) throw new Error(`Judge ${judgeId} status is inconsistent with its Cohen’s κ and threshold.`);

    return {
      judge_id: judgeId,
      judgments,
      human_labeled,
      agreement,
      cohens_kappa,
      position_bias_rate,
      length_correlation,
      self_preference_rate,
      cost_per_judgment_usd,
      ...countFields,
      status: expectedStatus,
    };
  });

  const disagreements = value.disagreements.map((rawRow, index): Disagreement => {
    if (!isRecord(rawRow) || typeof rawRow.judge_id !== 'string' || typeof rawRow.item_id !== 'string' || rawRow.item_id.length === 0) {
      throw new Error(`Disagreement ${index + 1} must have a valid judge_id and item_id.`);
    }
    const { judge_id, item_id } = rawRow;
    if (!judgeIds.has(judge_id)) throw new Error(`Disagreement ${index + 1} references unknown judge ${JSON.stringify(judge_id)}.`);
    const verdicts = ['a', 'b', 'tie'];
    if (typeof rawRow.judge_verdict !== 'string' || !verdicts.includes(rawRow.judge_verdict) ||
      typeof rawRow.human_verdict !== 'string' || !verdicts.includes(rawRow.human_verdict)) {
      throw new Error(`Disagreement ${index + 1} verdicts must be 'a', 'b', or 'tie'.`);
    }
    const judge_verdict = rawRow.judge_verdict;
    const human_verdict = rawRow.human_verdict;
    if (judge_verdict === human_verdict) throw new Error(`Disagreement ${index + 1} must contain different verdicts.`);
    const severity = count(rawRow.severity, `Disagreement ${index + 1} severity`);
    const expectedSeverity = new Set([judge_verdict, human_verdict]).size === 2 && judge_verdict !== 'tie' && human_verdict !== 'tie' ? 2 : 1;
    if (severity !== expectedSeverity) throw new Error(`Disagreement ${index + 1} severity is inconsistent with its verdicts.`);
    return { judge_id, item_id, judge_verdict, human_verdict, severity };
  });

  return { threshold, judges, disagreements };
}

const percent = (value: Metric) => value === null ? 'n/a' : `${(value * 100).toFixed(1)}%`;
const decimal = (value: Metric) => value === null ? 'n/a' : value.toFixed(3);

export function JudgesView() {
  const [report, setReport] = useState<JudgesReport | null>(null);
  const [error, setError] = useState('');
  const [judgeFilter, setJudgeFilter] = useState('all');
  const [filterOpen, setFilterOpen] = useState(false);
  const judges = useMemo(() => report?.judges ?? [], [report]);
  const selectedJudges = useMemo(() => judges.filter((judge) => judgeFilter === 'all' || judge.judge_id === judgeFilter), [judges, judgeFilter]);
  const disagreements = useMemo(() => (report?.disagreements ?? []).filter((row) => judgeFilter === 'all' || row.judge_id === judgeFilter), [report, judgeFilter]);

  async function loadFile(file: File | undefined) {
    if (!file) return;
    try {
      const parsed: unknown = JSON.parse(await file.text());
      setReport(parseReport(parsed));
      setJudgeFilter('all');
      setError('');
    } catch (cause) {
      setReport(null);
      setError(cause instanceof SyntaxError ? `Could not parse JSON: ${cause.message}` : cause instanceof Error ? cause.message : 'Could not read this report.');
    }
  }

  return <div className="judges-view">
    <section className="panel judges-panel" aria-labelledby="judges-title">
      <div className="judges-heading"><div><div className="eyebrow">CALIBRATION · PAIRWISE JUDGES</div><h2 id="judges-title">Judges</h2><p>Compare judge decisions with human labels and inspect the largest disagreements.</p></div>{report && <span className="judges-count">{judges.length} judges · κ target {report.threshold.toFixed(2)}</span>}</div>
      <label className="judges-upload">Upload judge report JSON<input aria-label="Upload judge report JSON" type="file" accept="application/json,.json" onChange={(event) => { void loadFile(event.currentTarget.files?.[0]); event.currentTarget.value = ''; }} /><span>Choose output from <code>arena judges report --json</code></span></label>
      {error && <div className="judges-error" role="alert">{error}</div>}
      {!report && !error && <div className="judges-empty">Upload a JSON report to view calibration and disagreements.</div>}
    </section>
    {report && <>
      {judges.some((judge) => judge.status === 'UNCALIBRATED') && <div className="judges-warning" role="status"><strong>Calibration warning</strong><span>At least one judge is below the κ target or lacks enough labeled evidence. Review its disagreements before relying on its pairwise results.</span></div>}
      <section className="panel judges-panel" aria-labelledby="calibration-title">
        <div className="judges-heading"><div><div className="eyebrow">HUMAN AGREEMENT</div><h2 id="calibration-title">Calibration</h2></div><div className="judge-filter"><span>Judge</span><button type="button" className="judge-filter-trigger" aria-label="Filter by judge" aria-expanded={filterOpen} onClick={() => setFilterOpen((open) => !open)}>{judgeFilter === 'all' ? 'All judges' : judgeFilter}<span aria-hidden="true">⌄</span></button>{filterOpen && <div className="judge-filter-options" role="group" aria-label="Judge options">{[['all', 'All judges'], ...judges.map((judge) => [judge.judge_id, judge.judge_id])].map(([value, label]) => <button key={value} type="button" aria-pressed={judgeFilter === value} onClick={() => { setJudgeFilter(value!); setFilterOpen(false); }}>{label}</button>)}</div>}</div></div>
        <div className="judges-table-wrap"><table className="judges-table" aria-label="Judge calibration"><thead><tr><th>Judge</th><th>Status</th><th>κ</th><th>Agreement</th><th>Position bias</th><th>Length corr.</th><th>Self preference</th><th>Labeled</th></tr></thead><tbody>{selectedJudges.map((judge) => <tr key={judge.judge_id}><th scope="row">{judge.judge_id}</th><td><span className={`judge-badge ${judge.status === 'calibrated' ? 'is-calibrated' : 'is-uncalibrated'}`}>{judge.status}</span></td><td>{decimal(judge.cohens_kappa)}</td><td>{percent(judge.agreement)}</td><td>{percent(judge.position_bias_rate)}</td><td>{decimal(judge.length_correlation)}</td><td>{percent(judge.self_preference_rate)}</td><td>{judge.human_labeled} / {judge.judgments}</td></tr>)}</tbody></table></div>
        <p className="judges-note">Missing evidence remains n/a. The calibrated badge requires Cohen’s κ ≥ {report.threshold.toFixed(2)}.</p>
      </section>
      <section className="panel judges-panel" aria-labelledby="disagreements-title"><div className="judges-heading"><div><div className="eyebrow">RANKED REVIEW QUEUE</div><h2 id="disagreements-title">Largest disagreements</h2><p>Opposite verdicts rank above a judge/human tie.</p></div><span className="judges-count">{disagreements.length} items</span></div>
        {disagreements.length ? <ol className="disagreement-list">{disagreements.map((row) => <li key={`${row.judge_id}:${row.item_id}`}><div><strong>{row.item_id}</strong><span>{row.judge_id}</span></div><div className="verdict-pair"><span>Judge <b>{row.judge_verdict}</b></span><span>Human <b>{row.human_verdict}</b></span></div><span className={`severity severity-${row.severity}`}>{row.severity === 2 ? 'Opposite' : 'Tie conflict'}</span></li>)}</ol> : <p className="judges-empty">No disagreements for this judge.</p>}
      </section>
    </>}
  </div>;
}
