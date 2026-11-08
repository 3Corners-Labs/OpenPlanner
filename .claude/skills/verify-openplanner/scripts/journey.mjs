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
 *   contact      the booking page shows the contact details on request
 *                only, and the audit log records it                        (booking-contact.md)
 *   manage       open the emailed link, move the appointment, cancel it    (manage-booking.md)
 *   admin-login  log in and out through Keycloak; a user without a role
 *                is refused                                                (admin-login.md)
 *   admin-layout admin pages at 1280 × 800: nothing runs past its box,
 *                no table is cut off, the texts read right                 (admin-layout.md)
 *   all          booking, agenda, contact, manage, agenda, admin-login,
 *                admin-layout
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
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { layoutProblems } from '../../../../e2e/layout.mjs';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..');
const EVIDENCE_ROOT = process.env.VERIFY_EVIDENCE_ROOT || join(REPO, '.verify-openplanner');

/** Dutch interface texts the journeys wait for, by catalogue key (app/i18n/locales/nl.po). */
const NL = {
  groupName: 'Demo: videogesprek', // the demo seed, app/adapters/db/seed.py
  formTitle: 'Uw gegevens', // book.title
  firstName: 'Voornaam', // form.first_name
  lastName: 'Achternaam', // form.last_name
  email: 'E-mailadres', // form.email
  phone: 'Telefoonnummer', // form.phone
  submit: 'Afspraak maken', // form.submit
  bookedTitle: 'Bevestig uw afspraak', // booked.title
  bookedText: 'We hebben u een e-mail gestuurd', // booked.text
  confirm: 'Afspraak bevestigen', // confirm.title, confirm.submit
  confirmed: 'Uw afspraak is bevestigd', // manage.notice.confirmed
  manageTitle: 'Uw afspraak', // manage.title
  move: 'Afspraak verplaatsen', // manage.reschedule, reschedule.title
  moveConfirmTitle: 'Nieuw moment bevestigen', // reschedule.confirm_title
  moveSubmit: 'Verplaatsen', // reschedule.submit
  moved: 'Uw afspraak is verplaatst', // manage.notice.rescheduled
  cancel: 'Afspraak annuleren', // cancel.title
  cancelSubmit: 'Ja, annuleren', // cancel.confirm
  cancelledNotice: 'Uw afspraak is geannuleerd', // manage.notice.cancelled
  dashboard: 'Overzicht', // admin.dashboard.title
  yourGroups: 'Uw groepen', // admin.dashboard.groups
  adminMenu: 'Beheermenu', // admin.nav.label
  logout: 'Uitloggen', // admin.logout
  loggedOut: 'U bent uitgelogd', // admin.logged_out.title
  forbidden: 'Geen toegang', // admin.forbidden.title
  noRole: 'U heeft geen rol in het beheerportaal', // admin.no_role.text
  agendaCancelled: 'Geannuleerd', // admin.agenda.cancelled
  details: 'Details', // admin.agenda.details
  contact: 'Contactgegevens', // admin.booking.contact
  contactShow: 'Contactgegevens tonen', // admin.booking.contact_show
  contactHide: 'Contactgegevens verbergen', // admin.booking.contact_hide
  contactNoPhone: 'Niet opgegeven', // admin.booking.contact_no_phone
  auditContactViewed: 'Contactgegevens van de deelnemer getoond', // admin.audit.detail.booking_contact_viewed
  environmentFact: 'Omgeving', // admin.settings.status.environment
  environmentDev: 'Ontwikkelomgeving', // admin.environment.dev
  slotFact: 'Duur van een gesprek', // admin.group.slot_minutes, as admin.field.slot_minutes says
  noticeFact: 'Minimale aanlooptijd', // admin.group.notice, as admin.field.min_notice_minutes says
};

/** Subjects of the participant's emails (message.<kind>.subject in nl.po). */
const SUBJECT = {
  confirmRequest: /^Bevestig uw afspraak met /, // message.confirm_request.subject
  confirmation: /^Bevestiging van uw videogesprek/, // message.confirmation.subject
  change: /^Uw videogesprek is verplaatst/, // message.change.subject
  cancellation: /is geannuleerd$/, // message.cancellation.subject
};

