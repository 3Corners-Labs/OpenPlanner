/**
 * An admin arranges their own overview (ADR 0096 step 3) with the keyboard only: remove a
 * widget, move one, add it back, restore the default. Every page passes axe (WCAG 2.2 AA) and
 * there is no drag-only control (2.5.7).
 *
 * A layout belongs to one person, so the run is chromium only: the browsers share the users.
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';
import { loginAs, requireKeycloak } from '../keycloak';

requireKeycloak();
test.skip(({ browserName }) => browserName !== 'chromium', 'one layout per user: chromium only');

test('the auditor removes, moves, adds back and restores with the keyboard', async ({ page }) => {
  await loginAs(page, 'auditor', '/admin/');
  const widgets = page.locator('[data-widget]');
  const before = await widgets.evaluateAll((els) => els.map((el) => el.getAttribute('data-widget')));
  expect(before.length).toBeGreaterThan(1);

  await tabTo(page, page.getByRole('link', { name: 'Overzicht aanpassen' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Overzicht aanpassen');
  await expectNoAxeViolations(page);

  // Remove the first widget; the confirmation is a status message.
  const first = page.locator('.layout-list > li').first();
  const title = (await first.locator('.layout-list__title').innerText()).trim();
  await tabTo(page, page.getByRole('button', { name: `${title} weghalen` }));
  await page.keyboard.press('Enter');
  await expect(page.getByText('De widget is weggehaald.')).toBeVisible();
  await expect(page.getByText('Dit is uw eigen indeling.')).toBeVisible();
  await expectNoAxeViolations(page);

  // Move the (new) last widget up.
  const last = page.locator('.layout-list > li').last();
  const lastTitle = (await last.locator('.layout-list__title').innerText()).trim();
  await tabTo(page, page.getByRole('button', { name: `${lastTitle} omhoog` }));
  await page.keyboard.press('Enter');
  await expect(page.getByText('De volgorde is aangepast.')).toBeVisible();

  // Add the removed widget back from Add widget.
  await page.goto('/admin/overview/add');
  await expectNoAxeViolations(page);
  await tabTo(page, page.getByRole('button', { name: `${title} toevoegen` }));
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/admin\/\?done=widget_added#widget-/);
  await expect(page.getByText('De widget staat op uw overzicht.')).toBeVisible();

  // Restore the default layout.
  await page.goto('/admin/overview/edit');
  await tabTo(page, page.getByRole('button', { name: 'Standaardindeling herstellen' }));
  await page.keyboard.press('Enter');
  await expect(page.getByText('U ziet weer de standaardindeling van uw rol.')).toBeVisible();
  const after = await widgets.evaluateAll((els) => els.map((el) => el.getAttribute('data-widget')));
  expect(after).toEqual(before);
});
