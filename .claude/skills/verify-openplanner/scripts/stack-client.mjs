/**
 * The client of the verification stack that stack.sh started: its run record, the instance
 * guard, the development users, Mailpit and the participant's slot picker. One home for these,
 * shared by journey.mjs and the role-manuals skill (../../role-manuals/scripts/), so a label or
 * route that changes is fixed once.
 *
 * Every function throws on a broken precondition; the caller decides how to stop. Nothing here
 * writes evidence: booking-link tokens and passwords stay in memory, and blankTokens() is the one
 * way to make a URL or message safe to keep.
 *
 * Playwright comes from e2e/ (cd e2e && npm ci). The browser is PLAYWRIGHT_CHROMIUM_CHANNEL,
 * by default chrome (an installed Google Chrome); set it empty for Playwright's own Chromium.
 */
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/** The root of the checkout this skill lives in. */
export const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..');
/** Where stack.sh keeps its runs (VERIFY_EVIDENCE_ROOT, as stack.sh reads it). */
export const EVIDENCE_ROOT = process.env.VERIFY_EVIDENCE_ROOT || join(REPO, '.verify-openplanner');

/**
 * The interface texts the scripts drive, from labels.json (one home, checked against the
 * catalogue by tests/test_skill_labels.py). NL: the participant pages and the portal basics, as
 * text. SUBJECT: email subjects, as patterns. LABEL: every group as handles: an exact text as a
 * string, a prefix or part as an anchored pattern, a template as a function of its placeholders.
 */
const LABELS = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'labels.json'), 'utf8'));
const quoteRe = (text) => text.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');

function handle(entry) {
  if (entry.template) return (values) => entry.template.replace(/\{(\w+)\}/g, (all, key) => values[key] ?? all);
  if (entry.pattern) return new RegExp(entry.pattern);
  if (entry.match === 'prefix') return new RegExp(`^${quoteRe(entry.text)}`);
  if (entry.match === 'contains') return new RegExp(quoteRe(entry.text));
  return entry.text;
}

const groupOf = (name, make) => Object.fromEntries(Object.entries(LABELS[name]).map(([key, entry]) => [key, make(entry)]));

export const NL = groupOf('participant', (entry) => entry.text);
export const SUBJECT = groupOf('subject', (entry) => new RegExp(entry.pattern));
export const LABEL = Object.fromEntries(Object.keys(LABELS).filter((g) => typeof LABELS[g] === 'object').map((g) => [g, groupOf(g, handle)]));
/** The plain texts of a group, for checks that look for them in a page's text. */
export const textsOf = (group) => Object.values(LABELS[group]).map((entry) => entry.text);
/** The plain text of one label. */
export const textOf = (group, key) => LABELS[group][key].text;

/** The slug of the demo group of the seed. */
export const DEMO_GROUP = 'demo';

/** A booking-link token in a URL path; /b/m/<reference> does not match. */
export const TOKEN_PATH = /\/b\/[A-Za-z0-9_-]{20,}/g;
export const HAS_TOKEN = /\/b\/[A-Za-z0-9_-]{20,}/;
const MAIL_WAIT_MS = 90_000;

/** `text` with every booking-link token replaced by /b/<token>. */
export const blankTokens = (text) => String(text ?? '').replace(TOKEN_PATH, '/b/<token>');

export const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Poll `check` until it returns a truthy value; throw after `timeout` ms, naming `what`. */
export async function until(what, check, timeout = 15_000) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try {
      const value = await check();
      if (value) return value;
    } catch (error) {
      last = error;
    }
    await sleep(250);
  }
  throw new Error(`timed out waiting for ${what}${last ? ` (${last.message.split('\n')[0]})` : ''}`);
}

