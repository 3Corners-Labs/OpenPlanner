/**
 * Direct video call (ADR 0019) end to end, without Webex: the org admin creates a group that is
 * open around the clock and switches direct calls on; a citizen starts a call and lands on the
 * waiting page; the org admin sees it in the agenda. axe (WCAG 2.2 AA) on every page.
 *
 * Without a Webex Service App the meeting and work item are skipped, so the waiting page keeps
 * "looking for an agent" without a join button: the citizen-facing flow is what is tested here.
 * Each browser creates its own group, so the parallel browsers do not collide.
 */
import { expect, type Page, test } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { loginAs, requireKeycloak } from '../keycloak';

requireKeycloak();

const WEEKDAYS = ['maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag'];

async function addAllDayHours(page: Page): Promise<void> {
  for (const day of WEEKDAYS) {
    await page.getByLabel('Dag', { exact: true }).selectOption({ label: day });
    await page.getByLabel('Van', { exact: true }).fill('00:00');
    await page.getByLabel('Tot', { exact: true }).fill('23:55');
    await page.getByLabel('Gelijktijdige gesprekken').fill('1');
    await page.getByRole('button', { name: 'Openingstijd toevoegen' }).press('Enter');
    await expect(page.getByRole('status')).toHaveText('Toegevoegd.');
  }
}

test('a citizen starts a call right away and the admin sees it', async ({ page, browser }, testInfo) => {
  const slug = `e2e-direct-${testInfo.project.name}-${Date.now().toString(36)}`;
  const tabs = page.getByRole('navigation', { name: 'Onderdelen van de groep' });

  // Admin: group open around the clock, direct calls on.
  await loginAs(page, 'orgadmin', '/admin/groups/new');
  await page.getByLabel('Naam', { exact: true }).fill(`Direct ${testInfo.project.name}`);
  await page.getByLabel('Webadres', { exact: true }).fill(slug);
  await page.getByRole('button', { name: 'Groep aanmaken' }).press('Enter');
  await expect(page.getByRole('status')).toContainText('De groep is aangemaakt');
  await tabs.getByRole('link', { name: 'Openingstijden' }).click();
  await addAllDayHours(page);
  await tabs.getByRole('link', { name: 'Instellingen' }).click();
  await page.getByLabel('Direct gesprek starten').selectOption({ label: 'Ja' });
  await page.getByRole('button', { name: 'Opslaan' }).press('Enter');
  await expect(page.getByRole('status')).toHaveText('De wijzigingen zijn opgeslagen.');
  await expectNoAxeViolations(page);

  // Citizen (no admin session): group page offers the direct call.
  const citizenContext = await browser.newContext({ locale: 'nl-NL' });
  const citizen = await citizenContext.newPage();
  await citizen.goto(`/g/${slug}`);
  await expect(citizen.getByRole('heading', { name: 'Liever meteen?' })).toBeVisible();
  await expectNoAxeViolations(citizen);
  await citizen.getByRole('link', { name: 'Nu een gesprek starten' }).click();
  await expect(citizen.getByRole('heading', { level: 1 })).toHaveText('Nu een videogesprek starten');
  await expectNoAxeViolations(citizen);
  await citizen.getByLabel('Naam').fill('Ada Lovelace');
  await citizen.getByLabel('E-mailadres').fill('ada@example.org');
  await citizen.getByRole('button', { name: 'Gesprek starten' }).press('Enter');
  await expect(citizen).toHaveURL(/\/b\/[A-Za-z0-9_-]+\/wait$/);
  await expect(citizen.getByRole('status')).toContainText('We zoeken een medewerker');
  await expectNoAxeViolations(citizen);
  await citizenContext.close();

  // Admin: the direct call is in today's agenda.
  await page.goto(`/admin/groups/${slug}/agenda`);
  const row = page.getByRole('row').filter({ hasText: 'Ada' });
  await expect(row).toContainText('Direct');
  await expect(row).toContainText('Nog geen taak');
  await expect(page.getByText('Lovelace')).toHaveCount(0);
  await expectNoAxeViolations(page);
  await row.getByRole('link', { name: /Details/ }).click();
  await expect(page.getByRole('heading', { name: 'Verloop' })).toBeVisible();
  await expectNoAxeViolations(page);
});