/** A booking-link token in a URL path; /b/m/<reference> does not match. */
const TOKEN_PATH = /\/b\/[A-Za-z0-9_-]{20,}/g;
const HAS_TOKEN = /\/b\/[A-Za-z0-9_-]{20,}/;
const MAIL_WAIT_MS = 90_000;

const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');

function fail(message) {
  console.error(`journey.mjs: ${message}`);
  process.exit(2);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Poll `check` until it returns a truthy value; throw after `timeout` ms, naming `what`. */
async function until(what, check, timeout = 15_000) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try {
      const value = await check();
      if (value) return value;
    } catch (error) {
      last = error;
    }
    await sleep(250);
  }
  throw new Error(`timed out waiting for ${what}${last ? ` (${last.message.split('\n')[0]})` : ''}`);
}

/** The current run that stack.sh recorded: its folder, addresses and participant values. */
function loadRun() {
  const currentFile = join(EVIDENCE_ROOT, 'current');
  if (!existsSync(currentFile)) fail(`no verification stack is up (no ${currentFile}); run stack.sh up`);
  const id = readFileSync(currentFile, 'utf8').trim();
  if (!/^\d{8}-\d{6}$/.test(id)) fail(`${currentFile} does not hold a run id (YYYYMMDD-HHMMSS)`);
  const dir = join(EVIDENCE_ROOT, id);
  const env = {};
  for (const line of readFileSync(join(dir, 'stack.env'), 'utf8').split('\n')) {
    const at = line.indexOf('=');
    if (at > 0) env[line.slice(0, at)] = line.slice(at + 1);
  }
  if (env.VERIFY_STATE !== 'up') fail(`run ${id} is ${env.VERIFY_STATE}, not up; run stack.sh doctor`);
  for (const key of ['VERIFY_WEB_PORT', 'VERIFY_KC_PORT', 'VERIFY_MAIL_PORT']) {
    if (!/^\d{4,5}$/.test(env[key] ?? '')) fail(`${key} in ${join(dir, 'stack.env')} is not a port`);
  }
  return {
    id,
    dir,
    passwords: realmPasswords(),
    instance: env.INSTANCE_ID,
    web: `http://127.0.0.1:${env.VERIFY_WEB_PORT}`,
    keycloak: `http://127.0.0.1:${env.VERIFY_KC_PORT}`,
    mail: `http://127.0.0.1:${env.VERIFY_MAIL_PORT}`,
    participant: {
      first: env.PARTICIPANT_FIRST_NAME,
      last: env.PARTICIPANT_LAST_NAME,
      email: env.PARTICIPANT_EMAIL,
    },
  };
}

/** Refuse to drive an instance this run did not start (another stack on the port). */
async function assertOurs(run) {
  const answer = await fetch(`${run.web}/api/v1/instance`).then((r) => r.json()).catch(() => null);
  if (answer?.instance_id !== run.instance) {
    fail(`${run.web} is not the installation of run ${run.id}; run stack.sh doctor`);
  }
}

function loadPlaywright() {
  const require = createRequire(join(REPO, 'e2e', 'package.json'));
  try {
    return require('@playwright/test');
  } catch {
    return fail('Playwright is not installed in e2e/; run: (cd e2e && npm ci)');
  }
}

/** The development test users of dev/keycloak/openplanner-realm.json. */
function realmUsers() {
  return JSON.parse(readFileSync(join(REPO, 'dev', 'keycloak', 'openplanner-realm.json'), 'utf8')).users ?? [];
}

const passwordOf = (user) => user?.credentials?.find((c) => c.type === 'password')?.value;

/** The password of a development test user, read from the realm it is defined in. */
function testPassword(name) {
  const value = passwordOf(realmUsers().find((u) => u.username === name));
  if (!value) throw new Error(`dev/keycloak/openplanner-realm.json has no test user ${name} with a password`);
  return value;
}

