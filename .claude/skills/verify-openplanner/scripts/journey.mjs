#!/usr/bin/env node
/**
 * Drive OpenPlanner the way a participant and a staff member do, on the verification stack that
 * stack.sh started, and keep evidence of every step. Part of the verify-openplanner skill
 * (../SKILL.md); the recipe of each journey, its handles and its traps are in ../features/.
 *
 *   node .claude/skills/verify-openplanner/scripts/journey.mjs <journey>...
 *
 *   booking      book on /g/demo, get the email, confirm by its link       (participant-booking.md)
 *   agenda       the group admin finds that booking in the agenda; an
 *                admin of another organisation is refused                  (admin-agenda.md)
 *   manage       open the emailed link, move the appointment, cancel it    (manage-booking.md)
 *   admin-login  log in and out through Keycloak; a user without a role
 *                is refused                                                (admin-login.md)
 *   all          booking, agenda, manage, agenda, admin-login
 *
 * Journeys run in the order given, each in a fresh browser context; the first failure stops the
 * run, because later journeys build on earlier ones. Evidence goes to the run folder that
 * stack.sh recorded (.verify-openplanner/<run id>/): per journey a folder with a screenshot and
 * an ARIA snapshot of every step and a JSON note of every email it waited for, plus one line per
 * step in journey.jsonl and a table per invocation in summary.md. Booking-link tokens and test
 * passwords never reach the evidence; this run's made-up participant values are masked in
 * screenshots and replaced by placeholders in text.
 *
 * Playwright comes from e2e/ (cd e2e && npm ci). The browser is PLAYWRIGHT_CHROMIUM_CHANNEL,
 * by default chrome (an installed Google Chrome); set it empty for Playwright's own Chromium.
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  DEMO_GROUP, HAS_TOKEN, NL, SUBJECT, agendaRow, assertOurs, chooseDay, confirmLinkIn, h1, launchBrowser,
  linksIn, loadRun, login, mailFor, mailMessage, manageLinkIn, referenceOf, safeText, statusText, until, waitForMail,
  waitH1,
  waitStatus,
} from './stack-client.mjs';

function fail(message) {
  console.error(`journey.mjs: ${message}`);
  process.exit(2);
}

/** Shared state between journeys of one run (no personal data: reference, day, time, status). */
function loadState(run) {
  const file = join(run.dir, 'state.json');
  return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : {};
}

function saveState(run, state) {
  writeFileSync(join(run.dir, 'state.json'), `${JSON.stringify(state, null, 2)}\n`);
}

/** Text with booking-link tokens, test passwords and this run's participant values replaced. */
function redact(run, text) {
  let out = safeText(run, text);
  const { email, last, first } = run.participant;
  const values = [[email, '<email>'], [last, '<last_name>'], [first, '<first_name>']];
  for (const [value, label] of values) {
    if (value) out = out.split(value).join(label);
  }
  return out;
}

/** Writes the evidence of one journey into its own folder of the run. */
class Recorder {
  constructor(run, journey) {
    this.run = run;
    this.journey = journey;
    let folder = journey;
    for (let n = 2; existsSync(join(run.dir, folder)); n++) folder = `${journey}-${n}`;
    this.folder = folder;
    this.dir = join(run.dir, folder);
    mkdirSync(this.dir, { recursive: true });
    this.count = 0;
    this.steps = 0;
    this.lastStep = 'start';
    this.consoleErrors = [];
  }

  /** Mark console errors that came after `afterStep` and match `pattern` as expected, saying why. */
  expectConsole(afterStep, pattern, reason) {
    for (const entry of this.consoleErrors) {
      if (!entry.expected && entry.after === afterStep && pattern.test(entry.text)) entry.expected = reason;
    }
  }

  next(name) {
    this.count += 1;
    return `${String(this.count).padStart(2, '0')}-${name}`;
  }

  log(entry) {
    const line = { at: new Date().toISOString(), run: this.run.id, journey: this.folder, ...entry };
    appendFileSync(join(this.run.dir, 'journey.jsonl'), `${redact(this.run, JSON.stringify(line))}\n`);
  }

