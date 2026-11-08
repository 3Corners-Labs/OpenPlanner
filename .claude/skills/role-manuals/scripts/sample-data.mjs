#!/usr/bin/env node
/**
 * Load sample data into the verification stack through real user paths, around the day of the
 * run, so agendas, the week, the reports and the audit log are filled. Part of the role-manuals
 * skill (../SKILL.md).
 *
 *   node .claude/skills/role-manuals/scripts/sample-data.mjs <workspace> [--count 18]
 *
 * Made-up participants book on the working days from the third weekday after today on (today and
 * the two days of the capture story stay as the story needs them) and confirm through the link
 * in their email; some move or cancel through their management link, a few never confirm, and
 * the planner moves and cancels a few in the agenda. The worker rolls the usage facts up into the
 * reports every 15 minutes, well before the capture reaches its report screen. Nothing is written
 * to the database and the clock is never shifted.
 *
 * The capture story books seven times more from the same address, so the booking limit per
 * address is raised to the portal's ceiling first (30 an hour, as a superuser would before a busy
 * day) and the default stays at 18 bookings.
 */
import { assertOurs, launchBrowser, loadRun } from '../../verify-openplanner/scripts/stack-client.mjs';
import { clock, loadState, nextWeekday } from './evidence.mjs';
import {
  Run, UI, bookAndConfirm, book, chooseReason, confirmCancel, confirmMove, formOf, freeTimes, openCancel, openManageLink,
  openMove, openPlannerForm, pickNewMoment,
} from './story.mjs';
import { fail, hiddenTerms, openWorkspace } from './workspace.mjs';

const NAMES = [
  ['Lotte', 'Bakker'], ['Mohammed', 'Yilmaz'], ['Eva', 'de Graaf'], ['Daan', 'Visser'], ['Fatima', 'Bouzid'],
  ['Ruben', 'Smit'], ['Noor', 'Hendriks'], ['Thomas', 'van Dijk'], ['Sophie', 'Mulder'], ['Yusuf', 'Kaya'],
  ['Emma', 'de Wit'], ['Lucas', 'Bos'], ['Julia', 'Vermeulen'], ['Milan', 'Peters'], ['Sara', 'Koster'],
  ['Bram', 'Jacobs'], ['Lisa', 'van Leeuwen'], ['Sem', 'Dekker'], ['Anna', 'Brouwer'], ['Jesse', 'de Jong'],
  ['Zoë', 'Willems'], ['Ömer', 'Demir'], ['Fleur', 'Kok'], ['Stijn', 'Meijer'],
];
/** Times tried in this order on a day, so a few slots fill up to their staffing. */
const PREFERRED = ['09:30', '11:00', '14:00', '10:00', '13:30', '15:00', '09:00', '16:00'];
const RATE_LIMIT = '30';
const BOOKING_LIMIT = 'Afspraken per IP-adres';

/** The made-up participant `index`, with an address at voorbeeld.nl. */
function person(index) {
  const [first, last] = NAMES[index];
  const local = `${first}.${last}`.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z.]/g, '');
  return { key: `sample-${String(index).padStart(2, '0')}`, first, last, email: `${local}@voorbeeld.nl` };
}

/** As the superuser, set the booking limit per address to the portal's ceiling. */
async function raiseBookingLimit(run) {
  const page = await run.staff('super');
  await page.goto('/admin/settings/values#topic-rate_limits');
  const field = page.getByLabel(BOOKING_LIMIT);
  const before = (await field.inputValue()).trim();
  const wanted = before ? before.replace(/^\d+/, RATE_LIMIT) : `${RATE_LIMIT}/hour`;
  if (before !== wanted) {
    await field.fill(wanted);
    await formOf(field).getByRole('button', { name: UI.save }).click();
    await page.waitForLoadState('load');
  }
  await page.goto('/admin/settings/values#topic-rate_limits');
  const after = await page.getByLabel(BOOKING_LIMIT).inputValue();
  if (!after.startsWith(RATE_LIMIT)) throw new Error(`the booking limit is still ${after}`);
  return `stond op "${before}", gezet op "${after}"`;
}

async function cancelByLink(run, key) {
  const page = await openManageLink(run, key);
  await openCancel(page);
  await confirmCancel(page);
  run.bookings[key].status = 'cancelled';
  run.save();
}

async function moveByLink(run, key, day) {
  const page = await openManageLink(run, key);
  await openMove(page);
  await pickNewMoment(page, day);
  await confirmMove(page);
  Object.assign(run.bookings[key], { status: 'moved', moved_to: day });
  run.save();
}