/** Every test password of the realm, so evidence can blank one that a failed sign-in left on screen. */
function realmPasswords() {
  return [...new Set(realmUsers().map(passwordOf).filter(Boolean))];
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
  let out = String(text).replace(TOKEN_PATH, '/b/<token>');
  const { email, last, first } = run.participant;
  const values = [[email, '<email>'], [last, '<last_name>'], [first, '<first_name>']];
  for (const password of run.passwords) values.push([password, '<password>']);
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

  /**
   * Screenshot and ARIA snapshot of the page after a step, with what the step showed. A step
   * with `problems` keeps its evidence and is logged as failed; the journey decides when to stop.
   */
  async step(page, name, observation, problems = []) {
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
    const ok = !problems.length;
    const artifacts = [`${base}.png`, `${base}.aria.yml`];
    this.log({ step: name, ok, observation, ...(ok ? {} : { problems }), url: page.url(), artifacts });
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${this.folder}/${name}  ${redact(this.run, ok ? observation : problems.join('; '))}`);
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

/** Heading level 1 of the page, or '' while there is none. */
async function h1(page) {
  const heading = page.getByRole('heading', { level: 1 }).first();
  return (await heading.count()) ? (await heading.innerText()).trim() : '';
}

/** Text of the page's status messages (role status). */
async function statusText(page) {
  return (await page.getByRole('status').allInnerTexts()).join(' ');
}

/** The participant's messages in Mailpit, newest first. */
async function mailFor(run) {
  const query = encodeURIComponent(`to:"${run.participant.email}"`);
  const response = await fetch(`${run.mail}/api/v1/search?query=${query}&limit=50`);
  if (!response.ok) throw new Error(`Mailpit search answered ${response.status}`);
  return (await response.json()).messages ?? [];
}

/** Wait for a message to the participant, not in `seen`, whose subject matches; return it whole. */
async function waitForMail(run, seen, subject) {
  const summary = await until(
    `an email to this run's participant with subject ${subject}`,
    async () => (await mailFor(run)).find((m) => !seen.has(m.ID) && subject.test(m.Subject)),
    MAIL_WAIT_MS,
  );
  seen.add(summary.ID);
  const response = await fetch(`${run.mail}/api/v1/message/${summary.ID}`);
  if (!response.ok) throw new Error(`Mailpit message answered ${response.status}`);
  return response.json();
}

/**
 * The booking links in a message's plain-text part: their URLs, and the same with tokens blanked.
 * Throws when `required` and there is none.
 */
function linksIn(run, message, required = true) {
  const pattern = new RegExp(`${escapeRe(run.web)}/b/[A-Za-z0-9_-]{20,}(?:/[a-z]+)?`, 'g');
  const urls = [...new Set(String(message.Text ?? '').match(pattern) ?? [])];
  if (required && !urls.length) {
    throw new Error(`email "${message.Subject}" holds no booking link to ${run.web}; is PUBLIC_BASE_URL this stack's?`);
  }
  return { urls, shown: urls.map((url) => url.replace(TOKEN_PATH, '/b/<token>')) };
}

/** Pick a free day in the slot picker and wait until its times are shown; return the day. */
async function chooseFreeDay(page) {
  const day = page.locator('a.calendar__day').first();
  await day.waitFor();
  const iso = await day.getAttribute('data-day');
  await day.click();
  // calendar.js swaps in the day's times and puts ?day= in the address; without it, the link loads.
  await until(`the times of ${iso}`, async () => {
    const url = new URL(page.url());
    const busy = await page.locator('#times[aria-busy="true"]').count();
    return url.searchParams.get('day') === iso && !busy && (await page.locator('#times .time-button').count()) > 0;
  });
  return iso;
}

/** Sign in at Keycloak as a development test user and wait until the portal answers. */
async function login(page, run, user, path = '/admin/') {
  await page.goto(path);
  if (new URL(page.url()).pathname === '/admin/login') {
    // The e-mail-first page, only when organisation identity providers route logins (ADR 0083).
    await page.locator('#identifier').fill(user);
    await page.locator('#identifier').press('Enter');
  }
  await until(`the Keycloak sign-in for ${user}`, () => page.url().startsWith(`${run.keycloak}/`));
  await page.locator('#username').fill(user);
  await page.locator('#password').fill(testPassword(user));
  await page.locator('#password').press('Enter');
  await until(`the portal after signing in as ${user}`, () => {
    const url = new URL(page.url());
    return url.origin === run.web && url.pathname.startsWith('/admin');
  });
}

