/**
 * The participant's journey around the video call, without Webex (ADR 0071, 0075, 0076): the
 * trial call ahead of the appointment, the join page and link on the day, and what the admin
 * sees of it on the booking page and in the agenda. The simulator (SIMULATOR_ENABLED, runbook
 * section 10) stands in for Webex: it gives the bookings stand-in meetings and plays the events
 * Webex and the contact center would send, through the real webhook path. A link to a meeting
 * is followed as a request and stopped at the redirect: the stand-in meeting lives on
 * `.invalid`, which never resolves. axe (WCAG 2.2 AA) on every page.
 *
 * Needs the seeded journey (python -m app.cli seed-closures > e2e/fixtures.json): per browser
 * a booking tomorrow or later (the trial call) and one that starts in ten minutes (the call).
 */
import { expect, type Locator, type Page, test } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { loginAs, requireKeycloak } from '../keycloak';
import { journey } from '../pages';

requireKeycloak();

/** Where both links lead: the stand-in meeting, opened in Webex's web app (ADR 0075). */
const SIMULATED_MEETING = 'https://simulated-meeting.invalid/join?launchApp=true';
const PREPARE = 'Nep-meeting en -taak aanmaken';

/** Press the simulator button `name` on the admin booking page and wait for its confirmation. */
async function play(page: Page, name: string, done: RegExp): Promise<void> {
  await page.getByRole('button', { name, exact: true }).click();
  await expect(page.getByRole('status')).toHaveText(done);
}

/** The timeline entries that say `label`. */
function timeline(page: Page, label: string): Locator {
  return page.locator('.timeline li').filter({ hasText: label });
}

/** The value of the fact `term` (Deelnemers, Medewerker binnen) on the admin booking page. */
function fact(page: Page, term: string): Locator {
  return page.locator('dl.facts div').filter({ has: page.locator('dt', { hasText: term }) }).locator('dd');
}

/** Follow `link` as the browser would and return where it leads; stops at the redirect. */
async function destinationOf(page: Page, link: Locator): Promise<string | undefined> {
  const response = await page.request.get((await link.getAttribute('href')) ?? '', { maxRedirects: 0 });
  expect(response.status()).toBe(303);
  return response.headers()['location'];
}

test('the participant tries the connection, joins the call, and the admin follows it', async ({ page, browser }, testInfo) => {
  const paths = journey(testInfo.project.name);
  const citizenContext = await browser.newContext({ locale: 'nl-NL' });
  const citizen = await citizenContext.newPage();

  // No Webex here, so the participant cannot open a trial call yet (opening one is covered by
  // the API tests with a fake Webex): the page says so and offers no way in.
  await citizen.goto(paths.trial_page);
  await expect(citizen.getByRole('heading', { level: 1 })).toHaveText('Proefgesprek');
  await expect(citizen.getByText('Voor deze afspraak is geen proefgesprek beschikbaar.')).toBeVisible();
  await expect(citizen.getByRole('link', { name: 'Naar het proefgesprek' })).toHaveCount(0);
  await expectNoAxeViolations(citizen);

  // The admin gives the booking a stand-in meeting, task and open trial call.
  await loginAs(page, 'orgadmin', paths.trial_booking);
  await expect(page.getByRole('heading', { name: 'Simulator (alleen in test)' })).toBeVisible();
  await expectNoAxeViolations(page);
  await play(page, PREPARE, /nep-proefgesprek/);
  await expect(timeline(page, 'Proefgesprek geopend door de burger')).toHaveCount(1);

  // The participant now has the way in, straight into Webex's web app (ADR 0075).
  await citizen.reload();
  await expect(citizen.getByRole('status')).toContainText('Uw proefgesprek staat open tot');
  const wayIn = citizen.getByRole('link', { name: 'Naar het proefgesprek' });
  await expectNoAxeViolations(citizen);
  expect(await destinationOf(citizen, wayIn)).toBe(SIMULATED_MEETING);

  // Webex reports the participant in and out of the trial meeting. The timeline shows the
  // journey; the appointment's own call status stays untouched (ADR 0076).
  await play(page, 'Deelnemer komt het proefgesprek binnen', /afgeleverd/);
  await play(page, 'Deelnemer verlaat het proefgesprek', /afgeleverd/);
  await expect(timeline(page, 'Burger ging het proefgesprek in')).toContainText('browser');
  await expect(timeline(page, 'Deelnemer kwam het proefgesprek binnen')).toHaveCount(1);
  await expect(timeline(page, 'Deelnemer verliet het proefgesprek')).toHaveCount(1);
  await expect(fact(page, 'Deelnemers')).toHaveText('0');
  await expect(fact(page, 'Medewerker binnen')).toHaveText('Nee');
  await expectNoAxeViolations(page);

  // The call: the join page is open, but the meeting is not there yet.
  await citizen.goto(paths.call_page);
  await expect(citizen.getByRole('heading', { level: 1 })).toHaveText('Uw videogesprek');
  await expect(citizen.getByRole('status')).toContainText('wordt klaargezet');
  await expectNoAxeViolations(citizen);

  await page.goto(paths.call_booking);
  await play(page, PREPARE, /nep-meeting/);

  await citizen.reload();
  const joinLink = citizen.getByRole('link', { name: 'Deelnemen aan het videogesprek' });
  await expectNoAxeViolations(citizen);
  expect(await destinationOf(citizen, joinLink)).toBe(SIMULATED_MEETING);

  // The call as the contact center and Webex would report it.
  for (const name of ['Agent neemt taak aan', 'Agent komt binnen', 'Burger komt binnen', 'Meeting beëindigd']) {
    await play(page, name, /afgeleverd/);
  }
  await expect(timeline(page, 'Deelnemer klikte op deelnemen')).toContainText('browser');
  for (const label of ['Medewerker nam de taak aan', 'Medewerker kwam binnen', 'Deelnemer kwam binnen', 'Meeting beëindigd']) {
    await expect(timeline(page, label)).toHaveCount(1);
  }
  await expect(fact(page, 'Medewerker binnen')).toHaveText('Ja');
  await expectNoAxeViolations(page);

  // Today's agenda shows the call with the agent in.
  await page.goto('/admin/groups/demo-reis/agenda');
  const row = page.getByRole('row').filter({ has: page.locator(`a[href="${paths.call_booking}"]`) });
  await expect(row).toContainText('Ja');
  await expectNoAxeViolations(page);

  await citizenContext.close();
});