/** The planner cancels a booking in the agenda, for unforeseen circumstances. */
async function plannerCancel(run, key) {
  const page = await openPlannerForm(run, run.booking(key), UI.cancel, UI.cancelHeading);
  await chooseReason(page, /^Onvoorziene/);
  await page.getByRole('button', { name: UI.plannerCancel }).click();
  await page.waitForLoadState('load');
  run.bookings[key].status = 'cancelled_by_planner';
  run.save();
}

/** The planner moves a booking to a later free time on the same day. */
async function plannerMove(run, key) {
  const booking = run.booking(key);
  const page = await openPlannerForm(run, booking, UI.move, UI.moveHeading);
  const select = page.getByLabel(UI.newSlot);
  const values = await select.evaluate((s) => [...s.options].map((o) => o.value).filter(Boolean));
  const sameDay = values.filter((v) => v.startsWith(`${booking.day}T`) && v.slice(11, 16) > booking.time);
  if (!sameDay.length) throw new Error(`no later free time on ${booking.day} to move ${key} to`);
  await select.selectOption(sameDay[0]);
  await page.getByRole('button', { name: UI.plannerMove }).click();
  await page.waitForLoadState('load');
  Object.assign(run.bookings[key], { status: 'moved_by_planner', moved_to: sameDay[0].slice(11, 16) });
  run.save();
}

async function main() {
  const argv = process.argv.slice(2);
  let ws;
  let stack;
  let count = 18;
  try {
    ws = openWorkspace(argv[0]);
    const at = argv.indexOf('--count');
    if (at > 0) count = Number(argv[at + 1]);
    if (!Number.isInteger(count) || count < 1 || count > NAMES.length) throw new Error(`--count takes 1 to ${NAMES.length}`);
    stack = loadRun();
    await assertOurs(stack);
  } catch (error) {
    fail('sample-data.mjs', error.message);
  }
  const state = loadState(ws);
  if (state.installation && state.installation !== stack.instance) {
    fail('sample-data.mjs', `${ws.state} belongs to installation ${state.installation}; move it away before a run on a new stack`);
  }
  state.installation = stack.instance;
  const counts = { geboekt: 0, bevestigd: 0, onbevestigd: 0, geannuleerd: 0, verplaatst: 0, 'door de planner geannuleerd': 0, 'door de planner verplaatst': 0 };
  const browser = await launchBrowser();
  const run = new Run({ ws, stack, browser, state, hidden: hiddenTerms(ws.vars) });
  try {
    const limit = await raiseBookingLimit(run);
    const today = clock().day;
    const people = NAMES.slice(0, count).map((_, index) => person(index));
    for (const [index, p] of people.entries()) {
      const day = nextWeekday(today, 3 + Math.floor(index / 3));
      const offered = await freeTimes(run, day);
      const label = PREFERRED.find((t) => offered.includes(t)) ?? offered[0];
      if (!label) {
        console.log(`  --   ${p.key}: geen vrije tijd op ${day}`);
        continue;
      }
      if (index % 8 === 7) {
        await book(run, p.key, p, day, label);
        counts.onbevestigd += 1;
      } else {
        await bookAndConfirm(run, p.key, p, day, label);
        counts.bevestigd += 1;
      }
      counts.geboekt += 1;
      console.log(`  ok   ${p.key}: ${p.first} ${p.last}, ${day} ${label}`);
    }
    for (const [index, p] of people.entries()) {
      const booking = run.bookings[p.key];
      if (booking?.status !== 'confirmed') continue;
      if (index % 6 === 2) {
        await cancelByLink(run, p.key);
        counts.geannuleerd += 1;
      } else if (index % 6 === 4) {
        await moveByLink(run, p.key, nextWeekday(booking.day, 2));
        counts.verplaatst += 1;
      } else if (index % 12 === 6) {
        await plannerCancel(run, p.key);
        counts['door de planner geannuleerd'] += 1;
      } else if (index % 12 === 9) {
        await plannerMove(run, p.key);
        counts['door de planner verplaatst'] += 1;
      }
    }
    const summary = Object.entries(counts).map(([k, n]) => `${n} ${k}`).join(', ');
    run.notePrep('4', '4', `Voorbeeldgegevens geladen via de echte paden, op de dag van de opname: ${summary}. Verzonnen `
      + 'deelnemers (@voorbeeld.nl), op de werkdagen vanaf de derde werkdag na vandaag; vandaag en de twee dagen van het '
      + `verhaal bleven vrij. Boekingslimiet per adres als \`super\` via **Geavanceerde instellingen → Instellingen** (${limit}).`);
    console.log(`sample-data.mjs: ${summary}`);
  } finally {
    await browser.close();
  }
  return 0;
}

process.exit(await main());
