/**
 * The closure pages of ADR 0037 with the keyboard only: from the banner of an upcoming closure
 * to its page and back to the calendar, the closed team's page in place of the calendar, and
 * a booking on hold with its contact options before its actions. Needs the closure examples
 * (python -m app.cli seed-closures > e2e/fixtures.json).
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';
import { fixture } from '../pages';

test('an upcoming closure: banner, closure page and back to the calendar', async ({ page }) => {
  await page.goto('/g/demo-gepland');
  await expect(page.getByRole('grid')).toBeVisible();

  await tabTo(page, page.getByRole('link', { name: 'Meer over de sluiting' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Storing bij het loket');
  await tabTo(page, page.getByRole('link', { name: '14 020' }));
  await expectNoAxeViolations(page);

  await tabTo(page, page.getByRole('link', { name: 'Naar de kalender' }));
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/g\/demo-gepland$/);
});

test('a closed team shows its closure page instead of the calendar', async ({ page }) => {
  await page.goto('/g/demo-gesloten?lang=en');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Outage at the desk');
  await expect(page.getByRole('grid')).toHaveCount(0);
  await tabTo(page, page.getByRole('link', { name: 'www.example.org/contact' }));
  await expectNoAxeViolations(page);
});

test('a booking on hold shows the contact options before its actions', async ({ page }) => {
  await page.goto(fixture('suspended_booking'));

  await tabTo(page, page.getByRole('link', { name: '14 020' }));
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'www.example.org/contact' })).toBeFocused();
  await tabTo(page, page.getByRole('link', { name: 'Kies een nieuwe tijd' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Afspraak verplaatsen');
});
