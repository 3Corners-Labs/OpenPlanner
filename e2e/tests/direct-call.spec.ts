/**
 * Direct video call (ADR 0019) end to end, without Webex: the org admin creates a group that is
 * open around the clock with blocks that offer direct calls (ADR 0072); a participant starts a call
 * and lands on the waiting page; the org admin sees it in the agenda. axe (WCAG 2.2 AA) on every
 * page.
 *
 * Without a Webex Service App the meeting and work item are skipped, so the waiting page keeps
 * "looking for an agent" without a join button: the participant-facing flow is what is tested here.
 * Each browser creates its own group, so the parallel browsers do not collide.
 *
 * A staffing block cannot end at 24:00, so "around the clock" leaves 23:55–00:00 closed. The
 * group therefore gets a time zone in which it is not near midnight while the test runs.
 */
import { expect, type Page, test } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { loginAs, requireKeycloak } from '../keycloak';

requireKeycloak();

const WEEKDAYS = ['maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag'];

/** Candidate zones 8 hours apart: when one is near midnight, the other is not. */
const ZONES = ['Europe/Amsterdam', 'Asia/Tokyo'];

/** Return the first zone whose local hour is between 01:00 and 22:00 right now. */
function zoneAwayFromMidnight(): string {
  const hourIn = (timeZone: string) =>
    Number(new Intl.DateTimeFormat('en-GB', { hour: '2-digit', hourCycle: 'h23', timeZone }).format(new Date()));
  return ZONES.find((zone) => hourIn(zone) >= 1 && hourIn(zone) < 22) ?? ZONES[0];
}

async function addAllDayHours(page: Page): Promise<void> {
  for (const [added, day] of WEEKDAYS.entries()) {
    await page.getByLabel('Dag', { exact: true }).selectOption({ label: day });
    await page.getByLabel('Van uu:mm', { exact: true }).fill('00:00');
    await page.getByLabel('Tot uu:mm', { exact: true }).fill('23:55');
    await page.getByLabel('Aantal medewerkers').fill('1');
    await page.getByLabel('Aanbod', { exact: true }).selectOption({ label: 'Afspraken en direct gesprek' });
    await page.getByRole('button', { name: 'Tijdblok toevoegen' }).press('Enter');
    // Every add reloads the page and shows the same "Toegevoegd." again. Wait for the new row
    // first: that is the one signal the reloaded page has, whereas the previous page's banner
    // already matches the text and would let the next add start on the page that is leaving
    // (WebKit commits the form navigation later than the other browsers).
    await expect(page.locator('.window__offer')).toHaveCount(added + 1);
    await expect(page.getByRole('status')).toHaveText('Toegevoegd.');
  }
}

test('a participant starts a call right away and the admin sees it', async ({ page, browser }, testInfo) => {
  // A long journey: Keycloak login, seven staffing round trips, two browser contexts and axe on
  // each page. It takes about 17 s alone, so the default 30 s leaves no room on a busy runner.
  test.slow();
  const slug = `e2e-direct-${testInfo.project.name}-${testInfo.repeatEachIndex}-${Date.now().toString(36)}`;
  const tabs = page.getByRole('navigation', { name: 'Onderdelen van de groep' });

  // Admin: group open around the clock (but 23:55–00:00), every block offers direct calls.
  await loginAs(page, 'orgadmin', '/admin/groups/new');
  await page.getByLabel('Naam', { exact: true }).fill(`Direct ${testInfo.project.name}`);
  await page.getByLabel('Webadres', { exact: true }).fill(slug);
  await page.getByLabel('Tijdzone', { exact: true }).selectOption(zoneAwayFromMidnight());
  await page.getByRole('button', { name: 'Groep aanmaken' }).press('Enter');
  await expect(page.getByRole('status')).toContainText('De groep is aangemaakt');
  await tabs.getByRole('link', { name: 'Bezetting', exact: true }).click();
  await addAllDayHours(page);
  await expect(page.locator('.window__offer', { hasText: 'Afspraken en direct gesprek' })).toHaveCount(WEEKDAYS.length);
  await expectNoAxeViolations(page);

  // Participant (no admin session): group page offers the direct call.
  const participantContext = await browser.newContext({ locale: 'nl-NL' });
  const participant = await participantContext.newPage();
  await participant.goto(`/g/${slug}`);
  await expect(participant.getByRole('heading', { name: 'Liever meteen?' })).toBeVisible();
  await expectNoAxeViolations(participant);
  await participant.getByRole('link', { name: 'Nu een gesprek starten' }).click();
  await expect(participant.getByRole('heading', { level: 1 })).toHaveText('Nu een videogesprek starten');
  await expectNoAxeViolations(participant);
  await participant.getByLabel('Voornaam').fill('Ada');
  await participant.getByLabel('Achternaam').fill('Lovelace');
  await participant.getByLabel('E-mailadres').fill('ada@example.org');
  await participant.getByRole('button', { name: 'Gesprek starten' }).press('Enter');
  // ADR 0093: the call sets the participant cookie itself, so the waiting room sits under the
  // reference, and the poll URL of the live region is on it too (no credential in any address).
  await expect(participant).toHaveURL(/\/b\/m\/[A-Za-z0-9_-]{22}\/wait$/);
  await expect(participant.locator('[data-poll]')).toHaveAttribute('data-poll', /^\/b\/m\/[A-Za-z0-9_-]{22}\/wait\/status\?seen=/);
  await expect(participant.getByRole('status')).toContainText('We zoeken een medewerker');
  await expectNoAxeViolations(participant);
  await participantContext.close();

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
