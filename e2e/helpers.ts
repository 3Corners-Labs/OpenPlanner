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
  await pressUntilFocused(page, locator, 'Tab');
}

/**
 * Press Shift+Tab until `locator` has focus: for a target that comes before the focused element.
 *
 * Tab never wraps round to the top of the page reliably: past the last element Firefox moves
 * focus into the browser itself and the page gets it back only after a variable number of
 * presses. A target earlier in the page is therefore reached backwards, never by wrapping.
 */
export async function shiftTabTo(page: Page, locator: Locator): Promise<void> {
  await pressUntilFocused(page, locator, 'Shift+Tab');
}

async function pressUntilFocused(page: Page, locator: Locator, key: string): Promise<void> {
  for (let i = 0; i < 200; i++) {
    // Inside a shadow root (the widget) the root, not the document, knows the focused element.
    if (await locator.evaluate((el) => el === (el.getRootNode() as Document | ShadowRoot).activeElement)) return;
    await page.keyboard.press(key);
  }
  throw new Error(`element not reachable with ${key}`);
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

/** How many months `chooseTimeByKeyboard` looks ahead before it gives up. */
const MONTHS_AHEAD = 3;

/**
 * Return a time clear of the minimum notice, moving through the calendar grid with the keyboard
 * to the next free day while the shown day has none (late on a day, its last times can all lie
 * inside the margin). Keyboard only, as the tests that call it.
 *
 * The next free day is read from the grid's links (a day with times is a link, a closed or full
 * day is not) and reached with ArrowRight; when the shown month has no later free day, PageDown
 * opens the next month and the same rule picks its first free day, never the day that merely
 * holds the tab stop. The grid comes before the times in the page, so it is reached with
 * Shift+Tab from the times (`shiftTabTo`, never by wrapping round the page); after PageDown the
 * page is new, focus is at its top and Tab reaches it.
 */
export async function chooseTimeByKeyboard(page: Page): Promise<Locator> {
  for (let attempt = 0; attempt < 31 * MONTHS_AHEAD; attempt++) {
    const time = await timeClearOfNotice(page);
    if (time !== null) return time;
    await openNextFreeDay(page);
  }
  throw new Error(`no time clear of the minimum notice in the coming ${MONTHS_AHEAD} months`);
}

/** The grid's tab stop: the one day of the calendar that Tab reaches. */
function tabStop(page: Page): Locator {
  return page.getByRole('grid').locator('.calendar__day[tabindex="0"]');
}

/** Return the days of the shown month that have times (links) and come after `after`. */
async function freeDaysAfter(page: Page, after: string): Promise<string[]> {
  return page
    .getByRole('grid')
    .locator('a.calendar__day')
    .evaluateAll(
      (links, day) =>
        links.map((link) => link.getAttribute('data-day') ?? '').filter((d) => d > day),
      after,
    );
}

/**
 * Open the next free day after the shown one with the keyboard: in this month if it has one,
 * else in the first following month that has one (at most `MONTHS_AHEAD`).
 */
async function openNextFreeDay(page: Page): Promise<void> {
  const stop = tabStop(page);
  let after = (await stop.getAttribute('data-day')) ?? '';
  await shiftTabTo(page, stop);
  for (let month = 0; month <= MONTHS_AHEAD; month++) {
    const [target] = await freeDaysAfter(page, after);
    if (target !== undefined) {
      await arrowTo(page, target);
      await page.keyboard.press('Enter');
      await expect(page.locator('#times-heading')).toBeFocused();
      await expect(page.locator(`a.calendar__day[data-day="${target}"]`)).toHaveAttribute(
        'aria-current',
        'true',
      );
      return;
    }
    // No later free day this month: the next month is a new page; every day of it is later.
    const shown = (await stop.getAttribute('data-day')) ?? '';
    await page.keyboard.press('PageDown');
    await expect(stop).not.toHaveAttribute('data-day', shown);
    await tabTo(page, stop);
    after = '';
  }
  throw new Error(`no free day in the coming ${MONTHS_AHEAD} months`);
}

/** Move the focused grid's roving tab stop with ArrowRight to the day `target` of this month. */
async function arrowTo(page: Page, target: string): Promise<void> {
  const stop = tabStop(page);
  for (let step = 0; (await stop.getAttribute('data-day')) !== target; step++) {
    if (step > 42) throw new Error(`the arrow keys did not reach ${target}`);
    await page.keyboard.press('ArrowRight');
  }
  await expect(stop).toBeFocused();
}
