// The JS side of tests/testenv: a temporary HOME, agent config dirs, and no provider secrets.
// The Python plugin in ../../../tests/testenv/plugin.py is the model; keep the two lists in step.
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// Each of these points at a directory under the temporary HOME.
export const HOME_VARS: Record<string, string> = {
  HOME: '.',
  USERPROFILE: '.',
  XDG_CONFIG_HOME: '.config',
  XDG_DATA_HOME: '.local/share',
  XDG_CACHE_HOME: '.cache',
  XDG_STATE_HOME: '.local/state',
  CLAUDE_CONFIG_DIR: '.claude',
  CODEX_HOME: '.codex',
  PI_CODING_AGENT_DIR: '.pi/agent',
  ARENA_HOME: '.arena',
};

// Credentials a test must never see, so nothing can spend on a real provider by accident.
const SECRET_SUFFIXES = ['_API_KEY', '_AUTH_TOKEN', '_ACCESS_TOKEN', '_OAUTH_TOKEN'];
const SECRET_NAMES = ['GITHUB_TOKEN', 'GH_TOKEN', 'COPILOT_TOKEN', 'ARENA_RUN_KEY'];

// Set by whoever created the temporary HOME, so child processes (Playwright workers) reuse it.
const MARKER = 'ARENA_TESTENV_HOME';

/** The real home directory, read from the user database so a changed $HOME can't hide it. */
export const REAL_HOME = os.userInfo().homedir;

export function isSecretName(name: string): boolean {
  return SECRET_NAMES.includes(name) || SECRET_SUFFIXES.some((s) => name.endsWith(s));
}

/**
 * Point HOME and the agent config dirs at a fresh temporary directory and drop provider secrets.
 * Idempotent across processes: a child that inherits the marker reuses the parent's directory.
 */
export function installTempHome(): string {
  const inherited = process.env[MARKER];
  const owner = inherited === undefined;
  const home = inherited ?? mkdtempSync(path.join(os.tmpdir(), 'arena-web-home-'));
  for (const [name, sub] of Object.entries(HOME_VARS)) {
    const dir = path.join(home, sub);
    mkdirSync(dir, { recursive: true });
    process.env[name] = dir;
  }
  for (const name of Object.keys(process.env)) {
    if (isSecretName(name)) delete process.env[name];
  }
  process.env[MARKER] = home;
  if (owner) process.once('exit', () => rmSync(home, { recursive: true, force: true }));
  return home;
}

/** Playwright finds its browsers under HOME; pin that cache before HOME moves. */
export function setupRealBrowserCache(): void {
  if (process.env.PLAYWRIGHT_BROWSERS_PATH) return;
  if (process.platform === 'darwin') {
    process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(REAL_HOME, 'Library/Caches/ms-playwright');
  } else if (process.platform === 'linux') {
    const cache = process.env.XDG_CACHE_HOME ?? path.join(REAL_HOME, '.cache');
    process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(cache, 'ms-playwright');
  }
}
