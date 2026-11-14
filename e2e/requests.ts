/**
 * Global set-up of the immediate pages (ADR 0121 gate item G06): two requests without an
 * appointment, one of them claimed by the e2e agent, written to requests.json.
 *
 * The request page /r/{ref} opens only for the browser that holds its cookie, and the claimed
 * request's page only for the agent who holds the claim, so neither can be a fixed path in
 * pages.json. Before the suite runs, a browser sends the immediate form of the demonstration
 * (python -m app.cli seed-demonstration) twice, each time in a context of its own (one open
 * request per browser and service), and the agent picks up the oldest one in the queue. The
 * second request keeps waiting: its page is the participant's, polled by live-region.js.
 *
 * pages.json names the results in braces ("{immediate_request}"); pages.ts reads them when a
 * test opens the page, not when the tests are listed. The set-up runs only when pages.json names
 * one of them, so the suite needs no seed of its own while the rollback gate is shut.
 */
import { writeFileSync } from 'node:fs';

import { chromium, type Browser, type FullConfig } from '@playwright/test';

import { loginAs } from './keycloak';
import { fixture, REQUEST_COOKIE, SETUP_FILE, setUpNeeded, type SetUp } from './pages';

const QUEUE = '/admin/groups/demo/queue';

/** Send the immediate form in a fresh context; return the request's path and the cookie value. */
async function ask(browser: Browser, baseURL: string, firstName: string): Promise<{ path: string; cookie: string }> {
  const context = await browser.newContext({ baseURL, locale: 'nl-NL' });
  const page = await context.newPage();
  // The immediate form of the document review, as seed-closures reports it (fixtures.json).
  await page.goto(`${fixture('immediate_form')}&lang=nl`);
  await page.locator('input[name="first_name"]').fill(firstName);
  await page.locator('input[name="last_name"]').fill('Voorbeeld');
  await page.locator('input[name="email"]').fill(`${firstName.toLowerCase()}@voorbeeld.nl`);
  await page.locator('form.card button[type="submit"]').click();
  await page.waitForURL(/\/r\/[\w-]+$/);
  const path = new URL(page.url()).pathname;
  const cookie = (await context.cookies()).find((c) => c.name.endsWith(REQUEST_COOKIE));
  await context.close();
  if (cookie === undefined) throw new Error('the immediate form set no request cookie');
  return { path, cookie: cookie.value };
}

/** Sign in as the e2e agent and pick up the oldest request; return the claimed page's path. */
async function claimOldest(browser: Browser, baseURL: string): Promise<string> {
  const context = await browser.newContext({ baseURL, locale: 'nl-NL' });
  const page = await context.newPage();
  await loginAs(page, 'agent', QUEUE);
  await page.locator(`form[action="${QUEUE}/requests/next"] button`).click();
  await page.waitForURL(/\/queue\/requests\/\d+$/);
  const path = new URL(page.url()).pathname;
  // A person keeps few admin sessions; end this one as admin-shell.spec.ts does.
  await page.request.post('/admin/logout', { maxRedirects: 0 }).catch(() => undefined);
  await context.close();
  return path;
}

export default async function globalSetup(config: FullConfig): Promise<void> {
  // The maintenance step (MAINTENANCE_E2E) runs its own spec only and opens no listed page.
  if (!setUpNeeded() || process.env.MAINTENANCE_E2E) return;
  const baseURL = config.projects[0]?.use.baseURL ?? 'http://127.0.0.1:8000';
  const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHROMIUM_CHANNEL });
  try {
    await ask(browser, baseURL, 'Ada'); // the oldest: the agent's "pick up next" takes it
    const waiting = await ask(browser, baseURL, 'Bea');
    const setUp: SetUp = {
      immediate_request: waiting.path,
      immediate_request_cookie: waiting.cookie,
      claimed_request: await claimOldest(browser, baseURL),
    };
    writeFileSync(SETUP_FILE, `${JSON.stringify(setUp, null, 2)}\n`);
  } finally {
    await browser.close();
  }
}
