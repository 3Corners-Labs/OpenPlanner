/**
 * axe-core scan of every public page in every locale: zero violations of WCAG 2.2 AA.
 * axe catches roughly 40% of issues; keyboard-flow specs and manual audits cover the rest.
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { pageVariants } from '../pages';

for (const variant of pageVariants()) {
  test(`axe: ${variant.title}`, async ({ page }) => {
    // networkidle: the widget pages render after their API calls (ADR 0035).
    await page.goto(variant.path, { waitUntil: 'networkidle' });
    // ADR 0093: the link from an email trades its token for a cookie and ends on a page whose
    // address holds no credential. The trial, join and waiting pages follow in a later step.
    if (/^\/b\/[^/?]+(\/confirm)?(\?|$)/.test(variant.path)) {
      const token = variant.path.split(/[/?]/)[2];
      expect(new URL(page.url()).pathname).toMatch(/^\/b\/m\/[\w-]+(\/confirm)?$/);
      expect(page.url()).not.toContain(token);
    }
    await expectNoAxeViolations(page);
  });
}
