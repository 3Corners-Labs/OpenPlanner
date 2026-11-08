/**
 * The core of the capture story: one run on the verification stack (browser sessions per
 * person and staff member, the story state, the records), the people of the story, the step
 * constructors, and the participant and planner actions that the story and the sample data
 * share. The steps themselves are in steps-*.mjs; capture.mjs puts them in story order.
 *
 * Everything goes through real user paths: the group page, the links in the emails the worker
 * sent, and the portal as the development users. Nothing is written to the database and the
 * clock is never shifted.
 */
import {
  NL, SUBJECT, chooseDay, escapeRe, linksIn, login, mailFor, mailMessage, manageLinkIn, waitForMail,
  waitH1, waitStatus,
} from '../../verify-openplanner/scripts/stack-client.mjs';
import { clock, nextWeekday, saveState, stamp } from './evidence.mjs';
import { EMAIL_WIDTH, Email, MOBILE, SCALE, VIEWPORT, Viewport, WIDE, shoot } from './shots.mjs';

export const GROUP = 'demo';
export const ORG = 'demo';

/** The made-up participants of the story; their addresses never leave Mailpit. */
export const PEOPLE = {
  sanne: { key: 'sanne', first: 'Sanne', last: 'Jansen', email: 'sanne.jansen@voorbeeld.nl' },
  ahmed: { key: 'ahmed', first: 'Ahmed', last: 'el Amrani', email: 'ahmed.elamrani@voorbeeld.nl' },
  pieter: { key: 'pieter', first: 'Pieter', last: 'de Bakker', email: 'pieter@dekorenaar.voorbeeld.nl' },
};

/** Someone who only looks at the participant pages. */
export const VIEWER = { key: 'viewer' };

/** The development users as the log names them. */
export const ROLES = {
  super: 'applicatiebeheerder (`super`)',
  orgadmin: 'organisatiebeheerder (`orgadmin`)',
  groupadmin: 'planner (`groupadmin`)',
  agent: 'medewerker (`agent`)',
  auditor: 'compliance en privacy (`auditor`)',
};

/** Admin texts the story waits for or fills in (app/i18n/locales/nl.po); participant texts are NL. */
export const UI = {
  save: 'Opslaan',
  details: /^Details/,
  move: /^Verplaatsen/,
  cancel: /^Annuleren/,
  moveHeading: /^Afspraak .* verplaatsen/,
  cancelHeading: /^Afspraak .* annuleren/,
  newSlot: /^Nieuw tijdslot/,
  reason: 'Reden',
  plannerMove: 'Verplaatsen',
  plannerCancel: 'Afspraak annuleren',
  exceptionForm: 'Afwijkende bezetting toevoegen',
};

/** A step that cannot be captured on this installation; the reason goes into the log. */
export class Skip extends Error {}

/** A step that must wait for a moment of today (the queue window), in minutes since midnight. */
export class NotYet extends Error {
  constructor(minutes, why) {
    super(why);
    this.minutes = minutes;
  }
}

/** A story action without a screenshot: preparation, a booking, a change. */
export const action = (id, run) => ({ id, action: true, run });

/** A step that captures `id` with logic of its own; `run(run, id)` gets the id passed in. */
export const step = (id, title, run, { wide = false } = {}) => ({ id, title, run, wide });

/**
 * A screen that only needs opening as `user` at `path`; `crop(page)` builds its cut from the
 * page (default: the window).
 */
export const screen = ({ id, title, user, path, visible, crop = null, notes = [], wide = false }) => step(id, title, async (run) => {
  const page = await run.staff(user);
  await page.goto(path);
  await page.waitForLoadState('load');
  await run.shot(page, id, { role: ROLES[user], visible, crop: crop ? crop(page) : new Viewport(), notes });
}, { wide });

/** An id that needs Webex Meetings: listed, never run on an installation without it. */
export const webex = (id, title) => ({ id, title, needsWebex: true });

/** Everything one invocation shares: browser, sessions, state and the capture records. */
export class Run {
  constructor({ ws, stack, browser, state, hidden }) {
    Object.assign(this, { ws, stack, browser, state, hidden });
    this.contexts = new Map();
    this.pages = new Map();
    this.staffViewport = VIEWPORT;
  }

  save() {
    saveState(this.ws, this.state);
  }

  get records() {
    this.state.records ??= {};
    return this.state.records;
  }

