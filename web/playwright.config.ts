import { defineConfig, devices } from '@playwright/test';
import { setupRealBrowserCache, installTempHome } from './tests/testenv/env';

// Browsers live in the user's cache, outside any agent config. Point at it explicitly before HOME
// moves, then run everything (including the browsers) under a temporary HOME.
setupRealBrowserCache();
installTempHome();

const port = 4173;

export default defineConfig({
  testDir: 'tests',
  testMatch: '*.spec.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${port}` },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
  webServer: {
    command: `pnpm build && pnpm exec vite preview --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
