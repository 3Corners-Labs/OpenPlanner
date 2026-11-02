/**
 * The participant's booking flow with the keyboard only (no mouse), checked with axe at every
 * step: choose a day in the calendar grid (arrow keys, ADR 0020) and a time, fill in the form,
 * book, confirm by the emailed link (ADR 0056), move and cancel the appointment. The link is
 * only in the email, so the confirming part starts from a seeded booking that awaits
 * confirmation. A second test runs the same pages without JavaScript. Needs the demo group
 * (python -m app.cli seed-demo) and the seeded examples (seed-closures > e2e/fixtures.json).
 */
import { expect, test } from '@playwright/test';

import { participantEmail, expectNoAxeViolations, tabTo } from '../helpers';
import { fixture } from '../pages';

test('book, confirm, move and cancel with the keyboard only', async ({ page }, testInfo) => {
  await page.goto('/g/demo');
  await expectNoAxeViolations(page);

  // The calendar is one tab stop (an ARIA grid); arrow keys move between days.
  const grid = page.getByRole('grid');
  // Any day can hold the tab stop: a day without times (a span) as well as a free one (a link).
  const selected = grid.locator('.calendar__day[tabindex="0"]');
  const day = await selected.getAttribute('data-day');
  await tabTo(page, selected);
  await page.keyboard.press('ArrowRight');
  await expect(selected).not.toHaveAttribute('data-day', day ?? '');
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator(`[data-day="${day}"]`)).toBeFocused();
  // Enter loads that day's times; focus moves to their heading and the result is announced.
  await page.keyboard.press('Enter');
  await expect(page.locator('#times-heading')).toBeFocused();
  await expect(page.locator('[data-announce]')).toContainText('vrij');
  await expectNoAxeViolations(page);

  // A time button takes the participant to the form.
  await tabTo(page, page.locator('#times .time-button').first());
  await page.keyboard.press('Enter');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Uw gegevens');
  await expectNoAxeViolations(page);

  // Submitting an invalid email shows the error summary, focused, linked to the field.
  await tabTo(page, page.getByLabel('Voornaam'));
  await page.keyboard.type('Ada');
  await page.keyboard.press('Tab');
  await page.keyboard.type('Lovelace');
  await page.keyboard.press('Tab');
  await page.keyboard.type('not-an-email');
  // A custom field of the demo group (ADR 0055): a checkbox, ticked with the space bar.
  await tabTo(page, page.getByLabel('Ik wil graag een tolk'));
  await page.keyboard.press('Space');
  await expect(page.getByLabel('Ik wil graag een tolk')).toBeChecked();
  await tabTo(page, page.getByRole('button', { name: 'Afspraak maken' }));
  await page.keyboard.press('Enter');
  const summary = page.getByRole('alert');
  await expect(summary).toBeFocused();
  await expect(page.getByLabel('E-mailadres')).toHaveAttribute('aria-invalid', 'true');
  await expectNoAxeViolations(page);

  await page.getByLabel('E-mailadres').focus();
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.type(participantEmail(testInfo));
  await tabTo(page, page.getByRole('button', { name: 'Afspraak maken' }));
  await page.keyboard.press('Enter');

  // ADR 0056: the participant is asked to confirm by the link in the email.
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Bevestig uw afspraak');
  await expect(page.getByRole('status')).toContainText('We hebben u een e-mail gestuurd');
  await expectNoAxeViolations(page);

  // The emailed link opens the confirm page; only its button confirms. Each browser confirms
  // its own seeded booking, since a confirmed one no longer shows the button.
  // ADR 0093: the link's token is traded for a cookie; the address that stays holds none.
  const emailed = fixture(`pending_flow_${testInfo.project.name}`);
  await page.goto(emailed);
  expect(new URL(page.url()).pathname).toMatch(/^\/b\/m\/[\w-]+\/confirm$/);
  expect(page.url()).not.toContain(emailed.split('/')[2]);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Afspraak bevestigen');
  await expectNoAxeViolations(page);
  await tabTo(page, page.getByRole('button', { name: 'Afspraak bevestigen' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status')).toContainText('Uw afspraak is bevestigd');
  await expectNoAxeViolations(page);

  // Move the appointment.
  await tabTo(page, page.getByRole('link', { name: 'Afspraak verplaatsen' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Afspraak verplaatsen');
  await expectNoAxeViolations(page);
  await tabTo(page, page.locator('#times .time-button').first());
  await page.keyboard.press('Enter');
  // A new time is confirmed first: from → to.
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Nieuw moment bevestigen');
  await expectNoAxeViolations(page);
  await tabTo(page, page.getByRole('button', { name: 'Verplaatsen' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status')).toContainText('Uw afspraak is verplaatst');

  // Cancel it.
  await tabTo(page, page.getByRole('link', { name: 'Afspraak annuleren' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Afspraak annuleren');
  await expectNoAxeViolations(page);
  await tabTo(page, page.getByRole('button', { name: 'Ja, annuleren' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status')).toContainText('Uw afspraak is geannuleerd');
  await expectNoAxeViolations(page);
});

test('the calendar works without JavaScript', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, locale: 'nl-NL' });
  const page = await context.newPage();
  await page.goto('/g/demo');

  // Without the script the calendar is a plain table of links, not a grid.
  await expect(page.getByRole('grid')).toHaveCount(0);
  // The last free day of the month: late in a month there may be only one.
  const days = page.locator('a.calendar__day');
  const last = days.last();
  const label = await last.getAttribute('data-day');
  await last.click();
  await expect(page).toHaveURL(new RegExp(`day=${label}`));
  await expect(page.locator(`a[data-day="${label}"]`)).toHaveAttribute('aria-current', 'true');
  // No axe here: axe runs as a script. The same markup is scanned in the other tests.

  await page.locator('#times .time-button').first().click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Uw gegevens');
  await context.close();
});

test('"Wanneer past het u?" narrows the times, with the keyboard', async ({ page }) => {
  await page.goto('/g/demo');
  const summary = page.locator('details.filter summary');
  await tabTo(page, summary);
  await page.keyboard.press('Enter');
  await tabTo(page, page.getByRole('checkbox', { name: 'vrijdag' }));
  await page.keyboard.press('Space');
  await page.getByLabel('Vanaf').selectOption('13');
  await tabTo(page, page.getByRole('button', { name: 'Toon passende tijden' }));
  await page.keyboard.press('Enter');

  await expect(page).toHaveURL(/days=4&from=13/);
  await expect(page.locator('.quick-pick')).toContainText(/Eerst passend: vrijdag/);
  await expect(page.locator('#times-heading')).toContainText('vrijdag');
  await expect(page.locator('#times').getByText('Ochtend')).toHaveCount(0);
  await expectNoAxeViolations(page);
});
