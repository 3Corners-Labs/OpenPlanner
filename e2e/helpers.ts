/**
 * Shared steps for the browser specs: the axe-core check and keyboard-only navigation.
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, type Locator, type Page, type TestInfo } from '@playwright/test';

/** The axe rule tags that together make up WCAG 2.2 AA. */
export const WCAG_22_AA = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

/**
 * Give the browser the cookie a page of pages.json needs before it opens (`cookie`: the request
 * page of ADR 0121, which opens only for the browser that holds its request's cookie).
 */
export async function holdCookie(
  page: Page,
  variant: { cookie?: { name: string; value: string } },
  baseURL?: string,
): Promise<void> {
  const cookie = variant.cookie;
  if (cookie === undefined) return;
  await page.context().addCookies([{ ...cookie, url: baseURL ?? 'http://127.0.0.1:8000' }]);
}

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

/** The minimum notice of the demo group (`seed-demo`, `min_notice_minutes`). */
export const DEMO_NOTICE_MINUTES = 60;
/** How far past the notice a chosen time must lie, so it stays offered while a test runs. */
const NOTICE_MARGIN_MINUTES = 10;

/**
 * Return the first time button of the shown day whose start lies clear of the minimum notice,
 * or `null` when the day has none.
 *
 * A time offered when the page renders stops being offered once the notice passes it: a test
 * that takes the first time a few seconds before a half hour is sent back with
 * `status=unavailable` (seen on webkit at 13:59:56 local time). Skipping the times inside the
 * margin keeps the choice the same for the whole test, whatever the hour.
 */
export async function timeClearOfNotice(page: Page): Promise<Locator | null> {
  const buttons = page.locator('#times .time-button');
  const starts = await buttons.evaluateAll((els) =>
    els.map((el) => Date.parse((el as HTMLButtonElement).value)),
  );
  const earliest = Date.now() + (DEMO_NOTICE_MINUTES + NOTICE_MARGIN_MINUTES) * 60_000;
  const index = starts.findIndex((start) => start >= earliest);
  return index < 0 ? null : buttons.nth(index);
}

/**
 * Return a time clear of the minimum notice, moving through the calendar grid with the arrow
 * keys and Enter to the next free day while the shown day has none (late on a day, its last
 * times can all lie inside the margin). Keyboard only, as the tests that call it.
 */
export async function chooseTimeByKeyboard(page: Page): Promise<Locator> {
  for (let day = 0; day < 31; day++) {
    const time = await timeClearOfNotice(page);
    if (time !== null) return time;
    const grid = page.getByRole('grid');
    const current = await grid.locator('.calendar__day[tabindex="0"]').getAttribute('data-day');
    await tabTo(page, grid.locator('.calendar__day[tabindex="0"]'));
    // Move right until a later day that has times (a link) holds the tab stop.
    for (let step = 0; step < 31; step++) {
      await page.keyboard.press('ArrowRight');
      const focused = page.locator('.calendar__day[tabindex="0"]');
      const next = await focused.getAttribute('data-day');
      const isLink = await focused.evaluate((el) => el.tagName === 'A');
      if (next !== null && current !== null && next > current && isLink) break;
    }
    await page.keyboard.press('Enter');
    await expect(page.locator('#times-heading')).toBeFocused();
  }
  throw new Error('no time clear of the minimum notice in the coming month');
}
