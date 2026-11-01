/**
 * The recorded walkthrough of the real interface (ADR 0087 step 3): one continuous story in
 * one recorded browser context, from a participant booking a call to an agent picking it up.
 *
 * Only the story context records. Whatever stands in for Webex and the contact center (the
 * simulator panel, ADR 0076) is pressed in a second context that does not record, so the video
 * shows the product, never test scaffolding. Every chapter's start is marked against the start
 * of the video; captions.mjs turns the marks and chapters.json into captions in nl and en.
 * A still of the main chapters (tour-<chapter>.jpg) feeds the gallery of screens on the site.
 *
 * Needs the demo seed and the seeded journey (seed-demo, seed-closures > e2e/fixtures.json),
 * the simulator (SIMULATOR_ENABLED) and Keycloak, as in the accessibility job of ci.yml.
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';

import { expect, type Locator, type Page, test } from '@playwright/test';

import { loginAs, requireKeycloak } from '../keycloak';
import { fixture, journey } from '../pages';

requireKeycloak();

const OUT = new URL('../tour-output/', import.meta.url).pathname;
const SIZE = { width: 1280, height: 720 };
/** How long a screen stays still so a viewer can read it; TOUR_PACE_MS makes it slower or faster. */
const PACE = Number(process.env.TOUR_PACE_MS ?? 1800);
const PREPARE = 'Nep-meeting en -taak aanmaken';
const JOURNEY_GROUP = '/admin/groups/demo-reis';
/** A fresh database per recording in CI; a local rerun needs another address (ADR 0066 limit). */
const EMAIL = process.env.TOUR_EMAIL ?? 'sam.jansen@example.org';

const { chapters } = JSON.parse(readFileSync(new URL('./chapters.json', import.meta.url), 'utf8')) as {
  chapters: { key: string }[];
};

/** Press the simulator button `name` on an admin booking page (unrecorded) and wait for it. */
async function simulate(page: Page, name: string, done: RegExp): Promise<void> {
  await page.getByRole('button', { name, exact: true }).click();
  await expect(page.getByRole('status')).toHaveText(done);
}

/**
 * Bring `locator` into view the way a person scrolls, then rest. Parts of the booking page are
 * re-rendered after a click (the times of a day), so a node that was replaced is looked up again.
 */
async function show(page: Page, locator: Locator, rest = PACE): Promise<void> {
  for (let attempt = 0; ; attempt++) {
    await expect(locator.first()).toBeVisible();
    try {
      await locator.first().evaluate((el) => el.scrollIntoView({ behavior: 'smooth', block: 'center' }));
      break;
    } catch (error) {
      if (attempt >= 2) throw error;
    }
  }
  await page.waitForTimeout(rest);
}

