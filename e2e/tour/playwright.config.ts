/**
 * The recorded walkthrough of the real interface (ADR 0087 step 3). Not part of `npm test`:
 * run with `npx playwright test -c tour/playwright.config.ts` against a seeded app with the
 * simulator and the development identity provider, as the accessibility job starts it.
 * The video, poster and chapter marks land in tour-output/; captions.mjs adds the captions.
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'tour.spec.ts',
  timeout: 300_000,
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL: process.env.BASE_URL ?? 'http://127.0.0.1:8000',
    locale: 'nl-NL',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], channel: process.env.PLAYWRIGHT_CHROMIUM_CHANNEL },
    },
  ],
});
