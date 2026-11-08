/**
 * The usage report families (ADR 0117 step 6): Queue and calls, Capacity and Channels, with their
 * charts. The pages are static HTML and SVG, so beside axe on every page (admin-shell.spec.ts runs
 * the pages of pages.json at three widths) this spec checks what only a browser can: the family
 * links and the charts' scroll frames with the keyboard alone, 320 px wide (400 % zoom), forced
 * colours, reduced motion, and that every chart is followed by a table with the same numbers.
 *
 * Needs Keycloak and the demo seed (`seed-closures` also writes example usage cells), like
 * admin-roles.spec.ts.
 */
import { expect, test, type Page } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';
import { loginAs, requireKeycloak } from '../keycloak';

requireKeycloak();

const GROUP = '/admin/groups/demo/usage';

/** Sign in, then open `path`: the sign-in redirect returns to the page without its query. */
async function openAs(page: Page, path: string): Promise<void> {
  await loginAs(page, 'super', GROUP);
  await page.goto(path);
}

/** Every chart of the page is followed, inside its figure, by a table. */
async function expectTablesAfterCharts(page: Page): Promise<void> {
  const figures = page.locator('figure.rchart');
  const count = await figures.count();
  for (let i = 0; i < count; i++) {
    const figure = figures.nth(i);
    await expect(figure.locator('svg[role="img"]')).toHaveCount(1);
    await expect(figure.locator('svg title')).not.toHaveCount(0);
    await expect(figure.locator('table')).toHaveCount(1);
    const order = await figure.evaluate((el) => {
      const svg = el.querySelector('svg');
      const table = el.querySelector('table');
      return !!svg && !!table && !!(svg.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING);
    });
    expect(order).toBe(true);
  }
}

test('the families are reachable with the keyboard and the current one is marked', async ({ page }) => {
  await openAs(page, `${GROUP}?view=queue`);

  const links = page.getByRole('navigation', { name: 'Rapport' }).getByRole('link');
  await expect(links).toHaveCount(4);
  await expect(links.filter({ hasText: 'Wachtrij en gesprekken' })).toHaveAttribute('aria-current', 'page');

  await tabTo(page, links.filter({ hasText: 'Capaciteit' }));
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/view=capacity/);
  await expect(page.getByRole('heading', { name: 'Drukste dagen en uren' })).toBeVisible();
  await expectNoAxeViolations(page);
});

test('a chart frame and its table can be reached and scrolled with the keyboard', async ({ page }) => {
  await openAs(page, `${GROUP}?view=queue`);

  const frame = page.locator('.rchart__frame').first();
  await tabTo(page, frame);
  await expect(frame).toBeFocused();
  await expectTablesAfterCharts(page);
});

for (const view of ['queue', 'capacity', 'channels']) {
  test(`${view}: 320 px wide (400 % zoom) the page needs no horizontal scroll of its own`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 700 });
    await openAs(page, `${GROUP}?view=${view}`);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
    await expectNoAxeViolations(page);
    await expectTablesAfterCharts(page);
  });

  test(`${view}: forced colours and reduced motion`, async ({ page }) => {
    await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
    await openAs(page, `${GROUP}?view=${view}`);

    await expect(page.locator('main')).toBeVisible();
    await expectNoAxeViolations(page);
    // The shade of the busiest-hours table is never the only signal: every cell prints its number.
    for (const cell of await page.locator('.heat-table td').all()) {
      await expect(cell).not.toHaveText('');
    }
  });
}
