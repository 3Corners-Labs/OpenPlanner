/**
 * axe-core scan of every public page in every locale: zero violations of WCAG 2.2 AA.
 * axe catches roughly 40% of issues; keyboard-flow specs and manual audits cover the rest.
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations, holdCookie } from '../helpers';
import { pageVariants } from '../pages';

for (const variant of pageVariants()) {
  test(`axe: ${variant.title}`, async ({ page, baseURL }) => {
    await holdCookie(page, variant, baseURL);
    // networkidle: the widget pages render after their API calls (ADR 0035).
    const response = await page.goto(variant.path, { waitUntil: 'networkidle' });
    // A page behind a cookie (pages.json `cookie`) must be the page itself, not the refusal.
    if (variant.cookie) expect(response?.status()).toBe(200);
    // ADR 0093: the link from an email trades its token for a cookie and ends on a page whose
    // address holds no credential: the confirm, trial and join pages included.
    if (/^\/b\/[^/?]+(\/(confirm|test|join))?(\?|$)/.test(variant.path)) {
      const token = variant.path.split(/[/?]/)[2];
      expect(new URL(page.url()).pathname).toMatch(/^\/b\/m\/[\w-]+(\/(confirm|test|join))?$/);
      expect(page.url()).not.toContain(token);
    }
    await expectNoAxeViolations(page);
  });
}