  get bookings() {
    this.state.bookings ??= {};
    return this.state.bookings;
  }

  /** A booking of the story, or Skip naming the step that makes it. */
  booking(key) {
    if (!this.bookings[key]) throw new Skip(`de boeking ${key} bestaat nog niet (eerdere stap van het verhaal niet gedraaid)`);
    return this.bookings[key];
  }

  /** Record a preparation step or a story change for the log, under its Dutch `label`. */
  notePrep(key, label, text) {
    this.state.prep ??= {};
    this.state.prep[key] = { label, at: stamp(), text };
    this.save();
  }

  /** The two weekdays of the story, fixed at the first run. */
  days() {
    this.state.days ??= {};
    if (!this.state.days.first) {
      const { day } = clock();
      this.state.days.first = nextWeekday(day, 1);
      this.state.days.second = nextWeekday(day, 2);
      this.save();
    }
    return this.state.days;
  }

  async context(key, { mobile = false, width = null } = {}) {
    if (!this.contexts.has(key)) {
      const viewport = width ? { width, height: 400 } : { ...(mobile ? MOBILE : VIEWPORT) };
      this.contexts.set(key, await this.browser.newContext({
        baseURL: this.stack.web,
        locale: 'nl-NL',
        timezoneId: 'Europe/Amsterdam',
        viewport,
        deviceScaleFactor: SCALE,
        isMobile: mobile,
        hasTouch: mobile,
        colorScheme: 'light',
        reducedMotion: 'reduce',
      }));
    }
    return this.contexts.get(key);
  }

  /** A page signed in as development user `user`, in the window of the current step. */
  async staff(user) {
    if (!this.pages.has(user)) {
      const page = await (await this.context(`staff:${user}`)).newPage();
      await login(page, this.stack, user);
      this.pages.set(user, page);
    }
    const page = this.pages.get(user);
    await page.setViewportSize(this.staffViewport);
    return page;
  }

  /** Run `fn` with the staff window of `stepDef`: WIDE for a wide overview, else VIEWPORT. */
  async inWindowOf(stepDef, fn) {
    if (!stepDef.wide) return fn();
    this.staffViewport = WIDE;
    try {
      return await fn();
    } finally {
      this.staffViewport = VIEWPORT;
      for (const [key, page] of this.pages) {
        if (!key.startsWith('participant:') && key !== 'mail') await page.setViewportSize(VIEWPORT);
      }
    }
  }

  /** The browser of a participant (one session per person; `fresh` starts a new one). */
  async participant(person, { mobile = false, fresh = false } = {}) {
    const key = `participant:${person.key}${mobile ? ':mobile' : ''}`;
    if (fresh && this.contexts.has(key)) {
      await this.contexts.get(key).close();
      this.contexts.delete(key);
      this.pages.delete(key);
    }
    if (!this.pages.has(key)) this.pages.set(key, await (await this.context(key, { mobile })).newPage());
    const page = this.pages.get(key);
    await page.setViewportSize(mobile ? MOBILE : VIEWPORT);
    return page;
  }

  /** A page 700 CSS pixels wide that renders an email's HTML part. */
  async mailPage() {
    if (!this.pages.has('mail')) this.pages.set('mail', await (await this.context('mail', { width: EMAIL_WIDTH })).newPage());
    return this.pages.get('mail');
  }

  /** Capture `page` as `id` and keep its record. */
  async shot(page, id, options) {
    const record = await shoot(page, { id, img: this.ws.img, hidden: this.hidden, ...options });
    this.records[id] = { ...record, at: stamp() };
    this.save();
    console.log(`  ok   ${id}  ${record.size_px.join('x')}  covered=${record.covered.length}  ${record.address}`);
  }

  /** Render a message's HTML part and capture the message only. */
  async emailShot(id, message, visible) {
    const page = await this.mailPage();
    await page.setViewportSize({ width: EMAIL_WIDTH, height: 400 });
    await page.setContent(message.HTML || '<p>(geen HTML-deel)</p>', { waitUntil: 'load' });
    const attachments = (message.Attachments ?? []).map((a) => a.FileName ?? '?').join(', ') || 'geen';
    await this.shot(page, id, {
      role: `deelnemer: e-mail zoals ontvangen (Mailpit, ${(message.To ?? []).map((t) => t.Address).join(', ')})`,
      visible,
      crop: new Email(),
      address: `Mailpit-bericht ${message.ID} (HTML-deel), onderwerp "${message.Subject}"`,
      notes: [`Verzonden ${message.Date ? stamp(new Date(message.Date)) : '?'}; bijlagen: ${attachments}`],
    });
  }

