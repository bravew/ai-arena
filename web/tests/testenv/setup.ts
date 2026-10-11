// Vitest setup: every test file runs under a temporary HOME with the real-HOME guard on.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach } from 'vitest';
import { RealHomeGuard, activate, deactivate, defaultAllowedRoots } from './guard';
import { REAL_HOME, installTempHome } from './env';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

let guard: RealHomeGuard | undefined;

beforeEach(() => {
  const home = installTempHome();
  guard = new RealHomeGuard(REAL_HOME, defaultAllowedRoots(home, repoRoot, REAL_HOME));
  activate(guard);
});

afterEach(() => {
  deactivate();
  const violations = guard?.violations ?? [];
  guard = undefined;
  if (violations.length > 0) {
    throw new Error(`test touched the real HOME:\n  ${violations.join('\n  ')}`);
  }
});

export function currentGuard(): RealHomeGuard {
  if (!guard) throw new Error('no guard: only call this inside a test');
  return guard;
}