/** participant-booking.md: book, get the confirmation request, confirm by the emailed link. */
async function booking({ page, rec, run }) {
  const { participant } = run;
  const seen = new Set((await mailFor(run)).map((m) => m.ID));

  await page.goto('/g/demo');
  await until(`h1 "${NL.groupName}"`, async () => (await h1(page)) === NL.groupName);
  await rec.step(page, 'group-page', `h1 "${NL.groupName}" with the month calendar`);

  const day = await chooseFreeDay(page);
  const time = page.locator('#times .time-button').first();
  const start = await time.getAttribute('value');
  const label = (await time.innerText()).trim();
  await rec.step(page, 'day-times', `day ${day}: ${await page.locator('#times .time-button').count()} free times, first ${label}`);

  await time.click();
  await until(`h1 "${NL.formTitle}"`, async () => (await h1(page)) === NL.formTitle);
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
  await until(`h1 "${NL.bookedTitle}"`, async () => (await h1(page)) === NL.bookedTitle);
  if (!(await statusText(page)).includes(NL.bookedText)) throw new Error(`no status "${NL.bookedText}"`);
  await rec.step(page, 'check-your-email', `h1 "${NL.bookedTitle}", status "${NL.bookedText}"`);

  const request = await waitForMail(run, seen, SUBJECT.confirmRequest);
  const requestLinks = linksIn(run, request);
  const confirmUrl = requestLinks.urls.find((url) => url.endsWith('/confirm'));
  if (!confirmUrl) throw new Error(`email "${request.Subject}" has no /confirm link`);
  rec.email('confirm-request-email', request, requestLinks.shown);

  await page.goto(confirmUrl);
  await until(`h1 "${NL.confirm}" on /b/m/<reference>/confirm`, async () =>
    /^\/b\/m\/[\w-]+\/confirm$/.test(new URL(page.url()).pathname) && (await h1(page)) === NL.confirm);
  // ADR 0093: the token is traded for a cookie; the address that stays holds none.
  if (HAS_TOKEN.test(new URL(page.url()).pathname)) throw new Error('the confirm page address still holds the token');
  const reference = new URL(page.url()).pathname.split('/')[3];
  await rec.step(page, 'confirm-page', `the emailed link led to /b/m/${reference}/confirm (no token in the address)`);

  await page.getByRole('button', { name: NL.confirm }).click();
  await until(`status "${NL.confirmed}"`, async () => (await statusText(page)).includes(NL.confirmed));
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
  const confirmation = await fetch(`${run.mail}/api/v1/message/${latest.ID}`).then((r) => r.json());
  // The plain /b/<token> link; /b/<token>/join and /b/<token>/test lead elsewhere.
  const manageUrl = linksIn(run, confirmation).urls.find((url) => !url.slice(url.indexOf('/b/') + 3).includes('/'));
  if (!manageUrl) throw new Error(`email "${confirmation.Subject}" has no management link`);

  await page.goto(manageUrl);
  await until(`h1 "${NL.manageTitle}" on /b/m/${state.booking.reference}`, async () =>
    new URL(page.url()).pathname === `/b/m/${state.booking.reference}` && (await h1(page)) === NL.manageTitle);
  await rec.step(page, 'manage-page', `the emailed link led to /b/m/${state.booking.reference}, h1 "${NL.manageTitle}"`);

  await page.getByRole('link', { name: NL.move }).click();
  await until(`h1 "${NL.move}"`, async () => (await h1(page)) === NL.move);
  await rec.step(page, 'reschedule-page', `h1 "${NL.move}" with the slot picker`);

  const day = await chooseFreeDay(page);
  const times = page.locator('#times .time-button');
  let index = 0;
  while (index < (await times.count()) && (await times.nth(index).getAttribute('value')) === state.booking.start) index++;
  if (index >= (await times.count())) throw new Error(`no other free time on ${day}`);
  const start = await times.nth(index).getAttribute('value');
  const label = (await times.nth(index).innerText()).trim();
  await times.nth(index).click();
  await until(`h1 "${NL.moveConfirmTitle}"`, async () => (await h1(page)) === NL.moveConfirmTitle);
  await rec.step(page, 'reschedule-confirm', `h1 "${NL.moveConfirmTitle}": from ${state.booking.time} on ${state.booking.day} to ${label} on ${day}`);

  await page.getByRole('button', { name: NL.moveSubmit }).click();
  await until(`status "${NL.moved}"`, async () => (await statusText(page)).includes(NL.moved));
  // A move makes a new booking with a reference of its own; the address shows it.
  const reference = new URL(page.url()).pathname.split('/')[3];
  await rec.step(page, 'moved', `status "${NL.moved}"; the booking is now /b/m/${reference}`);
  const change = await waitForMail(run, seen, SUBJECT.change);
  rec.email('change-email', change, linksIn(run, change).shown);

  await page.getByRole('link', { name: NL.cancel }).click();
  await until(`h1 "${NL.cancel}"`, async () => (await h1(page)) === NL.cancel);
  await rec.step(page, 'cancel-page', `h1 "${NL.cancel}"`);

  await page.getByRole('button', { name: NL.cancelSubmit }).click();
  await until(`status "${NL.cancelledNotice}"`, async () => (await statusText(page)).includes(NL.cancelledNotice));
  await rec.step(page, 'cancelled', `status "${NL.cancelledNotice}"`);
  const cancellation = await waitForMail(run, seen, SUBJECT.cancellation);
  rec.email('cancellation-email', cancellation, linksIn(run, cancellation, false).shown);

  saveState(run, { booking: { reference, day, start, time: label, status: 'cancelled' } });
}