/** The current run that stack.sh recorded: its folder, addresses, build and participant values. */
export function loadRun() {
  const currentFile = join(EVIDENCE_ROOT, 'current');
  if (!existsSync(currentFile)) throw new Error(`no verification stack is up (no ${currentFile}); run stack.sh up`);
  const id = readFileSync(currentFile, 'utf8').trim();
  if (!/^\d{8}-\d{6}$/.test(id)) throw new Error(`${currentFile} does not hold a run id (YYYYMMDD-HHMMSS)`);
  const dir = join(EVIDENCE_ROOT, id);
  const env = {};
  for (const line of readFileSync(join(dir, 'stack.env'), 'utf8').split('\n')) {
    const at = line.indexOf('=');
    if (at > 0) env[line.slice(0, at)] = line.slice(at + 1);
  }
  if (env.VERIFY_STATE !== 'up') throw new Error(`run ${id} is ${env.VERIFY_STATE}, not up; run stack.sh doctor`);
  for (const key of ['VERIFY_WEB_PORT', 'VERIFY_KC_PORT', 'VERIFY_MAIL_PORT']) {
    if (!/^\d{4,5}$/.test(env[key] ?? '')) throw new Error(`${key} in ${join(dir, 'stack.env')} is not a port`);
  }
  return {
    id,
    dir,
    passwords: realmPasswords(),
    instance: env.INSTANCE_ID,
    web: `http://127.0.0.1:${env.VERIFY_WEB_PORT}`,
    keycloak: `http://127.0.0.1:${env.VERIFY_KC_PORT}`,
    mail: `http://127.0.0.1:${env.VERIFY_MAIL_PORT}`,
    build: {
      commit: env.BUILT_FROM_COMMIT ?? '?',
      image: env.IMAGE_ID ?? '?',
      localChanges: env.BUILT_WITH_LOCAL_CHANGES ?? '?',
    },
    participant: {
      first: env.PARTICIPANT_FIRST_NAME,
      last: env.PARTICIPANT_LAST_NAME,
      email: env.PARTICIPANT_EMAIL,
    },
  };
}

/** Refuse to drive an instance this run did not start (another stack on the port). */
export async function assertOurs(run) {
  const answer = await fetch(`${run.web}/api/v1/instance`).then((r) => r.json()).catch(() => null);
  if (!run.instance || answer?.instance_id !== run.instance) {
    throw new Error(`${run.web} is not the installation of run ${run.id}; run stack.sh doctor`);
  }
}

/** Playwright from e2e/, pinned there (e2e/package.json). */
export function loadPlaywright() {
  const require = createRequire(join(REPO, 'e2e', 'package.json'));
  try {
    return require('@playwright/test');
  } catch {
    throw new Error('Playwright is not installed in e2e/; run: (cd e2e && npm ci)');
  }
}

/** Start the browser of PLAYWRIGHT_CHROMIUM_CHANNEL (default: an installed Google Chrome). */
export async function launchBrowser({ headless = true } = {}) {
  const { chromium } = loadPlaywright();
  const channel = process.env.PLAYWRIGHT_CHROMIUM_CHANNEL ?? 'chrome';
  return chromium.launch({ channel: channel || undefined, headless });
}

/** The development test users of dev/keycloak/openplanner-realm.json. */
function realmUsers() {
  return JSON.parse(readFileSync(join(REPO, 'dev', 'keycloak', 'openplanner-realm.json'), 'utf8')).users ?? [];
}

const passwordOf = (user) => user?.credentials?.find((c) => c.type === 'password')?.value;

/** The password of a development test user, read from the realm it is defined in. */
export function testPassword(name) {
  const value = passwordOf(realmUsers().find((u) => u.username === name));
  if (!value) throw new Error(`dev/keycloak/openplanner-realm.json has no test user ${name} with a password`);
  return value;
}

/** Every test password of the realm, so evidence can blank one that a failed sign-in left on screen. */
export function realmPasswords() {
  return [...new Set(realmUsers().map(passwordOf).filter(Boolean))];
}

/** Heading level 1 of the page, or '' while there is none. */
export async function h1(page) {
  const heading = page.getByRole('heading', { level: 1 }).first();
  return (await heading.count()) ? (await heading.innerText()).trim() : '';
}

/** Wait until the page's level-1 heading reads `text`. */
export async function waitH1(page, text, timeout = 20_000) {
  await until(`h1 "${text}"`, async () => (await h1(page)) === text, timeout);
}

/** Text of the page's status messages (role status). */
export async function statusText(page) {
  return (await page.getByRole('status').allInnerTexts()).join(' ');
}

/** Wait until a status message (role status) holds `text`. */
export async function waitStatus(page, text, timeout = 20_000) {
  await until(`status "${text}"`, async () => (await statusText(page)).includes(text), timeout);
}

/** The messages to `email` in Mailpit (default: this run's participant), newest first. */
export async function mailFor(run, email = run.participant.email) {
  const query = encodeURIComponent(`to:"${email}"`);
  const response = await fetch(`${run.mail}/api/v1/search?query=${query}&limit=100`);
  if (!response.ok) throw new Error(`Mailpit search answered ${response.status}`);
  return (await response.json()).messages ?? [];
}

/** One message whole (subject, text, HTML, attachments). */
export async function mailMessage(run, id) {
  const response = await fetch(`${run.mail}/api/v1/message/${id}`);
  if (!response.ok) throw new Error(`Mailpit message answered ${response.status}`);
  return response.json();
}

