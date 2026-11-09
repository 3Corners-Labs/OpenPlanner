/**
 * The group agenda (ADR 0044) through the real identity provider: the group admin opens the
 * day view and the week view of their group in Dutch and English, reaches the week view with
 * the keyboard, and both pass axe (WCAG 2.2 AA). The outsider is refused.
 *
 * The day view is the seeded day of the demo bookings (fixtures.json, key demo_agenda), not
 * today: whatever the date of the run, it holds a booking awaiting confirmation and a confirmed
 * one, so every row action is on the page and axe checks each target (WCAG 2.5.8). Today's
 * agenda holds such bookings only on some days and at some hours of the run.
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';
import { expectForbidden, loginAs, requireKeycloak } from '../keycloak';
import { fixture } from '../pages';

requireKeycloak();

const AGENDA = '/admin/groups/demo/agenda';

/** The row actions of the agenda (admin.agenda.*): links, and the button of a pending booking. */
const ROW_ACTIONS = {
  nl: {
    links: ['Details', 'Verplaatsen', 'Nieuwe tijd voorstellen', 'Om nieuwe tijd vragen', 'Annuleren'],
    button: 'Bevestigen namens deelnemer',
    week: 'Weekoverzicht',
  },
  en: {
    links: ['Details', 'Move', 'Propose a new time', 'Ask for a new time', 'Cancel'],
    button: 'Confirm for participant',
    week: 'Week view',
  },
} as const;

/** An action's accessible name: its label, then the slot and first name read out after it. */
function action(label: string): RegExp {
  return new RegExp(`^${label} \\d`);
}

for (const lang of ['nl', 'en'] as const) {
  test(`group admin reads the day and week agenda (${lang})`, async ({ page }) => {
    const day = fixture('demo_agenda');
    const labels = ROW_ACTIONS[lang];
    await loginAs(page, 'groupadmin', day);
    // The login redirect keeps the day but not the language; open the day in that language.
    await page.goto(`${day}&lang=${lang}`);
    const table = page.getByRole('table');
    for (const link of labels.links) {
      await expect(table.getByRole('link', { name: action(link) }).first()).toBeVisible();
    }
    await expect(table.getByRole('button', { name: action(labels.button) }).first()).toBeVisible();
    await expectNoAxeViolations(page);

    const week = page.getByRole('link', { name: labels.week });
    await tabTo(page, week);
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/agenda\/week/);
    await expect(page.getByRole('heading', { level: 2, name: lang === 'nl' ? /^Week van/ : /^Week of/ })).toBeVisible();
    await expectNoAxeViolations(page);
  });
}

test('an outsider sees no agenda of another organisation', async ({ page }) => {
  await loginAs(page, 'outsider', '/admin/');
  await expectForbidden(page, `${AGENDA}/week`);
});