  /** Screenshot and ARIA snapshot of the page after a step, with what the step showed. */
  async step(page, name, observation) {
    const base = this.next(name);
    const { first, last, email } = this.run.participant;
    await page.screenshot({
      path: join(this.dir, `${base}.png`),
      fullPage: true,
      mask: [
        page.getByLabel(NL.firstName),
        page.getByLabel(NL.lastName),
        page.getByLabel(NL.email),
        page.getByLabel(NL.phone),
        page.getByText(first),
        page.getByText(last),
        page.getByText(email),
      ],
    });
    const aria = await page.locator('body').ariaSnapshot();
    writeFileSync(join(this.dir, `${base}.aria.yml`), `${redact(this.run, aria)}\n`);
    this.steps += 1;
    this.lastStep = name;
    this.log({ step: name, ok: true, observation, url: page.url(), artifacts: [`${base}.png`, `${base}.aria.yml`] });
    console.log(`PASS  ${this.folder}/${name}  ${redact(this.run, observation)}`);
  }

  /** A JSON note of an email: subject, links (tokens blanked) and attachments; never the body. */
  email(name, message, links) {
    const base = this.next(name);
    const note = {
      subject: message.Subject,
      to: (message.To ?? []).map((to) => to.Address),
      date: message.Date,
      links,
      attachments: (message.Attachments ?? []).map((a) => ({ name: a.FileName, type: a.ContentType, size: a.Size })),
    };
    writeFileSync(join(this.dir, `${base}.json`), `${redact(this.run, JSON.stringify(note, null, 2))}\n`);
    this.steps += 1;
    this.log({ step: name, ok: true, observation: `email "${message.Subject}"`, artifacts: [`${base}.json`] });
    console.log(`PASS  ${this.folder}/${name}  email "${message.Subject}" with ${links.length} link(s), ${note.attachments.length} attachment(s)`);
  }

  async failure(page, error) {
    const base = this.next('FAILED');
    let artifacts = [];
    try {
      await page.screenshot({ path: join(this.dir, `${base}.png`), fullPage: true });
      writeFileSync(join(this.dir, `${base}.aria.yml`), `${redact(this.run, await page.locator('body').ariaSnapshot())}\n`);
      artifacts = [`${base}.png`, `${base}.aria.yml`];
    } catch {
      // The page may be gone; the log line below still records the failure.
    }
    this.log({ step: 'FAILED', ok: false, error: error.message, url: page.url(), artifacts });
    console.log(`FAIL  ${this.folder}  ${redact(this.run, error.message)}`);
  }
}

