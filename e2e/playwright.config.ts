/**
 * Playwright configuration. The app under test is started outside Playwright (CI starts the
 * container; locally `docker compose up`), and its address is passed in BASE_URL.
 * Set PLAYWRIGHT_CHROMIUM_CHANNEL=chrome to use an installed Google Chrome instead of the
 * downloaded Chromium (useful where the browser download is blocked).
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL: process.env.BASE_URL ?? 'http://127.0.0.1:8000',
    trace: 'retain-on-failure',
    // Dutch browser by default; specs that test detection override it.
    locale: 'nl-NL',
  },
  webServer: {
    command: 'node host/serve.mjs',
    url: process.env.WIDGET_HOST_URL ?? 'http://localhost:8001/',
    reuseExistingServer: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], channel: process.env.PLAYWRIGHT_CHROMIUM_CHANNEL },
    },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
});