  /** Record that `id` was not captured, and why; an earlier capture of it stays. */
  skipped(id, reason) {
    const old = this.records[id];
    if (old?.status === 'captured') {
      old.notes = [...(old.notes ?? []), `Latere poging (${stamp()}) niet opgenomen: ${reason}; deze opname blijft staan.`];
      console.log(`  --   ${id}  niet opnieuw opgenomen (de eerdere opname blijft): ${reason}`);
    } else {
      this.records[id] = { id, status: 'skipped', reason, at: stamp() };
      console.log(`  --   ${id}  niet opgenomen: ${reason}`);
    }
    this.save();
  }
}

// ---- Participant actions -------------------------------------------------------------------

const referenceOf = (page) => new URL(page.url()).pathname.split('/')[3];

/** The ids of every message to `person` so far. */
export const seenMail = async (run, person) => new Set((await mailFor(run.stack, person.email)).map((m) => m.ID));

/** The time button that reads `label`. */
export async function timeButton(page, label) {
  const buttons = page.locator('#times .time-button');
  const button = buttons.filter({ hasText: new RegExp(`^\\s*${escapeRe(label)}\\s*$`) });
  if (!(await button.count())) {
    throw new Error(`no free time ${label} (free: ${(await buttons.allInnerTexts()).map((t) => t.trim()).join(', ')})`);
  }
  return button.first();
}

/** The free times the group page offers on `day`; none when the day is not offered. */
export async function freeTimes(run, day) {
  const page = await run.participant(VIEWER, { fresh: true });
  await page.goto(`/g/${GROUP}`);
  await waitH1(page, NL.groupName);
  if (!(await page.locator(`a.calendar__day[data-day="${day}"]`).count())) return [];
  await chooseDay(page, day);
  return (await page.locator('#times .time-button').allInnerTexts()).map((t) => t.trim());
}

export async function fillContact(page, person, email = person.email) {
  await page.getByLabel(NL.firstName).fill(person.first);
  await page.getByLabel(NL.lastName).fill(person.last);
  await page.getByLabel(NL.email).fill(email);
}

export async function submitBooking(page) {
  await page.getByRole('button', { name: NL.submit }).click();
  await waitH1(page, NL.bookedTitle);
}

/** Keep a booking of the story; the person's name goes along, for the log. */
export function recordBooking(run, key, person, fields) {
  run.bookings[key] = { who: person.key, name: `${person.first} ${person.last}`, ...fields };
  run.save();
}

/** Book `day` at `label` on the group page as `person`; the booking waits for confirmation. */
export async function book(run, key, person, day, label) {
  const page = await run.participant(person, { fresh: true });
  await page.goto(`/g/${GROUP}`);
  await waitH1(page, NL.groupName);
  await chooseDay(page, day);
  await (await timeButton(page, label)).click();
  await waitH1(page, NL.formTitle);
  await fillContact(page, person);
  await submitBooking(page);
  recordBooking(run, key, person, { day, time: label, status: 'pending', booked_at: stamp() });
  return page;
}

/** Open the link of the confirm request email and keep the reference; return the email. */
export async function confirmByLink(run, page, person, seen, key) {
  const request = await waitForMail(run.stack, seen, SUBJECT.confirmRequest, person.email);
  const confirm = linksIn(run.stack, request).urls.find((u) => u.endsWith('/confirm'));
  if (!confirm) throw new Error('the confirm request holds no /confirm link');
  await page.goto(confirm);
  await waitH1(page, NL.confirm);
  Object.assign(run.bookings[key], { reference: referenceOf(page), confirm_request_mail: request.ID });
  run.save();
  return request;
}

/** Book, then confirm through the emailed link; a booking already confirmed is left alone. */
export async function bookAndConfirm(run, key, person, day, label) {
  if (run.bookings[key]?.status === 'confirmed') {
    console.log(`  ..   ${key}: al geboekt en bevestigd (${run.bookings[key].reference})`);
    return;
  }
  const seen = await seenMail(run, person);
  const page = await book(run, key, person, day, label);
  await confirmByLink(run, page, person, seen, key);
  await page.getByRole('button', { name: NL.confirm }).click();
  await waitStatus(page, NL.confirmed);
  const confirmation = await waitForMail(run.stack, seen, SUBJECT.confirmation, person.email);
  Object.assign(run.bookings[key], { status: 'confirmed', confirmation_mail: confirmation.ID, confirmed_at: stamp() });
  run.save();
  console.log(`  ..   ${key}: ${person.first} ${day} ${label} geboekt en bevestigd via de e-maillink (${run.bookings[key].reference})`);
}