/** participant-booking.md: book, get the confirmation request, confirm by the emailed link. */
async function booking({ page, rec, run }) {
  const { participant } = run;
  const seen = new Set((await mailFor(run)).map((m) => m.ID));

  await page.goto(`/g/${DEMO_GROUP}`);
  await waitH1(page, NL.groupName);
  await rec.step(page, 'group-page', `h1 "${NL.groupName}" with the month calendar`);

  const day = await chooseDay(page);
  const time = page.locator('#times .time-button').first();
  const start = await time.getAttribute('value');
  const label = (await time.innerText()).trim();
  await rec.step(page, 'day-times', `day ${day}: ${await page.locator('#times .time-button').count()} free times, first ${label}`);

  await time.click();
  await waitH1(page, NL.formTitle);
  await rec.step(page, 'form', `h1 "${NL.formTitle}" for ${label} on ${day}`);

  await page.getByLabel(NL.firstName).fill(participant.first);
  await page.getByLabel(NL.lastName).fill(participant.last);
  await page.getByLabel(NL.email).fill('not-an-email');
  await page.getByRole('button', { name: NL.submit }).click();
  await until('the error summary and an invalid email field', async () =>
    (await page.getByRole('alert').count()) > 0 &&
    (await page.getByLabel(NL.email).getAttribute('aria-invalid')) === 'true');
  await rec.step(page, 'invalid-email', 'error summary (role alert); the email field has aria-invalid="true"');

  await page.getByLabel(NL.email).fill(participant.email);
  await page.getByRole('button', { name: NL.submit }).click();
  await waitH1(page, NL.bookedTitle);
  if (!(await statusText(page)).includes(NL.bookedText)) throw new Error(`no status "${NL.bookedText}"`);
  await rec.step(page, 'check-your-email', `h1 "${NL.bookedTitle}", status "${NL.bookedText}"`);

  const request = await waitForMail(run, seen, SUBJECT.confirmRequest);
  const requestLinks = linksIn(run, request);
  const confirmUrl = confirmLinkIn(run, request);
  rec.email('confirm-request-email', request, requestLinks.shown);

  await page.goto(confirmUrl);
  await until(`h1 "${NL.confirm}" on /b/m/<reference>/confirm`, async () =>
    /^\/b\/m\/[\w-]+\/confirm$/.test(new URL(page.url()).pathname) && (await h1(page)) === NL.confirm);
  // ADR 0093: the token is traded for a cookie; the address that stays holds none.
  if (HAS_TOKEN.test(new URL(page.url()).pathname)) throw new Error('the confirm page address still holds the token');
  const reference = referenceOf(page);
  await rec.step(page, 'confirm-page', `the emailed link led to /b/m/${reference}/confirm (no token in the address)`);

  await page.getByRole('button', { name: NL.confirm }).click();
  await waitStatus(page, NL.confirmed);
  await rec.step(page, 'confirmed', `status "${NL.confirmed}" on /b/m/${reference}`);

  const confirmation = await waitForMail(run, seen, SUBJECT.confirmation);
  rec.email('confirmation-email', confirmation, linksIn(run, confirmation).shown);
  saveState(run, { booking: { reference, day, start, time: label, status: 'confirmed' } });
  rec.expectConsole('form', /status of 422/, 'the form with an invalid email answers 422 Unprocessable Entity');
}

/** manage-booking.md: open the emailed management link, move the appointment, cancel it. */
async function manage({ page, rec, run }) {
  const state = loadState(run);
  if (state.booking?.status !== 'confirmed') {
    throw new Error('precondition: a confirmed booking of this run (run the booking journey first)');
  }
  const messages = await mailFor(run);
  const seen = new Set(messages.map((m) => m.ID));
  const latest = messages.find((m) => SUBJECT.confirmation.test(m.Subject));
  if (!latest) throw new Error('precondition: the confirmation email of this run');
  const manageUrl = manageLinkIn(run, await mailMessage(run, latest.ID));

  await page.goto(manageUrl);
  await until(`h1 "${NL.manageTitle}" on /b/m/${state.booking.reference}`, async () =>
    new URL(page.url()).pathname === `/b/m/${state.booking.reference}` && (await h1(page)) === NL.manageTitle);
  await rec.step(page, 'manage-page', `the emailed link led to /b/m/${state.booking.reference}, h1 "${NL.manageTitle}"`);

  await page.getByRole('link', { name: NL.move }).click();
  await waitH1(page, NL.move);
  await rec.step(page, 'reschedule-page', `h1 "${NL.move}" with the slot picker`);

  const day = await chooseDay(page);
  const times = page.locator('#times .time-button');
  let index = 0;
  while (index < (await times.count()) && (await times.nth(index).getAttribute('value')) === state.booking.start) index++;
  if (index >= (await times.count())) throw new Error(`no other free time on ${day}`);
  const start = await times.nth(index).getAttribute('value');
  const label = (await times.nth(index).innerText()).trim();
  await times.nth(index).click();
  await waitH1(page, NL.moveConfirmTitle);
  await rec.step(page, 'reschedule-confirm', `h1 "${NL.moveConfirmTitle}": from ${state.booking.time} on ${state.booking.day} to ${label} on ${day}`);

  await page.getByRole('button', { name: NL.moveSubmit }).click();
  await waitStatus(page, NL.moved);
  // A move makes a new booking with a reference of its own; the address shows it.
  const reference = referenceOf(page);
  await rec.step(page, 'moved', `status "${NL.moved}"; the booking is now /b/m/${reference}`);
  const change = await waitForMail(run, seen, SUBJECT.change);
  rec.email('change-email', change, linksIn(run, change).shown);

  await page.getByRole('link', { name: NL.cancel }).click();
  await waitH1(page, NL.cancel);
  await rec.step(page, 'cancel-page', `h1 "${NL.cancel}"`);

  await page.getByRole('button', { name: NL.cancelSubmit }).click();
  await waitStatus(page, NL.cancelledNotice);
  await rec.step(page, 'cancelled', `status "${NL.cancelledNotice}"`);
  const cancellation = await waitForMail(run, seen, SUBJECT.cancellation);
  rec.email('cancellation-email', cancellation, linksIn(run, cancellation, false).shown);

  saveState(run, { booking: { reference, day, start, time: label, status: 'cancelled' } });
}

