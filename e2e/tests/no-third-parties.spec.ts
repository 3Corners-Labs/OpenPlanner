/**
 * No request to a third party (ASVS V14.2.3): every public page, in every locale, makes no
 * request to an origin other than the app host (and, on the widget's host page, that page's
 * own origin). No font, script, analytics or CDN reaches a participant's browser, so no
 * participant's address or page visit is sent to anyone else. The configured video link is the
 * one deliberate exit and only after a click, which this test never makes.
 * The CSP check proves the same from the policy: it names no source but 'self', 'none' and
 * the two origins above, so a future template change cannot add one unseen.
 */
import { expect, test, type Page } from '@playwright/test';

import { pageVariants } from '../pages';

const APP = new URL(process.env.BASE_URL ?? 'http://127.0.0.1:8000').origin;
/** Schemes that never leave the browser. */
const LOCAL_SCHEMES = ['data:', 'blob:', 'about:'];

/** Open `path` and return every origin the page contacted, its Content-Security-Policy and own origin. */
async function visit(page: Page, path: string): Promise<{ origins: Set<string>; csp: string; own: string }> {
  const origins = new Set<string>();
  const note = (url: string): void => {
    if (!LOCAL_SCHEMES.some((scheme) => url.startsWith(scheme))) origins.add(new URL(url).origin);
  };
  page.on('request', (request) => note(request.url()));
  page.on('websocket', (socket) => note(socket.url().replace(/^ws/, 'http')));
  // networkidle: the widget pages render after their API calls (ADR 0035).
  const response = await page.goto(path, { waitUntil: 'networkidle' });
  return {
    origins,
    csp: (await response?.headerValue('content-security-policy')) ?? '',
    own: new URL(page.url()).origin,
  };
}

for (const variant of pageVariants()) {
  test(`no third-party request: ${variant.title}`, async ({ page }) => {
    const { origins, csp, own } = await visit(page, variant.path);
    const allowed = new Set([APP, own]);
    expect([...origins].filter((origin) => !allowed.has(origin))).toEqual([]);

    // Every source in the policy is 'self', 'none' or one of the two origins; no wildcard,
    // no scheme-only source such as https:, no other host.
    const sources = csp
      .split(';')
      .flatMap((directive) => directive.trim().split(/\s+/).slice(1))
      .filter((source) => source !== '');
    const foreign = sources.filter((source) => source !== "'self'" && source !== "'none'" && !allowed.has(source));
    expect(foreign).toEqual([]);
  });
}
