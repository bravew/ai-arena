import { describe, expect, it } from 'vitest';
import { RealHomeGuard, AGENT_STATE, defaultAllowedRoots } from '../testenv/guard';
import { REAL_HOME, isSecretName, installTempHome } from '../testenv/env';

describe('web testenv', () => {
  it('redirects all configured HOME and agent directories beneath the temporary HOME', () => {
    const home = installTempHome();
    for (const name of ['HOME', 'USERPROFILE', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'ARENA_HOME']) {
      expect(process.env[name]).toContain(home);
    }
    expect(process.env.HOME).not.toBe(REAL_HOME);
  });

  it('recognizes provider secrets by the same names as the Python fixture harness', () => {
    expect(isSecretName('ANTHROPIC_API_KEY')).toBe(true);
    expect(isSecretName('SOME_AUTH_TOKEN')).toBe(true);
    expect(isSecretName('ARENA_RUN_KEY')).toBe(true);
    expect(isSecretName('PATH')).toBe(false);
  });

  it('blocks reads of real agent state and all writes beneath real HOME', () => {
    const home = installTempHome();
    const guard = new RealHomeGuard(REAL_HOME, defaultAllowedRoots(home, '/repo', REAL_HOME));
    for (const item of AGENT_STATE) {
      expect(guard.check(`${REAL_HOME}/${item}/settings.json`, false)).toContain('read of real agent');
    }
    expect(guard.check(`${REAL_HOME}/notes.txt`, true)).toContain('write under the real HOME');
    expect(guard.check(`${home}/notes.txt`, true)).toBeUndefined();
  });
});