/** Open the management link of a booking's email in the participant's browser. */
export async function openManageLink(run, key, mailKey = 'confirmation_mail') {
  const booking = run.booking(key);
  const link = manageLinkIn(run.stack, await mailMessage(run.stack, booking[mailKey]));
  const page = await run.participant({ key: booking.who }, { fresh: true });
  await page.goto(link);
  await waitH1(page, NL.manageTitle);
  return page;
}

export async function openMove(page) {
  await page.getByRole('link', { name: NL.move }).click();
  await waitH1(page, NL.move);
  await page.locator('a.calendar__day').first().waitFor({ timeout: 15_000 });
}

/** Choose `day` and `label` (or its first free time) for the move. */
export async function pickNewMoment(page, day, label = null) {
  await chooseDay(page, day);
  await (label ? await timeButton(page, label) : page.locator('#times .time-button').first()).click();
  await waitH1(page, NL.moveConfirmTitle);
}

/** Confirm the move; return the reference of the new booking. */
export async function confirmMove(page) {
  await page.getByRole('button', { name: NL.moveSubmit }).click();
  await waitStatus(page, NL.moved);
  return referenceOf(page);
}

export async function openCancel(page) {
  await page.getByRole('link', { name: NL.cancel }).click();
  await waitH1(page, NL.cancel);
}

export async function confirmCancel(page) {
  await page.getByRole('button', { name: NL.cancelSubmit }).click();
  await waitStatus(page, NL.cancelledNotice);
}

// ---- Planner actions -----------------------------------------------------------------------

/** The agenda row of `firstName` whose header starts at `timeLabel`. */
export async function agendaRow(page, firstName, timeLabel) {
  const row = page.getByRole('row').filter({ hasText: firstName })
    .filter({ has: page.locator('th', { hasText: new RegExp(`^${escapeRe(timeLabel)}–`) }) });
  await row.first().waitFor({ timeout: 15_000 });
  return row.first();
}

/** Open the planner's form behind `link` (UI.move, UI.cancel, ...) on a booking's agenda row. */
export async function openPlannerForm(run, booking, link, heading) {
  const page = await run.staff('groupadmin');
  await page.goto(`/admin/groups/${GROUP}/agenda?day=${booking.day}`);
  await (await agendaRow(page, booking.name.split(' ')[0], booking.time)).getByRole('link', { name: link }).click();
  await page.getByRole('heading', { name: heading }).first().waitFor({ timeout: 15_000 });
  return page;
}

/** Choose a slot in the planner's move form, preferring one that holds `prefer`; return it. */
export async function chooseSlot(page, prefer = '11:00') {
  const select = page.getByLabel(UI.newSlot);
  if ((await select.count()) && (await select.first().evaluate((e) => e.tagName)) === 'SELECT') {
    const options = (await select.first().evaluate((s) => [...s.options].map((o) => o.text))).filter((o) => o.trim());
    const chosen = options.find((o) => o.includes(prefer)) ?? options[0];
    if (chosen) await select.first().selectOption({ label: chosen });
    return chosen ?? '';
  }
  const radio = page.getByRole('radio').filter({ hasText: new RegExp(escapeRe(prefer)) });
  if (await radio.count()) {
    await radio.first().check();
    return prefer;
  }
  return '';
}

/** Choose the reason that matches `pattern` in a planner form (a list or radio buttons). */
export async function chooseReason(page, pattern) {
  const reason = page.getByLabel(UI.reason);
  if ((await reason.count()) && (await reason.first().evaluate((e) => e.tagName)) === 'SELECT') {
    const label = await reason.first().evaluate((s, source) => [...s.options].find((o) => new RegExp(source).test(o.text))?.text, pattern.source);
    if (label) await reason.first().selectOption({ label });
  }
  const radio = page.getByRole('radio', { name: pattern });
  if (await radio.count()) await radio.first().check();
}

/** The form that holds `locator`. */
export const formOf = (locator) => locator.locator('xpath=ancestor::form[1]');
