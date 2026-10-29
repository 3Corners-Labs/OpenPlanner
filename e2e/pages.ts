/**
 * Typed access to pages.json, the single list of public pages under test.
 *
 * A path may name a seeded example in braces, e.g. "{suspended_booking}": its value comes from
 * fixtures.json, written by `python -m app.cli seed-closures > e2e/fixtures.json` (a management
 * link exists only after seeding, so it cannot be in pages.json).
 */
import { existsSync, readFileSync } from 'node:fs';

export interface PageUnderTest {
  /** Human-readable name, used in test titles. */
  name: string;
  /** Path relative to BASE_URL, e.g. "/g/aangifte-2025", a "{fixture}" placeholder, or an absolute URL (the widget host page). */
  path: string;
  /** Locales the page must pass in; each is requested with ?lang=<locale>. */
  locales: string[];
}

const file = new URL('./pages.json', import.meta.url);
const fixtureFile = new URL('./fixtures.json', import.meta.url);

/** Return the seeded example `name` from fixtures.json; fails with the command that writes it. */
export function fixture(name: string): string {
  const fixtures = existsSync(fixtureFile)
    ? (JSON.parse(readFileSync(fixtureFile, 'utf8')) as Record<string, string>)
    : {};
  const value = fixtures[name];
  if (value === undefined) {
    throw new Error(
      `fixture "${name}" missing: run python -m app.cli seed-closures > e2e/fixtures.json`,
    );
  }
  return value;
}

/** Every (page, locale) combination as a URL path with the lang query parameter. */
export function pageVariants(): { title: string; path: string }[] {
  const { pages } = JSON.parse(readFileSync(file, 'utf8')) as { pages: PageUnderTest[] };
  return pages.flatMap((page) => {
    const path = page.path.replace(/^\{(\w+)\}$/, (_, name: string) => fixture(name));
    return page.locales.map((locale) => {
      const separator = path.includes('?') ? '&' : '?';
      return { title: `${page.name} [${locale}]`, path: `${path}${separator}lang=${locale}` };
    });
  });
}
