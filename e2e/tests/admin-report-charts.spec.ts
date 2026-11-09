/**
 * The interactive usage charts (ADR 0117 step 12, report-charts.js): a tip on keyboard focus, the
 * series toggles as buttons with aria-pressed, the downloads, Enter on a day, and that the page is
 * complete with scripts off. Needs Keycloak and the demo seed like admin-reports.spec.ts.
 */
import { expect, test, type Page } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { loginAs, requireKeycloak } from '../keycloak';

requireKeycloak();

const GROUP = '/admin/groups/demo/usage';

async function openAs(page: Page, path: string): Promise<void> {
  await loginAs(page, 'super', GROUP);
  await page.goto(path);
}

test('arrow keys show a tip made from the table, and Escape closes it', async ({ page }) => {
  await openAs(page, `${GROUP}?view=queue`);

  const frame = page.locator('.rchart__frame').first();
  await frame.focus();
  await page.keyboard.press('ArrowRight');
  const tip = page.locator('.rchart__tip').first();
  await expect(tip).toBeVisible();
  await expect(frame).toHaveAttribute('aria-describedby', /rchart-hint-/);
  await page.keyboard.press('Escape');
  await expect(tip).toBeHidden();
  await expect(frame).toBeFocused();
  await expectNoAxeViolations(page);
});

test('a series toggle is a button with aria-pressed and hides that series', async ({ page }) => {
  await openAs(page, `${GROUP}?view=channels`);

  const chart = page.locator('op-report-chart').first();
  // Pin the button by its name: a locator for "the first pressed button" moves on once it is not pressed.
  const name = (await chart.getByRole('button', { pressed: true }).first().textContent())?.trim() ?? '';
  const toggle = chart.getByRole('button', { name, exact: true });
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await expect(chart.locator('.rchart__off').first()).toBeAttached();
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
});

test('the chart downloads as SVG and as PNG', async ({ page }) => {
  await openAs(page, `${GROUP}?view=queue`);

  const chart = page.locator('op-report-chart').first();
  for (const kind of ['svg', 'png']) {
    const download = page.waitForEvent('download');
    await chart.getByRole('button', { name: new RegExp(kind, 'i') }).click();
    expect((await download).suggestedFilename()).toMatch(new RegExp(`\\.${kind}$`));
  }
});

test('with scripts off the chart, its table and the day links are all there', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await loginAs(page, 'super', GROUP);
  await page.goto(`${GROUP}?view=volume`);

  await expect(page.locator('figure.rchart table').first()).toBeVisible();
  await expect(page.locator('op-report-chart button')).toHaveCount(0);
  await expect(page.locator('figure.rchart table a').first()).toBeVisible();
  await context.close();
});