/**
 * Sign in as the group admin on the agenda day of this run's booking and return its one row.
 * Throws without a booking of this run (the booking journey makes it).
 */
async function agendaRow(page, run) {
  const { booking: current } = loadState(run);
  if (!current) throw new Error('precondition: a booking of this run (run the booking journey first)');
  const path = `/admin/groups/demo/agenda?day=${current.day}`;

  await login(page, run, 'groupadmin', path);
  // The login keeps only the path; open the day again.
  await page.goto(path);
  await until('the agenda day heading', () => page.locator('#agenda-heading').isVisible());
  // The row header reads "<start>–<end>"; match the start, since a moved booking's old row may end
  // at the new start.
  const row = page
    .getByRole('row')
    .filter({ hasText: run.participant.first })
    .filter({ has: page.locator('th', { hasText: new RegExp(`^${escapeRe(current.time)}–`) }) });
  await until(`one agenda row at ${current.time} for this run's participant`, async () => (await row.count()) === 1);
  return { current, path, row };
}

/** admin-agenda.md: the group admin finds this run's booking; another organisation's admin is refused. */
async function agenda({ page, rec, run, browser }) {
  const { current, path, row } = await agendaRow(page, run);
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
    await until(`h1 "${NL.forbidden}"`, async () => (await h1(outsider)) === NL.forbidden);
    if (response?.status() !== 403) throw new Error(`the outsider got status ${response?.status()}, not 403`);
    await rec.step(outsider, 'outsider-refused', `outsider (organisation andere) gets 403 "${NL.forbidden}" on the demo agenda`);
  } finally {
    await other.close();
  }
}

/**
 * booking-contact.md: the booking page shows the participant's contact details only on request,
 * hides them again, and the audit log records the request without them.
 */
async function contact({ page, rec, run }) {
  const { first, last, email } = run.participant;
  const { current, row } = await agendaRow(page, run);
  await row.getByRole('link', { name: new RegExp(`^${NL.details}\\b`) }).click();
  const card = page.getByRole('region', { name: NL.contact });
  await until(`the card "${NL.contact}" on the booking page`, () => card.isVisible());
  if ((await page.getByText(email).count()) || (await page.getByText(last).count())) {
    throw new Error('the booking page shows the contact details before anyone asked for them');
  }
  const show = card.getByRole('button', { name: NL.contactShow });
  if (!(await show.isVisible())) throw new Error(`no button "${NL.contactShow}"`);
  await rec.step(page, 'booking-page', `the booking page of ${current.time} shows "${NL.contactShow}" and no contact details`);

  await show.click();
  const mail = card.getByRole('link', { name: email, exact: true });
  await until('the contact details after the request', async () =>
    new URL(page.url()).searchParams.get('contact') === 'show' && (await mail.count()) === 1);
  if ((await mail.getAttribute('href')) !== `mailto:${email}`) throw new Error('the address is not a mailto link');
  if (!(await card.getByText(`${first} ${last}`, { exact: true }).count())) throw new Error('the full name is not shown');
  // The booking journey gives no phone number, so the card says so instead of a tel: link.
  if (!(await card.getByText(NL.contactNoPhone, { exact: true }).count())) throw new Error(`no "${NL.contactNoPhone}" for the phone`);
  await rec.step(page, 'contact-shown',
    `"${NL.contactShow}" shows the full name, a mailto link and "${NL.contactNoPhone}" for the phone`);

  await card.getByRole('link', { name: NL.contactHide }).click();
  await until('the contact details hidden again', async () =>
    !new URL(page.url()).searchParams.has('contact') && (await page.getByText(email).count()) === 0);
  await rec.step(page, 'contact-hidden', `"${NL.contactHide}" opens the page without them`);

  await page.goto('/admin/audit');
  const event = page.getByRole('row').filter({ hasText: NL.auditContactViewed });
  await until(`the audit event "${NL.auditContactViewed}"`, async () => (await event.count()) === 1);
  const text = await page.locator('main').innerText();
  if ([first, last, email].some((value) => text.includes(value))) throw new Error('the audit log holds a participant value');
  await rec.step(page, 'audit-event', `the audit log lists "${NL.auditContactViewed}" once, without the participant's values`);
}

