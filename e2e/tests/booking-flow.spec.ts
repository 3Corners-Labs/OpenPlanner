/**
 * The citizen's booking flow with the keyboard only (no mouse), checked with axe at every
 * step: choose a slot, fill in the form, book, view, move and cancel the appointment.
 * Needs the demo group (python -m app.cli seed-demo).
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';

test('book, move and cancel with the keyboard only', async ({ page }) => {
  await page.goto('/g/demo');
  await expectNoAxeViolations(page);

  // Choose the first free slot: Tab into the radio group, Space selects it.
  const firstSlot = page.locator('input[name="start"]').first();
  await tabTo(page, firstSlot);
  await page.keyboard.press('Space');
  await expect(firstSlot).toBeChecked();
  await tabTo(page, page.getByRole('button', { name: 'Verder' }));
  await page.keyboard.press('Enter');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Uw gegevens');
  await expectNoAxeViolations(page);

  // Submitting an invalid email shows the error summary, focused, linked to the field.
  await tabTo(page, page.getByLabel('Naam'));
  await page.keyboard.type('Ada Lovelace');
  await page.keyboard.press('Tab');
  await page.keyboard.type('not-an-email');
  await tabTo(page, page.getByRole('button', { name: 'Afspraak maken' }));
  await page.keyboard.press('Enter');
  const summary = page.getByRole('alert');
  await expect(summary).toBeFocused();
  await expect(page.getByLabel('E-mailadres')).toHaveAttribute('aria-invalid', 'true');
  await expectNoAxeViolations(page);

  await page.getByLabel('E-mailadres').focus();
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.type('ada@example.org');
  await tabTo(page, page.getByRole('button', { name: 'Afspraak maken' }));
  await page.keyboard.press('Enter');

  await expect(page.getByRole('status')).toContainText('Uw afspraak is gemaakt');
  await expectNoAxeViolations(page);

  // Move the appointment.
  await tabTo(page, page.getByRole('link', { name: 'Afspraak verplaatsen' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Afspraak verplaatsen');
  await expectNoAxeViolations(page);
  await tabTo(page, page.locator('input[name="start"]').first());
  await page.keyboard.press('Space');
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
