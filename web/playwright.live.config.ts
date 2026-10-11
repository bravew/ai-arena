import { mkdtempSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';
import { setupRealBrowserCache, installTempHome } from './tests/testenv/env';

// Runs the vote page against a real `arena serve` over a seeded temporary data home.
setupRealBrowserCache();
installTempHome();

const port = 4191;
// Workers load this file again and inherit the environment, so create the home only once.
const home = (process.env.ARENA_VOTE_HOME ??= mkdtempSync(path.join(os.tmpdir(), 'arena-vote-home-')));
const repo = fileURLToPath(new URL('../', import.meta.url));

export default defineConfig({
  testDir: 'tests',
  testMatch: 'arena-live.spec.ts',
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${port}` },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command:
      `pnpm build && cd ${repo} && uv run python -I tests/server/seed_vote_home.py ${home} 20 && ` +
      `uv run arena serve --home ${home} --port ${port} --artifact-port ${port + 1}`,
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
