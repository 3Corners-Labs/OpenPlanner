/**
 * axe-core scan of every public page in every locale: zero violations of WCAG 2.2 AA.
 * axe catches roughly 40% of issues; keyboard-flow specs and manual audits cover the rest.
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { pageVariants } from '../pages';

const WCAG_22_AA = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

for (const variant of pageVariants()) {
  test(`axe: ${variant.title}`, async ({ page }) => {
    await page.goto(variant.path);
    const results = await new AxeBuilder({ page }).withTags(WCAG_22_AA).analyze();
    expect(results.violations).toEqual([]);
  });
}
