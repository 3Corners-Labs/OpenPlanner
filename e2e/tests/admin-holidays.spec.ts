/**
 * Holiday calendars (ADR 0025) through the real identity provider: the superuser fills the
 * national calendar with the keyboard (Platform > Holiday calendars) and lets an organisation
 * follow it on the organisation's Holidays tab; an organisation admin may choose calendars on
 * that tab but not maintain them (ADR 0047 step 3). Every page passes axe (WCAG 2.2 AA).
 *
 * The three browsers run in parallel against one database: filling a year skips dates that
 * exist and every browser ticks the same calendar, so the runs do not disturb each other.
 * Only the "andere" organisation follows the calendar, so the participant specs on "demo" keep
 * their opening hours.
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';
import { adminMenu, expectForbidden, loginAs, requireKeycloak } from '../keycloak';

requireKeycloak();

test('the superuser fills a calendar and an organisation follows it', async ({ page }) => {
  await loginAs(page, 'super', '/admin/holidays');
  await expect(adminMenu(page).getByRole('link', { name: 'Feestdagkalenders' })).toBeVisible();
  await expectNoAxeViolations(page);

  await page.getByRole('link', { name: 'Nationale feestdagen' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Nationale feestdagen');
  await tabTo(page, page.getByRole('button', { name: /Feestdagen van \d{4} toevoegen/ }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status')).toBeVisible();
  // exact: the remove button's label in the same row also mentions Koningsdag.
  await expect(page.getByRole('cell', { name: 'Koningsdag', exact: true })).toBeVisible();
  await expectNoAxeViolations(page);

  await page.goto('/admin/organisations/andere/holidays');
  await expectNoAxeViolations(page);
  const calendar = page.getByRole('checkbox', { name: 'Nationale feestdagen' });
  await tabTo(page, calendar);
  if (!(await calendar.isChecked())) await page.keyboard.press('Space');
  await tabTo(page, page.getByRole('button', { name: 'Opslaan' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status')).toContainText(/opgeslagen|niets gewijzigd/);

  await page.goto('/admin/groups/andere-team/holidays');
  await expect(page.getByText('Nu: Nationale feestdagen')).toBeVisible();
  await expectNoAxeViolations(page);
});

test('an organisation admin chooses calendars but cannot maintain them', async ({ page }) => {
  await loginAs(page, 'orgadmin', '/admin/organisations');
  await expect(page).toHaveURL(/\/admin\/organisations\/demo$/);
  // The calendars are the superuser's: no menu entry, and the page itself is refused.
  await expect(adminMenu(page).getByRole('link', { name: 'Feestdagkalenders' })).toHaveCount(0);
  await page
    .getByRole('navigation', { name: 'Onderdelen van de organisatie' })
    .getByRole('link', { name: 'Feestdagen' })
    .click();
  await expect(page).toHaveURL(/\/admin\/organisations\/demo\/holidays$/);
  await expect(page.getByRole('checkbox', { name: 'Nationale feestdagen' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Nationale feestdagen' })).toHaveCount(0);
  await expectNoAxeViolations(page);
  await expectForbidden(page, '/admin/holidays');
  await expectForbidden(page, '/admin/holidays/nationaal');
});
