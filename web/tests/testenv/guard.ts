// Fail any test that writes under the real HOME or reads a real agent config.
//
// The Node `fs` entry points are wrapped while a guard is active. An access the guard forbids
// throws and is also recorded, so a test that swallows the error still fails at teardown.
// Subprocesses are not guarded; they inherit the temporary HOME instead.
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import os from 'node:os';
import path from 'node:path';

// Agent and arena state that tests may not even read, relative to the real HOME.
export const AGENT_STATE = [
  '.claude',
  '.claude.json',
  '.codex',
  '.config',
  '.gemini',
  '.pi',
  '.local/share/opencode',
  '.aider.conf.yml',
  '.arena',
];

// function name -> (indexes of path arguments, whether it writes). Each exists as `fs.name`,
// `fs.nameSync` and `fs.promises.name` where Node has them.
const PATH_CALLS: Record<string, [number[], boolean]> = {
  writeFile: [[0], true],
  appendFile: [[0], true],
  mkdir: [[0], true],
  mkdtemp: [[0], true],
  rm: [[0], true],
  rmdir: [[0], true],
  unlink: [[0], true],
  rename: [[0, 1], true],
  copyFile: [[1], true],
  cp: [[1], true],
  symlink: [[1], true],
  link: [[1], true],
  truncate: [[0], true],
  chmod: [[0], true],
  utimes: [[0], true],
  readFile: [[0], false],
  readdir: [[0], false],
  opendir: [[0], false],
  stat: [[0], false],
  lstat: [[0], false],
  access: [[0], false],
};
const WRITE_FLAGS = /^(?:[wa]|r\+)/;

export class RealHomeAccess extends Error {}

function within(p: string, root: string): boolean {
  const rel = path.relative(root, p);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

export class RealHomeGuard {
  readonly violations: string[] = [];
  private readonly allowed: string[];
  private readonly agentState: string[];

  constructor(
    private readonly realHome: string,
    allowed: string[],
  ) {
    this.allowed = allowed.map((a) => path.resolve(a));
    this.agentState = AGENT_STATE.map((s) => path.join(realHome, s));
  }

  /** The reason `raw` is forbidden, or undefined when the access is fine. */
  check(raw: unknown, writes: boolean): string | undefined {
    if (typeof raw !== 'string' && !(raw instanceof URL) && !Buffer.isBuffer(raw)) return undefined;
    let p: string;
    try {
      p = path.resolve(raw instanceof URL ? raw.pathname : raw.toString());
    } catch {
      return undefined;
    }
    if (!within(p, this.realHome) || this.allowed.some((a) => within(p, a))) return undefined;
    if (writes) return `write under the real HOME: ${p}`;
    if (this.agentState.some((s) => within(p, s))) return `read of real agent or arena state: ${p}`;
    return undefined;
  }

  enforce(raw: unknown, writes: boolean): void {
    const reason = this.check(raw, writes);
    if (reason === undefined) return;
    this.violations.push(reason);
    throw new RealHomeAccess(reason);
  }
}

type Fn = (...args: unknown[]) => unknown;
type Restore = () => void;

function wrap(target: object, name: string, guard: RealHomeGuard, indexes: number[], writes: boolean): Restore | undefined {
  const original = (target as Record<string, unknown>)[name];
  if (typeof original !== 'function') return undefined;
  (target as Record<string, unknown>)[name] = function (this: unknown, ...args: unknown[]) {
    for (const i of indexes) guard.enforce(args[i], writes);
    return (original as Fn).apply(this, args);
  };
  return () => {
    (target as Record<string, unknown>)[name] = original;
  };
}

/** open and openSync decide read or write from their flags (second argument). */
function wrapOpen(target: object, name: string, guard: RealHomeGuard): Restore | undefined {
  const original = (target as Record<string, unknown>)[name];
  if (typeof original !== 'function') return undefined;
  (target as Record<string, unknown>)[name] = function (this: unknown, ...args: unknown[]) {
    const flags = args[1];
    const writes = typeof flags === 'string' ? WRITE_FLAGS.test(flags) : typeof flags === 'number' && (flags & 3) !== 0;
    guard.enforce(args[0], writes);
    return (original as Fn).apply(this, args);
  };
  return () => {
    (target as Record<string, unknown>)[name] = original;
  };
}

let restores: Restore[] = [];

export function activate(guard: RealHomeGuard): void {
  deactivate();
  const add = (r: Restore | undefined) => {
    if (r) restores.push(r);
  };
  for (const target of [fs, fs.promises]) {
    for (const [name, [indexes, writes]] of Object.entries(PATH_CALLS)) {
      add(wrap(target, name, guard, indexes, writes));
      add(wrap(target, `${name}Sync`, guard, indexes, writes));
    }
  }
  add(wrapOpen(fs, 'openSync', guard));
  add(wrapOpen(fs, 'open', guard));
  add(wrapOpen(fs.promises, 'open', guard));
  add(wrap(fs, 'createWriteStream', guard, [0], true));
  add(wrap(fs, 'createReadStream', guard, [0], false));
  syncBuiltinESMExports();
}

export function deactivate(): void {
  for (const r of restores.reverse()) r();
  restores = [];
  syncBuiltinESMExports();
}

/** Where writes are fine: the temporary HOME, the repo, and the temp dir unless HOME is inside it. */
export function defaultAllowedRoots(home: string, repoRoot: string, realHome: string): string[] {
  const tmp = path.resolve(os.tmpdir());
  return within(path.resolve(realHome), tmp) ? [home, repoRoot] : [home, repoRoot, tmp];
}