/** admin-login.md: sign in and out through Keycloak; a user without a role is refused. */
async function adminLogin({ page, rec, run }) {
  await page.goto('/admin/');
  await until('the Keycloak sign-in page', () => page.url().startsWith(`${run.keycloak}/realms/openplanner/`));
  await rec.step(page, 'keycloak-sign-in', `/admin/ sends an anonymous visitor to ${run.keycloak}/realms/openplanner`);

  await login(page, run, 'groupadmin');
  await until(`h1 "${NL.dashboard}"`, async () => (await h1(page)) === NL.dashboard);
  const groups = page.getByRole('list', { name: NL.yourGroups }).getByRole('listitem');
  if ((await groups.count()) !== 1 || !(await groups.first().innerText()).includes(NL.groupName)) {
    throw new Error(`"${NL.yourGroups}" does not list exactly "${NL.groupName}"`);
  }
  await rec.step(page, 'dashboard', `h1 "${NL.dashboard}"; "${NL.yourGroups}" lists only "${NL.groupName}"`);

  await page.locator('details.admin-profile > summary').click();
  await page.getByRole('button', { name: NL.logout }).click();
  await until('the Keycloak logout page', () => page.url().startsWith(`${run.keycloak}/`) && page.url().includes('/logout'));
  await page.locator('#kc-logout').click();
  await until(`h1 "${NL.loggedOut}"`, async () => (await h1(page)) === NL.loggedOut);
  await rec.step(page, 'logged-out', `h1 "${NL.loggedOut}" after the Keycloak logout`);

  await page.goto('/admin/');
  await until('the Keycloak sign-in page again', () => page.url().startsWith(`${run.keycloak}/`));
  await rec.step(page, 'sign-in-again', 'after logging out, /admin/ asks for a sign-in again');

  await login(page, run, 'norole');
  await until(`h1 "${NL.forbidden}"`, async () => (await h1(page)) === NL.forbidden);
  const links = await page.getByRole('navigation', { name: NL.adminMenu }).getByRole('link').count();
  if (!(await page.getByText(NL.noRole).isVisible()) || links !== 0) {
    throw new Error(`norole sees ${links} menu links or no "${NL.noRole}"`);
  }
  await rec.step(page, 'no-role-refused', `norole: h1 "${NL.forbidden}", "${NL.noRole}", no menu links`);
  rec.expectConsole('sign-in-again', /status of 403/, 'the portal answers 403 to a user without a role');
}

/** The screen of an ordinary laptop, at which admin-layout.md checks the portal. */
const LAPTOP = { width: 1280, height: 800 };

/** The next working day after today (the demo group's slots are on weekdays), as YYYY-MM-DD. */
function nextWorkingDay() {
  const day = new Date();
  do day.setUTCDate(day.getUTCDate() + 1);
  while ([0, 6].includes(day.getUTCDay()));
  return day.toISOString().slice(0, 10);
}

