/**
 * The <gov-booking> widget on a host page of another origin (ADR 0035, EM-4): the booking
 * flow with the keyboard only, axe at every step, inside the shadow root. The host page
 * (e2e/host/, started by playwright.config.ts) has a CSP with only what docs/widget.md asks
 * hosts to allow. Needs the demo seed, which allows http://localhost:8001 for the demo group.
 */
import { expect, test } from '@playwright/test';

import { citizenEmail, expectNoAxeViolations, tabTo } from '../helpers';

const HOST = process.env.WIDGET_HOST_URL ?? 'http://localhost:8001/';

test('book through the widget with the keyboard only', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto(HOST);
  const widget = page.locator('gov-booking');
  await expect(widget.getByRole('heading', { level: 2, name: 'Demo: videogesprek' })).toBeVisible();
  await expectNoAxeViolations(page);

  // The calendar is one tab stop (an ARIA grid); arrow keys move between days.
  const grid = widget.getByRole('grid');
  const active = grid.locator('.calendar__day[tabindex="0"]');
  const day = await active.getAttribute('data-day');
  await tabTo(page, active);
  await page.keyboard.press('ArrowRight');
  await expect(active).not.toHaveAttribute('data-day', day ?? '');
  await page.keyboard.press('ArrowLeft');
  await expect(grid.locator(`[data-day="${day}"]`)).toBeFocused();
  // Enter shows that day's times; focus moves to their heading and the count is announced.
  await page.keyboard.press('Enter');
  await expect(widget.locator('#times-heading')).toBeFocused();
  await expect(widget.locator('[aria-live="polite"]')).toContainText('vrij');
  await expectNoAxeViolations(page);

  // Each browser takes another time from the end of the day, so parallel runs do not collide.
  const index = ['chromium', 'firefox', 'webkit'].indexOf(info.project.name);
  const times = widget.locator('.time');
  await tabTo(page, times.nth((await times.count()) - 1 - Math.max(0, index)));
  await page.keyboard.press('Enter');
  await expect(widget.getByRole('heading', { level: 2, name: 'Uw gegevens' })).toBeFocused();
  await expectNoAxeViolations(page);

  // An invalid email: the error summary is focused and linked to the field.
  await tabTo(page, widget.getByLabel('Voornaam'));
  await page.keyboard.type('Ada');
  await tabTo(page, widget.getByLabel('Achternaam'));
  await page.keyboard.type('Lovelace');
  await tabTo(page, widget.getByLabel('E-mailadres'));
  await page.keyboard.type('not-an-email');
  await tabTo(page, widget.getByRole('button', { name: 'Afspraak maken' }));
  await page.keyboard.press('Enter');
  await expect(widget.getByRole('alert')).toBeFocused();
  await expect(widget.getByLabel('E-mailadres')).toHaveAttribute('aria-invalid', 'true');
  await expectNoAxeViolations(page);

  // The summary's link moves focus to the input.
  await tabTo(page, widget.getByRole('link', { name: /geldig e-mailadres/ }));
  await page.keyboard.press('Enter');
  await expect(widget.getByLabel('E-mailadres')).toBeFocused();
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.type(citizenEmail(info));
  await tabTo(page, widget.getByRole('button', { name: 'Afspraak maken' }));
  await page.keyboard.press('Enter');

  await expect(widget.getByRole('heading', { level: 2, name: 'Uw afspraak is gemaakt' })).toBeFocused();
  await expect(widget.getByRole('status')).toContainText('bevestiging per e-mail');
  await expectNoAxeViolations(page);
  // No CSP violations or script errors on the strict host page; the refused email above is
  // an expected 422, which browsers log as a failed resource.
  expect(errors.filter((text) => !text.startsWith('Failed to load resource'))).toEqual([]);
});

test('the widget speaks English when the host page does', async ({ page }) => {
  await page.goto(`${HOST}?lang=en`);

  const widget = page.locator('gov-booking');
  await expect(widget.getByRole('region', { name: 'Book an appointment' })).toBeVisible();
  await expect(widget.getByRole('list', { name: /steps/i })).toBeVisible();
});

test('a group that is not embedded here says so', async ({ page }) => {
  await page.goto(`${HOST}?organisation=andere&group=andere-team`);

  await expect(page.locator('gov-booking')).toContainText('niet beschikbaar');
  await expectNoAxeViolations(page);
});

test('the host styles the widget through tokens and parts only', async ({ page }) => {
  await page.goto(`${HOST}?theme=gemeente`);

  const free = page.locator('gov-booking .calendar__day--free').first();
  await expect(free).toHaveCSS('border-top-color', 'rgb(138, 21, 56)');
  const heading = page.locator('gov-booking').getByRole('heading', { level: 2 }).first();
  await expect(heading).toHaveCSS('font-family', /Georgia/);
});
