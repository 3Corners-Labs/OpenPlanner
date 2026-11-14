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

import { holdCookie } from '../helpers';
import { pageVariants } from '../pages';

const APP = new URL(process.env.BASE_URL ?? 'http://127.0.0.1:8000').origin;
/**
 * The one recorded exception (ADR 0123 point 5): the call page (`/b/m/{ref}/call`, the route that
 * is served with CALL_CSP) may contact these hosts AFTER the click that joins, never before it.
 * Every other page, and the call page before the click, stays under the rule above. Nothing uses
 * the allowance yet: the step-2 page makes no request at all, and it is not in `pages.json` (it
 * needs a booking session, like the other `/b/m/` pages). The step that builds the click (step 4)
 * uses `mayContact(origin, AFTER_JOIN_CLICK)` in its own test and nowhere else.
 */
const CALL_HOST_PATTERNS = [
  /^(https|wss):\/\/[^/]+\.wbx2\.com$/,
  /^(https|wss):\/\/[^/]+\.webex\.com$/,
  /^https:\/\/[^/]+\.ciscospark\.com$/,
];
type Phase = 'every-page' | 'call-page-before-click' | 'call-page-after-click';
const AFTER_JOIN_CLICK: Phase = 'call-page-after-click';

/** Whether `origin` may be contacted in `phase`: only the call page after the click may reach the call's hosts. */
function mayContact(origin: string, phase: Phase): boolean {
  if (origin === APP) return true;
  return phase === AFTER_JOIN_CLICK && CALL_HOST_PATTERNS.some((pattern) => pattern.test(origin));
}

test('the call hosts are allowed after the join click only', () => {
  const host = 'https://mercury-connection-a.wbx2.com';
  expect(mayContact(host, 'call-page-after-click')).toBe(true);
  expect(mayContact(host, 'call-page-before-click')).toBe(false);
  expect(mayContact(host, 'every-page')).toBe(false);
  expect(mayContact('https://evil.example', 'call-page-after-click')).toBe(false);
  // CALL_CSP names *.host: subdomains only, over the listed schemes, never the bare name.
  expect(mayContact('https://wbx2.com', 'call-page-after-click')).toBe(false);
  expect(mayContact('https://ciscospark.com', 'call-page-after-click')).toBe(false);
  expect(mayContact('wss://x.webex.com', 'call-page-after-click')).toBe(true);
  expect(mayContact('wss://x.ciscospark.com', 'call-page-after-click')).toBe(false);
  expect(mayContact(APP, 'every-page')).toBe(true);
});

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
  test(`no third-party request: ${variant.title}`, async ({ page, baseURL }) => {
    await holdCookie(page, variant, baseURL);
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
