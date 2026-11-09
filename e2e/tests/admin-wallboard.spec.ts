/**
 * The live wallboard (ADR 0117 step 13): complete with scripts off, the old-data state in words after
 * three failed polls (R11), pause, nothing that moves, text size for a 1080p screen. Needs Keycloak and
 * the demo seed like admin-reports.spec.ts.
 */
import { expect, test, type Page } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { loginAs, requireKeycloak } from '../keycloak';

requireKeycloak();

const GROUP = '/admin/groups/demo/usage';
const BOARD = `${GROUP}?view=wallboard`;
const KIOSK = `${BOARD}&layout=kiosk`;
const LIVE = `${GROUP}/wallboard/live`;

async function openAs(page: Page, path: string): Promise<void> {
  await loginAs(page, 'super', GROUP);
  await page.goto(path);
}

test('the board shows six tiles with an as-of each, and is complete with scripts off', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await loginAs(page, 'super', GROUP);
  await page.goto(BOARD);

  await expect(page.locator('.wb__tile')).toHaveCount(6);
  await expect(page.locator('.wb__asof')).toHaveCount(6);
  await expect(page.getByRole('link', { name: /Nu verversen|Refresh now/ })).toBeVisible();
  await expect(page.locator('.wb__pause')).toBeHidden();
  await context.close();
});

test('the full screen layout has no menu and no axe violations', async ({ page }) => {
  await openAs(page, KIOSK);

  await expect(page.locator('.admin-nav')).toHaveCount(0);
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expectNoAxeViolations(page);
});

test('the figures are 3.4 rem or more and nothing on the board moves', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await openAs(page, KIOSK);

  const size = await page.locator('.wb__n').first().evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(size).toBeGreaterThanOrEqual(3.4 * 16);
  const moving = await page.locator('.wb__tiles *, .wb__strip *').evaluateAll(
    (els) =>
      els.filter((el) => {
        const style = getComputedStyle(el);
        return style.animationName !== 'none' || style.transitionDuration !== '0s';
      }).length,
  );
  expect(moving).toBe(0);
});

test('three failed polls in a row show the old-data state in words, and a good answer ends it', async ({ page }) => {
  test.setTimeout(120_000);
  let failing = false;
  await page.route(`**${LIVE}*`, (route) => (failing ? route.fulfill({ status: 500, body: '' }) : route.continue()));
  await openAs(page, BOARD);
  const board = page.locator('.wb');
  const seconds = Number(await board.locator('[data-live-fragment]').getAttribute('data-poll-seconds'));
  const stale = page.locator('.wb__banner--stale');
  await expect(stale).toBeHidden();

  failing = true;
  await expect(stale).toBeVisible({ timeout: (3 * seconds + 5) * 1000 });
  await expect(stale).toContainText(/\d{2}:\d{2}:\d{2}/);
  await expect(board).toHaveAttribute('data-stale', '');
  await expect(page.locator('[data-live-announce]')).toContainText(/oud sinds|old since/);

  failing = false;
  await expect(stale).toBeHidden({ timeout: (seconds + 5) * 1000 });
  await expect(board).not.toHaveAttribute('data-stale', '');
});

test('pausing stops the asking and says so, resuming asks again', async ({ page }) => {
  let asked = 0;
  await page.route(`**${LIVE}*`, (route) => {
    asked += 1;
    return route.continue();
  });
  await openAs(page, BOARD);
  // The name changes while paused, so the button is found by its class, not by its name.
  const pause = page.locator('.wb__pause');

  await pause.click();
  await expect(pause).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.wb__banner--paused')).toBeVisible();
  const before = asked;
  await page.waitForTimeout(12000);
  expect(asked).toBe(before);

  await pause.click();
  await expect(pause).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(() => asked).toBeGreaterThan(before);
});
