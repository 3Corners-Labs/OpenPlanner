/**
 * Typed access to pages.json, the single list of public pages under test.
 */
import { readFileSync } from 'node:fs';

export interface PageUnderTest {
  /** Human-readable name, used in test titles. */
  name: string;
  /** Path relative to BASE_URL, e.g. "/g/aangifte-2025". */
  path: string;
  /** Locales the page must pass in; each is requested with ?lang=<locale>. */
  locales: string[];
}

const file = new URL('./pages.json', import.meta.url);

/** Every (page, locale) combination as a URL path with the lang query parameter. */
export function pageVariants(): { title: string; path: string }[] {
  const { pages } = JSON.parse(readFileSync(file, 'utf8')) as { pages: PageUnderTest[] };
  return pages.flatMap((page) =>
    page.locales.map((locale) => {
      const separator = page.path.includes('?') ? '&' : '?';
      return { title: `${page.name} [${locale}]`, path: `${page.path}${separator}lang=${locale}` };
    }),
  );
}
