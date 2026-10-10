import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { validateBundle, validateEvent, type SchemaIssue, type Validated } from '../../src/lib/schema';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const fixture = (rel: string) => readFileSync(path.join(repoRoot, 'fixtures', rel), 'utf8');

function bundle(): Record<string, unknown> {
  return JSON.parse(fixture('bundles/schema-example.json')) as Record<string, unknown>;
}

function issuesOf<T>(result: Validated<T>): SchemaIssue[] {
  if (result.ok) throw new Error('expected the input to be rejected');
  return result.issues;
}

describe('validateBundle', () => {
  it('accepts the fixture bundle', () => {
    const result = validateBundle(bundle());
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.value.run.id).toBe('run-schema-001');
  });

  it('names a missing required section', () => {
    const b = bundle();
    delete b.trials;
    const issues = issuesOf(validateBundle(b));
    expect(issues).toContainEqual({ path: '/trials', message: 'is missing required property "trials"' });
  });

  it('points at the exact value of a bad enum and lists the allowed ones', () => {
    const b = bundle();
    (b.trials as { status: string }[])[0]!.status = 'done';
    const issues = issuesOf(validateBundle(b));
    const issue = issues.find((i) => i.path === '/trials/0/status');
    expect(issue?.message).toBe(
      'must be one of: queued, running, succeeded, failed, errored, timeout, skipped',
    );
  });

  it('rejects an unknown property instead of ignoring it', () => {
    const b = { ...bundle(), surprise: true };
    expect(issuesOf(validateBundle(b))).toContainEqual({
      path: '/surprise',
      message: 'has unexpected property "surprise"',
    });
  });

  it('accepts the v2 fixture with required provenance and artifact trial links', () => {
    const result = validateBundle(bundle());
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.bundle_version).toBe(2);
      expect(result.value.provenance?.origin).toBe('native');
      expect(result.value.artifacts[0]?.trial_id).toBe('trial-base');
    }
  });

  it('rejects a v2 bundle missing required provenance', () => {
    const b = bundle();
    delete b.provenance;
    expect(issuesOf(validateBundle(b))).toContainEqual({
      path: '/provenance',
      message: 'is missing required property "provenance"',
    });
  });

  it('rejects a v2 artifact without its required trial link', () => {
    const b = bundle();
    delete (b.artifacts as Record<string, unknown>[])[0]!.trial_id;
    expect(issuesOf(validateBundle(b)).map((issue) => issue.path)).toContain('/artifacts/0/trial_id');
  });

  it('rejects an unsupported bundle version', () => {
    const issues = issuesOf(validateBundle({ ...bundle(), bundle_version: 3 }));
    expect(issues[0]).toEqual({
      path: '/bundle_version',
      message: 'unsupported bundle version 3; this viewer reads versions 1 and 2',
    });
  });

  it('accepts a strict v1 payload without v2 provenance or artifact trial links', () => {
    const v2 = bundle();
    const v1: Record<string, unknown> = { ...v2, bundle_version: 1 };
    delete v1.provenance;
    for (const artifact of v1.artifacts as Record<string, unknown>[]) delete artifact.trial_id;
    const result = validateBundle(v1);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.value.bundle_version).toBe(1);
  });

  it('rejects v1 payloads containing v2-only fields', () => {
    const v1 = { ...bundle(), bundle_version: 1 };
    const issues = issuesOf(validateBundle(v1));
    expect(issues.map((issue) => issue.path)).toContain('/provenance');
    expect(issues.map((issue) => issue.path)).toContain('/artifacts/0/trial_id');
  });

  it('rejects a malformed artifact digest', () => {
    const b = bundle();
    (b.artifacts as { sha256: string }[])[0]!.sha256 = 'XYZ';
    const issues = issuesOf(validateBundle(b));
    expect(issues.map((i) => i.path)).toContain('/artifacts/0/sha256');
  });

  it('rejects a timestamp that is not a date-time', () => {
    const b = bundle();
    (b.run as { started_at: string }).started_at = 'yesterday';
    expect(issuesOf(validateBundle(b)).map((i) => i.path)).toContain('/run/started_at');
  });

  it.each([null, 42, 'bundle', [], true])('rejects a non-object document: %j', (input) => {
    const issues = issuesOf(validateBundle(input));
    expect(issues).toHaveLength(1);
    expect(issues[0]?.path).toBe('');
  });

  it('rejects every bundle that is missing one required top-level section', () => {
    const required = [
      'bundle_version',
      'run',
      'provenance',
      'contestants',
      'trials',
      'calls',
      'scores',
      'sessions',
      'kit_installs',
      'artifacts',
    ];
    for (const key of required) {
      const b = bundle();
      delete b[key];
      expect(validateBundle(b).ok, `without ${key}`).toBe(false);
    }
  });
});

describe('validateEvent', () => {
  const events = () =>
    fixture('events/events.jsonl')
      .split('\n')
      .filter((l) => l.trim() !== '')
      .map((l) => JSON.parse(l) as Record<string, unknown>);

  it('accepts every event in the fixture stream', () => {
    const all = events();
    expect(all).toHaveLength(7);
    for (const e of all) expect(validateEvent(e).ok, JSON.stringify(e)).toBe(true);
  });

  it('requires data for kinds the schema types (session_turn)', () => {
    const e = events().find((x) => x.kind === 'session_turn')!;
    delete e.data;
    expect(issuesOf(validateEvent(e)).map((i) => i.path)).toContain('/data');
  });

  it('checks the typed data of a skill event', () => {
    const e = events().find((x) => x.kind === 'skill_event')!;
    (e.data as { skill_event: { kind: string } }).skill_event.kind = 'forgotten';
    const issues = issuesOf(validateEvent(e));
    expect(issues.map((i) => i.path)).toContain('/data/skill_event/kind');
  });

  it('rejects an unknown event kind', () => {
    const e = { ...events()[0]!, kind: 'run_exploded' };
    expect(issuesOf(validateEvent(e)).map((i) => i.path)).toContain('/kind');
  });
});
