/**
 * axe-core scan of every public page in every locale: zero violations of WCAG 2.2 AA.
 * axe catches roughly 40% of issues; keyboard-flow specs and manual audits cover the rest.
 */
import { test } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { pageVariants } from '../pages';

for (const variant of pageVariants()) {
  test(`axe: ${variant.title}`, async ({ page }) => {
    await page.goto(variant.path);
    await expectNoAxeViolations(page);
  });
}
