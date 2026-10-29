/**
 * Shared steps for the browser specs: the axe-core check and keyboard-only navigation.
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, type Locator, type Page } from '@playwright/test';

/** The axe rule tags that together make up WCAG 2.2 AA. */
export const WCAG_22_AA = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

/** Fail the test if axe finds any WCAG 2.2 AA violation on the current page. */
export async function expectNoAxeViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_22_AA).analyze();
  expect(results.violations).toEqual([]);
}

/**
 * The demo group's page on the month a week from now: always several free weekdays, also in
 * the last days of a month, when the current month may have only one left (horizon 60 days).
 */
export function demoMonthPath(): string {
  const later = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
  const month = `${later.getUTCFullYear()}-${String(later.getUTCMonth() + 1).padStart(2, '0')}`;
  return `/g/demo?month=${month}`;
}

/** Press Tab until `locator` has focus; fails if it is not reachable with the keyboard. */
export async function tabTo(page: Page, locator: Locator): Promise<void> {
  for (let i = 0; i < 200; i++) {
    if (await locator.evaluate((el) => el === document.activeElement)) return;
    await page.keyboard.press('Tab');
  }
  throw new Error('element not reachable with Tab');
}
