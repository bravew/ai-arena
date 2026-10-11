import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// The schemas under ../docs are the published contract; the app imports them rather than copying.
const repoRoot = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
  // Relative asset URLs, so a static export opens from any path or from file://.
  base: './',
  plugins: [react()],
  server: { fs: { allow: [repoRoot] } },
  test: {
    environment: 'jsdom',
    include: ['tests/unit/**/*.test.{ts,tsx}'],
    setupFiles: ['tests/testenv/setup.ts'],
  },
});
