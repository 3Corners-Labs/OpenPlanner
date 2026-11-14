/**
 * Typed access to pages.json, the single list of pages under test: public pages, and admin
 * pages that need a login (tests/admin-shell.spec.ts).
 *
 * A path may name a seeded example in braces, e.g. "{suspended_booking}": its value comes from
 * fixtures.json, written by `python -m app.cli seed-closures > e2e/fixtures.json` (a management
 * link exists only after seeding, so it cannot be in pages.json). The names in `SETUP_KEYS`
 * come from requests.json instead, which the global set-up (requests.ts) writes just before the
 * tests run; they are read when a test opens its page, never while the tests are listed.
 */
import { existsSync, readFileSync } from 'node:fs';

import type { TestUser } from './keycloak';

export interface PageUnderTest {
  /** Human-readable name, used in test titles. */
  name: string;
  /** Path relative to BASE_URL, e.g. "/g/balie-2025", a "{fixture}" placeholder, or an absolute URL (the widget host page). */
  path: string;
  /** Locales the page must pass in; each is requested with ?lang=<locale>. */
  locales: string[];
  /** The page carries islands (ADR 0118, docs/islands.md): tests/islands.spec.ts runs the island gates on it. */
  island?: boolean;
  /** A set-up value (`SETUP_KEYS`) the browser holds as the request cookie before it opens the page. */
  cookie?: string;
}

export interface AdminPageUnderTest extends PageUnderTest {
  /** The Keycloak test user who opens the page (keycloak.ts). */
  user: TestUser;
}

const file = new URL('./pages.json', import.meta.url);
const fixtureFile = new URL('./fixtures.json', import.meta.url);
/** Written by the global set-up (requests.ts); read when a page that names it is opened. */
export const SETUP_FILE = new URL('./requests.json', import.meta.url);
/** The cookie of an immediate request over plain http (app/api/immediate_pages.py, COOKIE_BASE). */
export const REQUEST_COOKIE = 'op_req';

/** What the global set-up writes: two requests without an appointment, one claimed (G06). */
export interface SetUp {
  /** The page of the request that keeps waiting. */
  immediate_request: string;
  /** The value of its cookie, `<reference>.<random value>`. */
  immediate_request_cookie: string;
  /** The page of the request the e2e agent claimed. */
  claimed_request: string;
}

const SETUP_KEYS: ReadonlySet<string> = new Set(['immediate_request', 'immediate_request_cookie', 'claimed_request']);

/** The participant's journey of one browser (ADR 0071, 0075, 0076): two seeded bookings. */
export interface JourneyExample {
  /** The trial call page of a booking tomorrow or later. */
  trial_page: string;
  /** The admin booking page of that booking. */
  trial_booking: string;
  /** The join page of a booking that starts in ten minutes. */
  call_page: string;
  /** The admin booking page of that booking. */
  call_booking: string;
  /** The group's agenda on the day that call starts (not "today": midnight moves it). */
  call_agenda: string;
}

const SEED_COMMAND = 'run python -m app.cli seed-closures > e2e/fixtures.json';

function fixtures(): Record<string, unknown> {
  return existsSync(fixtureFile) ? (JSON.parse(readFileSync(fixtureFile, 'utf8')) as Record<string, unknown>) : {};
}

/** Return the seeded example `name` from fixtures.json; fails with the command that writes it. */
export function fixture(name: string): string {
  const value = fixtures()[name];
  if (typeof value !== 'string') throw new Error(`fixture "${name}" missing: ${SEED_COMMAND}`);
  return value;
}

/** Return the seeded journey of the browser `project` (chromium, firefox or webkit). */
export function journey(project: string): JourneyExample {
  const journeys = fixtures().journeys as Record<string, JourneyExample> | undefined;
  const value = journeys?.[project];
  if (value === undefined) throw new Error(`journey of "${project}" missing: ${SEED_COMMAND}`);
  return value;
}

/** One page in one locale. `path` and `cookie` are read when a test opens the page. */
export interface Variant {
  readonly title: string;
  readonly path: string;
  readonly cookie?: { name: string; value: string };
  readonly user?: TestUser;
}

function listed(): { pages: PageUnderTest[]; admin: AdminPageUnderTest[] } {
  return JSON.parse(readFileSync(file, 'utf8')) as { pages: PageUnderTest[]; admin: AdminPageUnderTest[] };
}

/** Whether any page names a value of the global set-up, so requests.ts must run. */
export function setUpNeeded(): boolean {
  const { pages, admin } = listed();
  return [...pages, ...admin].some((page) => page.cookie !== undefined || SETUP_KEYS.has(placeholder(page.path) ?? ''));
}

/** Return the value `name` of the global set-up; fails when requests.ts did not write it. */
function setUp(name: string): string {
  const values = existsSync(SETUP_FILE) ? (JSON.parse(readFileSync(SETUP_FILE, 'utf8')) as Record<string, unknown>) : {};
  const value = values[name];
  if (typeof value !== 'string') throw new Error(`set-up value "${name}" missing: the global set-up (requests.ts) did not run`);
  return value;
}

/** Every (page, locale) combination as a URL path with the lang query parameter. */
export function pageVariants(): Variant[] {
  return variants(listed().pages);
}

/** Every (admin page, locale) combination with the user who logs in for it. */
export function adminPageVariants(): Variant[] {
  return listed().admin.flatMap((page) => variants([page], { user: page.user }));
}

/** The island pages (public and admin) with the user who logs in for an admin one; ADR 0118 point 10. */
export function islandVariants(): Variant[] {
  const { pages, admin } = listed();
  return [
    ...variants(pages.filter((p) => p.island)),
    ...admin.filter((p) => p.island).flatMap((p) => variants([p], { user: p.user, prefix: 'admin ' })),
  ];
}

function placeholder(path: string): string | undefined {
  return /^\{(\w+)\}$/.exec(path)?.[1];
}

function variants(pages: PageUnderTest[], extra: { user?: TestUser; prefix?: string } = {}): Variant[] {
  return pages.flatMap((page) => {
    const name = placeholder(page.path);
    const later = name !== undefined && SETUP_KEYS.has(name);
    const path = later ? '' : page.path.replace(/^\{(\w+)\}$/, (_, seeded: string) => fixture(seeded));
    // A placeholder the resolver does not take (it names a whole path) must not reach the browser as text.
    if (/[{}]/.test(path)) throw new Error(`unresolved placeholder in "${page.path}" of pages.json`);
    const cookie = page.cookie;
    return page.locales.map((locale): Variant => ({
      title: `${extra.prefix ?? ''}${page.name} [${locale}]`,
      user: extra.user,
      get path() {
        const base = later && name !== undefined ? setUp(name) : path;
        return `${base}${base.includes('?') ? '&' : '?'}lang=${locale}`;
      },
      get cookie() {
        return cookie === undefined ? undefined : { name: REQUEST_COOKIE, value: setUp(cookie) };
      },
    }));
  });
}