test('the walkthrough, recorded', async ({ browser }) => {
  mkdirSync(OUT, { recursive: true });
  const paths = journey('chromium');

  // The stand-ins for Webex and the contact center, pressed out of the picture.
  const backstage = await (await browser.newContext({ locale: 'nl-NL' })).newPage();
  await loginAs(backstage, 'orgadmin', paths.trial_booking);
  await simulate(backstage, PREPARE, /nep-proefgesprek/);
  await backstage.goto(paths.call_booking);
  await simulate(backstage, PREPARE, /nep-meeting/);

  const story = await browser.newContext({
    locale: 'nl-NL',
    viewport: SIZE,
    bypassCSP: true,
    recordVideo: { dir: OUT, size: SIZE },
  });
  // The portal marks a test environment with a bar ("Ontwikkelomgeving", "Simulator staat
  // aan"); production has no such bar, so the recording hides it to show what production shows.
  // The portal's CSP forbids inline styles, hence bypassCSP on this one context only.
  await story.addInitScript(() => {
    const hide = () => {
      if (document.getElementById('tour-hide-env')) return;
      const style = document.createElement('style');
      style.id = 'tour-hide-env';
      style.textContent = '.env-bar { display: none !important; }';
      (document.head ?? document.documentElement).append(style);
    };
    if (document.documentElement) hide();
    document.addEventListener('DOMContentLoaded', hide);
  });
  const page = await story.newPage();
  const start = Date.now();
  const marks: { key: string; at: number }[] = [];
  const mark = (key: string) => {
    if (!chapters.some((chapter) => chapter.key === key)) throw new Error(`chapter ${key} not in chapters.json`);
    marks.push({ key, at: (Date.now() - start) / 1000 });
  };
  /** A still of the screen as it is now, for the gallery on the site (tour-<chapter>.jpg). */
  const shot = (key: string) => page.screenshot({ path: `${OUT}tour-${key}.jpg`, type: 'jpeg', quality: 80 });

  // The participant: pick a day and a time.
  await page.goto('/g/demo');
  await expect(page.locator('a.calendar__day').first()).toBeVisible();
  mark('book');
  await page.waitForTimeout(PACE);
  await page.locator('a.calendar__day').last().click();
  await show(page, page.locator('#times .time-button'));
  await page.screenshot({ path: `${OUT}tour.jpg`, type: 'jpeg', quality: 85 });
  await shot('book');
  await page.locator('#times .time-button').first().click();

  // The form.
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Uw gegevens');
  mark('form');
  await page.getByLabel('Voornaam').pressSequentially('Sam', { delay: 80 });
  await page.getByLabel('Achternaam').pressSequentially('Jansen', { delay: 80 });
  await page.getByLabel('E-mailadres').pressSequentially(EMAIL, { delay: 40 });
  await page.getByRole('radio', { name: 'Toeslagen' }).check();
  await show(page, page.getByRole('button', { name: 'Afspraak maken' }), PACE / 2);
  await shot('form');
  await page.getByRole('button', { name: 'Afspraak maken' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Bevestig uw afspraak');
  mark('check_email');
  await page.waitForTimeout(PACE);

  // The emailed link (a seeded booking awaiting confirmation; the mail itself is not shown).
  await page.goto(fixture('pending_flow_chromium'));
  await page.getByRole('button', { name: 'Afspraak bevestigen' }).click();
  await expect(page.getByRole('status')).toContainText('Uw afspraak is bevestigd');
  mark('confirmed');
  await shot('confirmed');
  await page.waitForTimeout(PACE);

  await page.getByRole('link', { name: 'Afspraak verplaatsen' }).click();
  mark('move');
  await show(page, page.locator('#times .time-button'));
  await page.locator('#times .time-button').first().click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Nieuw moment bevestigen');
  await page.waitForTimeout(PACE / 2);
  await page.getByRole('button', { name: 'Verplaatsen' }).click();
  await expect(page.getByRole('status')).toContainText('Uw afspraak is verplaatst');
  await page.waitForTimeout(PACE);

  // The trial call and, on the day, the call itself.
  await page.goto(paths.trial_page);
  await expect(page.getByRole('status')).toContainText('Uw proefgesprek staat open tot');
  mark('trial');
  await shot('trial');
  await page.waitForTimeout(PACE * 1.5);
  await page.goto(paths.call_page);
  await expect(page.getByRole('link', { name: 'Deelnemen aan het videogesprek' })).toBeVisible();
  mark('join');
  await shot('join');
  await page.waitForTimeout(PACE * 1.5);

  // The team: sign in through the identity provider.
  mark('login');
  await loginAs(page, 'orgadmin', '/admin/groups');
  mark('groups');
  await shot('groups');
  await page.waitForTimeout(PACE * 1.5);

  await page.goto('/admin/groups/demo/staffing');
  mark('staffing');
  await shot('staffing');
  await page.waitForTimeout(PACE);
  await page.mouse.wheel(0, 400);
  await page.waitForTimeout(PACE);

  // The journey group works from the own queue from now on (ADR 0043).
  await page.goto(`${JOURNEY_GROUP}/settings`);
  const dispatch = page.getByLabel(/^Hoe afspraken bij/);
  mark('dispatch');
  await show(page, dispatch);
  await dispatch.selectOption({ label: 'Eigen wachtrij van OpenPlanner' });
  await page.waitForTimeout(PACE / 2);
  await dispatch.locator('xpath=ancestor::form').getByRole('button', { name: 'Opslaan' }).click();
  await page.waitForTimeout(PACE);

  // The participant arrives in the lobby (played backstage), the agenda shows it live.
  await backstage.goto(paths.call_booking);
  await simulate(backstage, 'Deelnemer komt binnen', /afgeleverd/);
  await page.goto(`${JOURNEY_GROUP}/agenda`);
  mark('agenda');
  await shot('agenda');
  await page.waitForTimeout(PACE * 2);

  await page.goto(`${JOURNEY_GROUP}/queue`);
  mark('queue');
  await page.waitForTimeout(PACE);
  await page.getByRole('button', { name: 'Volgende oppakken' }).click();
  await expect(page.locator('#claimed-heading')).toContainText('opgepakt');
  mark('claimed');
  await shot('claimed');
  await page.waitForTimeout(PACE * 2);

  const end = (Date.now() - start) / 1000;
  const video = page.video();
  await story.close();
  await video?.saveAs(`${OUT}tour.webm`);
  await video?.delete();
  writeFileSync(`${OUT}tour.marks.json`, `${JSON.stringify({ marks, end }, null, 2)}\n`);
  expect(marks.map((m) => m.key)).toEqual(chapters.map((c) => c.key));
});