/** Wait for a message to `email`, not in `seen`, whose subject matches; return it whole. */
export async function waitForMail(run, seen, subject, email = run.participant.email) {
  const summary = await until(
    `an email to ${email === run.participant.email ? "this run's participant" : 'the participant'} with subject ${subject}`,
    async () => (await mailFor(run, email)).find((m) => !seen.has(m.ID) && subject.test(m.Subject)),
    MAIL_WAIT_MS,
  );
  seen.add(summary.ID);
  return mailMessage(run, summary.ID);
}

/**
 * The booking links in a message's plain-text part: their URLs, and the same with tokens blanked.
 * Throws when `required` and there is none.
 */
export function linksIn(run, message, required = true) {
  const pattern = new RegExp(`${escapeRe(run.web)}/b/[A-Za-z0-9_-]{20,}(?:/[a-z]+)?`, 'g');
  const urls = [...new Set(String(message.Text ?? '').match(pattern) ?? [])];
  if (required && !urls.length) {
    throw new Error(`email "${message.Subject}" holds no booking link to ${run.web}; is PUBLIC_BASE_URL this stack's?`);
  }
  return { urls, shown: urls.map(blankTokens) };
}

/** The plain management link /b/<token> of a message; /b/<token>/join and /test lead elsewhere. */
export function manageLinkIn(run, message) {
  const url = linksIn(run, message).urls.find((u) => !u.slice(u.indexOf('/b/') + 3).includes('/'));
  if (!url) throw new Error(`email "${message.Subject}" has no management link`);
  return url;
}

/** The /confirm link of a confirmation request email. */
export function confirmLinkIn(run, message) {
  const url = linksIn(run, message).urls.find((u) => u.endsWith('/confirm'));
  if (!url) throw new Error(`email "${message.Subject}" has no /confirm link`);
  return url;
}

/** The booking reference in a /b/m/<reference> address. */
export const referenceOf = (page) => new URL(page.url()).pathname.split('/')[3];

/** `text` with booking-link tokens and the run's test passwords blanked: safe for evidence. */
export function safeText(run, text) {
  let out = blankTokens(text);
  for (const password of run.passwords) out = out.split(password).join('<password>');
  return out;
}

/** The agenda row of `firstName` whose row header starts at `timeLabel` ("HH:MM"). */
export async function agendaRow(page, firstName, timeLabel, timeout = 15_000) {
  const row = page.getByRole('row').filter({ hasText: firstName })
    .filter({ has: page.locator('th', { hasText: new RegExp(`^${escapeRe(timeLabel)}–`) }) });
  await row.first().waitFor({ timeout });
  return row;
}

/**
 * Pick a day in the slot picker (`iso`, or the first free one) and wait until its times are
 * shown; return the day. Throws when `iso` is not a free day.
 */
export async function chooseDay(page, iso = null) {
  const day = iso ? page.locator(`a.calendar__day[data-day="${iso}"]`) : page.locator('a.calendar__day').first();
  if (iso && !(await day.count())) throw new Error(`day ${iso} is not free in the calendar on ${page.url()}`);
  await day.waitFor();
  const chosen = await day.getAttribute('data-day');
  await day.click();
  // calendar.js swaps in the day's times and puts ?day= in the address; without it, the link loads.
  await until(`the times of ${chosen}`, async () => {
    const url = new URL(page.url());
    const busy = await page.locator('#times[aria-busy="true"]').count();
    return url.searchParams.get('day') === chosen && !busy && (await page.locator('#times .time-button').count()) > 0;
  }, 20_000);
  return chosen;
}

/** Sign in at Keycloak as a development test user and wait until the portal answers. */
export async function login(page, run, user, path = '/admin/') {
  await page.goto(path);
  if (new URL(page.url()).pathname === '/admin/login') {
    // The e-mail-first page, only when organisation identity providers route logins (ADR 0083).
    await page.locator('#identifier').fill(user);
    await page.locator('#identifier').press('Enter');
  }
  await until(`the Keycloak sign-in for ${user}`, () => page.url().startsWith(`${run.keycloak}/`));
  await page.locator('#username').fill(user);
  await page.locator('#password').fill(testPassword(user));
  await page.locator('#password').press('Enter');
  await until(`the portal after signing in as ${user}`, () => {
    const url = new URL(page.url());
    return url.origin === run.web && url.pathname.startsWith('/admin');
  }, 30_000);
}