/** admin-layout.md: admin pages at 1280 × 800 keep their content in their boxes and read right. */
async function adminLayout({ page, rec, run, browser }) {
  const day = loadState(run).booking?.day ?? nextWorkingDay();
  const factText = async (target, term) => {
    const fact = target.locator('dl.facts > div').filter({ has: target.locator('dt', { hasText: new RegExp(`^${escapeRe(term)}$`) }) });
    return (await fact.count()) ? (await fact.first().locator('dd').innerText()).trim() : null;
  };
  const pages = [
    { user: 'super', path: '/admin/', step: 'overview' },
    {
      user: 'super',
      path: '/admin/settings',
      step: 'system-status',
      texts: async (target) => {
        const value = await factText(target, NL.environmentFact);
        return value === NL.environmentDev ? [] : [`"${NL.environmentFact}" reads "${value}", not "${NL.environmentDev}"`];
      },
    },
    { user: 'super', path: '/admin/idp-mapping', step: 'idp-mapping' },
    {
      user: 'super',
      path: '/admin/roles/new',
      step: 'new-role',
      texts: async (target) => {
        const mentions = (await target.locator('main').innerText()).match(/\bADR \d{4}\b/g) ?? [];
        return mentions.map((mention) => `the page text mentions "${mention}", a design record`);
      },
    },
    {
      user: 'super',
      path: '/admin/groups/demo',
      step: 'group-overview',
      texts: async (target) => {
        const terms = (await target.locator('main dl.facts dt').allInnerTexts()).map((t) => t.trim());
        return [NL.slotFact, NL.noticeFact].filter((t) => !terms.includes(t)).map((t) => `no fact "${t}" (the settings form's term)`);
      },
    },
    { user: 'super', path: `/admin/groups/demo/agenda?day=${day}`, step: 'agenda-day' },
    { user: 'super', path: `/admin/groups/demo/agenda/week?week=${day}`, step: 'agenda-week' },
    { user: 'super', path: '/admin/matrix', step: 'rights-matrix' },
    { user: 'super', path: '/admin/audit', step: 'audit-log' },
    { user: 'auditor', path: '/admin/', step: 'overview-auditor' },
  ];

  await page.setViewportSize(LAPTOP);
  const failed = [];
  const contexts = [];
  try {
    const pageOf = { super: page };
    const signedIn = new Set();
    for (const { user, path, step, texts } of pages) {
      if (!pageOf[user]) {
        const context = await browser.newContext({ baseURL: run.web, locale: 'nl-NL', viewport: LAPTOP });
        contexts.push(context);
        pageOf[user] = await context.newPage();
      }
      const target = pageOf[user];
      if (!signedIn.has(user)) {
        await login(target, run, user, path);
        signedIn.add(user);
      }
      await target.goto(path);
      await until(`a level 1 heading on ${path}`, async () => (await h1(target)) !== '');
      const problems = [...(await target.evaluate(layoutProblems)), ...(texts ? await texts(target) : [])];
      if (problems.length) failed.push(step);
      await rec.step(target, step, `${user} on ${path} at ${LAPTOP.width} × ${LAPTOP.height}: nothing runs past its box${texts ? ', the texts read right' : ''}`, problems);
    }
  } finally {
    for (const context of contexts) await context.close();
  }
  if (failed.length) {
    throw new Error(`${failed.length} of ${pages.length} pages show a fault at ${LAPTOP.width} × ${LAPTOP.height}: ${failed.join(', ')}`);
  }
}

const JOURNEYS = { booking, agenda, contact, manage, 'admin-login': adminLogin, 'admin-layout': adminLayout };
const ALL = ['booking', 'agenda', 'contact', 'manage', 'agenda', 'admin-login', 'admin-layout'];

async function main() {
  const asked = process.argv.slice(2);
  if (!asked.length || asked.some((a) => a.startsWith('-'))) {
    console.log(readFileSync(fileURLToPath(import.meta.url), 'utf8').split('\n').slice(2, 20).join('\n').replace(/^ \* ?/gm, ''));
    process.exit(asked.length ? 0 : 2);
  }
  const names = asked.flatMap((name) => (name === 'all' ? ALL : [name]));
  const unknown = names.filter((name) => !JOURNEYS[name]);
  if (unknown.length) fail(`unknown journey ${unknown.join(', ')} (${Object.keys(JOURNEYS).join(', ')}, all)`);

  const run = loadRun();
  await assertOurs(run);
  const { chromium } = loadPlaywright();
  const channel = process.env.PLAYWRIGHT_CHROMIUM_CHANNEL ?? 'chrome';
  const browser = await chromium.launch({ channel: channel || undefined });
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
