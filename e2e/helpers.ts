/**
 * Shared steps for the browser specs: the axe-core check and keyboard-only navigation.
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, type Locator, type Page, type TestInfo } from '@playwright/test';

/** The axe rule tags that together make up WCAG 2.2 AA. */
export const WCAG_22_AA = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

/** Fail the test if axe finds any WCAG 2.2 AA violation on the current page. */
export async function expectNoAxeViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_22_AA).analyze();
  expect(results.violations).toEqual([]);
}

/**
 * An email address of its own for this test, browser and attempt. All browsers book against
 * one server, and an address may hold only a few bookings per group (ADR 0066), so a shared
 * address would make the later browsers' runs fail.
 */
export function participantEmail(testInfo: TestInfo): string {
  return `participant.${testInfo.project.name}.${testInfo.testId}.${testInfo.retry}@example.org`;
}

/** Press Tab until `locator` has focus; fails if it is not reachable with the keyboard. */
export async function tabTo(page: Page, locator: Locator): Promise<void> {
  for (let i = 0; i < 200; i++) {
    // Inside a shadow root (the widget) the root, not the document, knows the focused element.
    if (await locator.evaluate((el) => el === (el.getRootNode() as Document | ShadowRoot).activeElement)) return;
    await page.keyboard.press('Tab');
  }
  throw new Error('element not reachable with Tab');
}
