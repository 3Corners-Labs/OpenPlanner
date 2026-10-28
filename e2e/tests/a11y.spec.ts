/**
 * axe-core scan of every public page in every locale: zero violations of WCAG 2.2 AA.
 * axe catches roughly 40% of issues; keyboard-flow specs and manual audits cover the rest.
 */
import { test } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { pageVariants } from '../pages';

for (const variant of pageVariants()) {
  test(`axe: ${variant.title}`, async ({ page }) => {
    // networkidle: the widget pages render after their API calls (ADR 0035).
    await page.goto(variant.path, { waitUntil: 'networkidle' });
    await expectNoAxeViolations(page);
  });
}
