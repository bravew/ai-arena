// Types for the two published schemas, written by hand to match docs/bundle-schema.json and
// docs/event-schema.json. The values are checked against the schemas at the boundary (validate.ts);
// nothing outside this folder casts to these types.

export type RunStatus = 'running' | 'succeeded' | 'failed' | 'errored' | 'cancelled';
export type TrialStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'errored'
  | 'timeout'
  | 'skipped';

export interface Run {
  id: string;
  suite_id: string;
  started_at: string;
  ended_at?: string | null;
  status: RunStatus;
}

export interface Contestant {
  id: string;
  label?: string | null;
  model: string;
  params: Record<string, unknown>;
  scaffold: { id: string; version: string; settings: Record<string, unknown> } | null;
  scaffold_prompt: 'native' | 'pinned';
  kit_hash: string;
  orchestration: string;
  prompt_version?: string | null;
  hooks: string[];
}

export interface Trial {
  id: string;
  run_id: string;
  contestant_id: string;
  task_id: string;
  attempt: number;
  status: TrialStatus;
  error_class?: string | null;
  flags: { unmetered: boolean; swapped: boolean; subscription_served: boolean; kit_unapplied: boolean };
  started_at?: string | null;
  ended_at?: string | null;
}

export interface Tokens {
  in: number;
  out: number;
  reasoning: number;
  cache_read: number;
  cache_write: number;
}

export type PromptPartKind = 'system' | 'tools' | 'instructions' | 'files' | 'conversation';

export interface Call {
  id: string;
  seq: number;
  run_id: string;
  trial_id?: string | null;
  span_id?: string | null;
  parent_span_id?: string | null;
  purpose: 'contestant' | 'judge' | 'orchestration' | 'arena';
  protocol_in: string;
  protocol_out: string;
  translated: boolean;
  provider: string;
  account_id: string;
  model_asked: string;
  model_served?: string | null;
  swapped: boolean;
  upstream?: string | null;
  effort_asked?: string | null;
  effort_applied?: string | null;
  tries: { account_id: string; status?: number | null; error_class?: string | null; rest_ms: number; ms: number }[];
  status: number | null;
  error_class?: string | null;
  tokens: Tokens;
  cost_usd: number | null;
  price_version?: string | null;
  queue_ms: number;
  ttft_ms?: number | null;
  first_text_ms?: number | null;
  total_ms: number;
  cache: 'hit' | 'miss' | 'off';
  prompt_parts: { kind: PromptPartKind; tokens: number }[];
}

export interface Score {
  trial_id: string;
  scorer_id: string;
  scorer_version: string;
  value: number;
  normalized: number;
  passed: boolean | null;
  rationale: string;
  evidence: Record<string, unknown>;
}

export interface SkillEvent {
  kind: 'listed' | 'loaded' | 'invoked';
  skill: string;
  kit_hash: string;
}

export interface Turn {
  call_ids: string[];
  tool_calls: {
    name: string;
    args_digest: string;
    result_size: number;
    duration_ms: number;
    exit_status: number | null;
  }[];
  skill_events: SkillEvent[];
  mcp_calls: { server: string; tool: string; duration_ms: number; status: string }[];
  files: { path: string; added: number; removed: number }[];
  unmetered: boolean;
}

export interface Session {
  id: string;
  trial_id: string;
  parent_session_id?: string | null;
  agent: string;
  native_session_id?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  status: string;
  turns: Turn[];
}

export interface KitInstall {
  trial_id: string;
  kit_hash: string;
  written: { path: string; sha256: string }[];
  refused: { item: string; reason: string }[];
}

export interface Provenance {
  origin: 'native' | 'imported';
  verification: 'verified' | 'unverified';
  importer?: string | null;
  importer_version?: string | null;
  source_ref?: string | null;
}

export interface Artifact {
  trial_id?: string;
  sha256: string;
  path: string;
  mime: string;
  render_hint: 'code' | 'diff' | 'markdown' | 'html-sandbox' | 'svg' | 'image' | 'json';
}

export interface Bundle {
  bundle_version: 1 | 2;
  provenance?: Provenance;
  run: Run;
  contestants: Contestant[];
  trials: Trial[];
  calls: Call[];
  scores: Score[];
  sessions: Session[];
  kit_installs: KitInstall[];
  artifacts: Artifact[];
}

export type RunEventKind =
  | 'run_started'
  | 'trial_queued'
  | 'trial_started'
  | 'kit_installed'
  | 'call_queued'
  | 'call_try'
  | 'call_finished'
  | 'session_turn'
  | 'skill_event'
  | 'lane_changed'
  | 'trial_finished'
  | 'score_added'
  | 'budget'
  | 'run_finished';

export interface RunEvent {
  event_version: 1;
  seq: number;
  ts: string;
  run_id: string;
  kind: RunEventKind;
  ref?: string | null;
  data?: Record<string, unknown>;
}