/** admin-agenda.md: the group admin finds this run's booking; another organisation's admin is refused. */
async function agenda({ page, rec, run, browser }) {
  const { booking: current } = loadState(run);
  if (!current) throw new Error('precondition: a booking of this run (run the booking journey first)');
  const path = `/admin/groups/${DEMO_GROUP}/agenda?day=${current.day}`;

  await login(page, run, 'groupadmin', path);
  // The login keeps only the path; open the day again.
  await page.goto(path);
  await until('the agenda day heading', () => page.locator('#agenda-heading').isVisible());
  // The row header reads "<start>–<end>"; match the start, since a moved booking's old row may end
  // at the new start.
  const row = await agendaRow(page, run.participant.first, current.time);
  await until(`one agenda row at ${current.time} for this run's participant`, async () => (await row.count()) === 1);
  const cancelled = (await row.getByText(NL.agendaCancelled, { exact: true }).count()) > 0;
  if (cancelled !== (current.status === 'cancelled')) {
    throw new Error(`the row is ${cancelled ? '' : 'not '}marked "${NL.agendaCancelled}", but the booking is ${current.status}`);
  }
  await rec.step(page, `agenda-${current.status}`,
    `groupadmin sees the booking at ${current.time} on ${current.day}${cancelled ? `, marked "${NL.agendaCancelled}"` : ', not cancelled'}`);

  const other = await browser.newContext({ baseURL: run.web, locale: 'nl-NL' });
  try {
    const outsider = await other.newPage();
    await login(outsider, run, 'outsider');
    const response = await outsider.goto(path);
    await waitH1(outsider, NL.forbidden);
    if (response?.status() !== 403) throw new Error(`the outsider got status ${response?.status()}, not 403`);
    await rec.step(outsider, 'outsider-refused', `outsider (organisation andere) gets 403 "${NL.forbidden}" on the demo agenda`);
  } finally {
    await other.close();
  }
}

/** admin-login.md: sign in and out through Keycloak; a user without a role is refused. */
async function adminLogin({ page, rec, run }) {
  await page.goto('/admin/');
  await until('the Keycloak sign-in page', () => page.url().startsWith(`${run.keycloak}/realms/openplanner/`));
  await rec.step(page, 'keycloak-sign-in', `/admin/ sends an anonymous visitor to ${run.keycloak}/realms/openplanner`);

  await login(page, run, 'groupadmin');
  await waitH1(page, NL.dashboard);
  const groups = page.getByRole('list', { name: NL.yourGroups }).getByRole('listitem');
  if ((await groups.count()) !== 1 || !(await groups.first().innerText()).includes(NL.groupName)) {
    throw new Error(`"${NL.yourGroups}" does not list exactly "${NL.groupName}"`);
  }
  await rec.step(page, 'dashboard', `h1 "${NL.dashboard}"; "${NL.yourGroups}" lists only "${NL.groupName}"`);

  await page.locator('details.admin-profile > summary').click();
  await page.getByRole('button', { name: NL.logout }).click();
  await until('the Keycloak logout page', () => page.url().startsWith(`${run.keycloak}/`) && page.url().includes('/logout'));
  await page.locator('#kc-logout').click();
  await waitH1(page, NL.loggedOut);
  await rec.step(page, 'logged-out', `h1 "${NL.loggedOut}" after the Keycloak logout`);

  await page.goto('/admin/');
  await until('the Keycloak sign-in page again', () => page.url().startsWith(`${run.keycloak}/`));
  await rec.step(page, 'sign-in-again', 'after logging out, /admin/ asks for a sign-in again');

  await login(page, run, 'norole');
  await waitH1(page, NL.forbidden);
  const links = await page.getByRole('navigation', { name: NL.adminMenu }).getByRole('link').count();
  if (!(await page.getByText(NL.noRole).isVisible()) || links !== 0) {
    throw new Error(`norole sees ${links} menu links or no "${NL.noRole}"`);
  }
  await rec.step(page, 'no-role-refused', `norole: h1 "${NL.forbidden}", "${NL.noRole}", no menu links`);
  rec.expectConsole('sign-in-again', /status of 403/, 'the portal answers 403 to a user without a role');
}

const JOURNEYS = { booking, agenda, manage, 'admin-login': adminLogin };
const ALL = ['booking', 'agenda', 'manage', 'agenda', 'admin-login'];

async function main() {
  const asked = process.argv.slice(2);
  if (!asked.length || asked.some((a) => a.startsWith('-'))) {
    console.log(readFileSync(fileURLToPath(import.meta.url), 'utf8').split('\n').slice(2, 15).join('\n').replace(/^ \* ?/gm, ''));
    process.exit(asked.length ? 0 : 2);
  }
  const names = asked.flatMap((name) => (name === 'all' ? ALL : [name]));
  const unknown = names.filter((name) => !JOURNEYS[name]);
  if (unknown.length) fail(`unknown journey ${unknown.join(', ')} (${Object.keys(JOURNEYS).join(', ')}, all)`);

  let run;
  let browser;
  try {
    run = loadRun();
    await assertOurs(run);
    browser = await launchBrowser();
  } catch (error) {
    fail(error.message);
  }
  const results = [];
  try {
    for (const name of names) {
      const context = await browser.newContext({ baseURL: run.web, locale: 'nl-NL', viewport: { width: 1280, height: 900 } });
      const page = await context.newPage();
      const rec = new Recorder(run, name);
      const noteError = (text) => rec.consoleErrors.push({ text, after: rec.lastStep });
      page.on('console', (message) => message.type() === 'error' && noteError(message.text()));
      page.on('pageerror', (error) => noteError(error.message));
      let ok = true;
      try {
        await JOURNEYS[name]({ page, rec, run, browser });
      } catch (error) {
        ok = false;
        await rec.failure(page, error);
      } finally {
        await context.close();
      }
      const unexpected = rec.consoleErrors.filter((entry) => !entry.expected);
      if (rec.consoleErrors.length) rec.log({ step: 'console', ok: !unexpected.length, console_errors: rec.consoleErrors });
      for (const entry of unexpected) console.log(`WARN  ${rec.folder}  console error after ${entry.after}: ${redact(run, entry.text)}`);
      results.push({ name: rec.folder, ok, steps: rec.steps, unexpected: unexpected.length, expected: rec.consoleErrors.length - unexpected.length });
      if (!ok) break;
    }
  } finally {
    await browser.close();
  }

  const skipped = names.slice(results.length);
  const lines = [
    `## journey.mjs ${asked.join(' ')} (${new Date().toISOString()})`,
    '',
    '| Journey | Result | Steps with evidence | Console errors (unexpected / expected) | Folder |',
    '|---|---|---|---|---|',
    ...results.map((r) => `| ${r.name} | ${r.ok ? 'PASS' : 'FAIL'} | ${r.steps} | ${r.unexpected} / ${r.expected} | ${r.name}/ |`),
    ...skipped.map((name) => `| ${name} | not run (an earlier journey failed) | 0 | – | – |`),
    '',
  ];
  appendFileSync(join(run.dir, 'summary.md'), `${lines.join('\n')}\n`);
  console.log(`\n${lines.join('\n')}\nEvidence: ${run.dir}`);
  process.exit(results.every((r) => r.ok) && !skipped.length ? 0 : 1);
}

await main();
