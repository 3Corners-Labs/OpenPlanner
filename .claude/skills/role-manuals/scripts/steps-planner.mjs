/**
 * The planner's part of the story: two bookings in one slot, the planner lowers the staffing so
 * the slot gets a shortage and asks one participant for a new time; the agenda, the week and a
 * booking's details; then the planning screens.
 */
import { agendaRow } from '../../verify-openplanner/scripts/stack-client.mjs';
import { dutchDate, stamp } from './evidence.mjs';
import { addException } from './steps-prepare.mjs';
import { Full, Region, Top } from './shots.mjs';
import { A, GROUP, PEOPLE, ROLES, Skip, action, bookAndConfirm, openPlannerForm, pick, screen, step } from './story.mjs';

const { sanne, ahmed, pieter } = PEOPLE;
const clean = (text) => text.replace(/\s+/g, ' ').trim();

/** The planner lowers the staffing of the second day to one agent: the 10:00 slot is short. */
async function shortage(run) {
  if (run.state.shortage) return;
  const day = run.days().second;
  await addException(await run.staff('groupadmin'), { date: day, from: '09:00', to: '12:00', agents: 1 });
  run.state.shortage = { day, block: '09:00–12:00', agents: 1, at: stamp() };
  run.notePrep('shortage', `Verhaal, als \`groupadmin\`: **Planning → Afwijkende bezetting**, datum ${dutchDate(day)}, `
    + 'eigen tijdblok 09:00–12:00 met 1 medewerker (aanbod Afspraken). Op die dag stonden om 10:00 al twee bevestigde '
    + 'afspraken (Pieter de Bakker en Sanne Jansen); het tijdslot krijgt daardoor een tekort van 1.');
}

/** The first day from the story's agenda day on whose agenda a slot is short. */
async function shortSlot(page, from) {
  for (let offset = 0; offset < 22; offset++) {
    const day = new Date(`${from}T12:00:00Z`);
    day.setUTCDate(day.getUTCDate() + offset);
    const iso = day.toISOString().slice(0, 10);
    await page.goto(`/admin/groups/${GROUP}/agenda?day=${iso}`);
    await page.locator('#agenda-heading').waitFor({ timeout: 15_000 });
    const found = page.locator('tbody', { has: page.locator('tr.agenda-slot--short') });
    if (await found.count()) return [iso, found.first()];
  }
  throw new Skip(`geen tijdslot met een tekort gevonden in de 3 weken vanaf ${from}`);
}

export const planner = [
  action('s-book-second-day-pieter', (run) => bookAndConfirm(run, 'pieter-second', pieter, run.days().second, '10:00')),
  action('s-book-second-day-sanne', (run) => bookAndConfirm(run, 'sanne-second', sanne, run.days().second, '10:00')),
  action('s-shortage-lower-staffing', shortage),
  step('pl-13-tekort', 'een tijdslot met **Tekort: 1** in de agenda', async (run, id) => {
    const page = await run.staff('groupadmin');
    const from = run.state.agenda_day ?? run.state.shortage?.day ?? run.days().first;
    const [day, slot] = await shortSlot(page, from);
    const chip = clean(await slot.locator('.chip--danger').first().innerText());
    const row = clean(await slot.locator('tr.agenda-slot--short').innerText());
    await run.shot(page, id, {
      role: ROLES.groupadmin,
      visible: `Agenda van ${dutchDate(day)}: het tijdslot "${row}", gemarkeerd met "${chip}", met de knoppen om de `
        + 'deelnemers om een nieuwe tijd te vragen of het tekort te accepteren.',
      crop: new Top(slot, 4),
    });
  }),
  step('pl-12-nieuwe-tijd', '**Om nieuwe tijd vragen**', async (run, id) => {
    const booking = run.booking('pieter-second');
    if (booking.status === 'waiting') {
      console.log('  ..   pl-12: de vraag om een nieuwe tijd is al verstuurd; formulier niet opnieuw op te nemen');
      return;
    }
    const page = await openPlannerForm(run, booking, A.rebookLink, A.rebookHeading);
    await pick(page, A.reason, A.reasonStaffing);
    await run.shot(page, id, {
      role: ROLES.groupadmin,
      visible: 'Het formulier "Om een nieuwe tijd vragen" voor de afspraak van Pieter in het tijdslot met tekort: uitleg '
        + 'wat er gebeurt (plek komt vrij, bericht met reden en link, herinnering, vervallen), de reden "Te weinig '
        + 'medewerkers" gekozen, en de knop "Om een nieuwe tijd vragen".',
      crop: new Full(),
      notes: ['Daarna verstuurd met "Om een nieuwe tijd vragen"; de afspraak staat sindsdien in Wacht op nieuwe tijd (pl-17).'],
    });
    await page.getByRole('button', { name: A.rebookButton }).click();
    await page.waitForLoadState('load');
    Object.assign(booking, { status: 'waiting', rebook_asked_at: stamp() });
    run.save();
  }),
  screen({
    id: 'pl-17-wacht-op-nieuwe-tijd',
    title: 'de lijst **Wacht op nieuwe tijd**',
    user: 'groupadmin',
    path: `/admin/groups/${GROUP}/agenda/waiting`,
    visible: 'De lijst "Wacht op nieuwe tijd" met de afspraak van Pieter: afspraaknummer, oorspronkelijke tijd, reden, '
      + 'vervaldatum en de acties Verplaatsen en Annuleren.',
    crop: () => new Full(),
  }),
  step('pl-10-verplaatsen', '**Verplaatsen** door de planner: tijdslot kiezen', async (run, id) => {
    const page = await openPlannerForm(run, run.booking('sanne-second'), A.moveLink, A.moveHeading);
    const chosen = await pick(page, A.newSlot, /\d\d:\d\d/, '11:00');
    await run.shot(page, id, {
      role: ROLES.groupadmin,
      visible: 'Het formulier "Afspraak … verplaatsen" van de planner voor de afspraak van Sanne: de vrije tijdsloten van '
        + `de groep, gekozen: ${chosen}. Niet uitgevoerd.`,
      crop: new Full(),
      notes: ['Niet verplaatst: de opname laat alleen het kiezen van een tijdslot zien.'],
    });
  }),
  step('pl-11-annuleren', '**Annuleren** door de planner: de vaste redenen', async (run, id) => {
    const page = await openPlannerForm(run, run.booking('sanne-second'), A.cancelLink, A.cancelHeading);
    await run.shot(page, id, {
      role: ROLES.groupadmin,
      visible: 'Het formulier "Afspraak … annuleren" van de planner: de vaste redenen (Te weinig medewerkers, Technische '
        + 'storing, Onvoorziene omstandigheden) en de knop "Afspraak annuleren". Niet uitgevoerd.',
      crop: new Full(),
      notes: ['Niet geannuleerd: de opname laat alleen het formulier zien.'],
    });
  }),
  step('pl-07-agenda-dag', '**Agenda**, dagoverzicht met bevestigde afspraken', async (run, id) => {
    const day = run.state.agenda_day ?? run.days().first;
    const page = await run.staff('groupadmin');
    await page.goto(`/admin/groups/${GROUP}/agenda?day=${day}`);
    await page.locator('#agenda-heading').waitFor({ timeout: 15_000 });
    const booked = page.locator('table.agenda-table tbody').filter({ has: page.getByRole('link', { name: A.details }) });
    if (!(await booked.count())) throw new Skip(`de agenda van ${day} heeft geen afspraken`);
    await run.shot(page, id, {
      role: ROLES.groupadmin,
      visible: `Agenda, dagoverzicht van ${dutchDate(day)}: per tijdslot de bezetting en de afspraken met de deelnemer `
        + '(voornaam), de videomeeting, of er een medewerker binnen is en de acties.'
        + (day === run.days().first ? ' Bevestigd: 09:00 Ahmed en 11:30 Sanne (verplaatst); met het label Geannuleerd: '
          + '10:00 Sanne (het oude moment) en 14:00 Pieter.' : ''),
      crop: new Top(booked.last(), 4),
    });
  }, { wide: true }),
  step('pl-08-agenda-week', '**Weekoverzicht**', async (run, id) => {
    const day = run.state.agenda_day ?? run.days().first;
    const page = await run.staff('groupadmin');
    await page.goto(`/admin/groups/${GROUP}/agenda/week?week=${day}`);
    await page.waitForLoadState('load');
    const noon = page.getByRole('row').filter({ has: page.locator('th', { hasText: /^\s*11:30–/ }) });
    await run.shot(page, id, {
      role: ROLES.groupadmin,
      visible: `Het weekoverzicht van de week van ${dutchDate(day)} (de ochtend, 09:00–12:00): per dag en tijdslot de `
        + 'bezetting en het aantal afspraken als link naar die dag; de tweede dag 1 medewerker door de afwijkende bezetting. '
        + 'Boven de tabel de melding dat elk tijdslot genoeg medewerkers heeft (het tekort is weg sinds de planner Pieter om '
        + 'een nieuwe tijd vroeg, pl-12).',
      crop: new Top(noon.first(), 4),
    });
  }, { wide: true }),
  step('pl-09-afspraak-details', '**Details** van een afspraak (verloop, achtergrondtaken)', async (run, id) => {
    const booking = run.booking('ahmed-first');
    const page = await run.staff('groupadmin');
    await page.goto(`/admin/groups/${GROUP}/agenda?day=${booking.day}`);
    await (await agendaRow(page, ahmed.first, booking.time)).first().getByRole('link', { name: A.details }).click();
    await page.waitForLoadState('load');
    const jobs = page.getByRole('region', { name: A.jobs }).first();
    const rows = (await jobs.locator('tbody tr').allInnerTexts()).map(clean).join('; ');
    await run.shot(page, id, {
      role: ROLES.groupadmin,
      visible: `De details van de afspraak van Ahmed (${dutchDate(booking.day)} ${booking.time}): afspraaknummer, moment en `
        + 'soort, de stand van het videogesprek, de antwoorden op het formulier, het verloop ("Nog niets gebeurd.") en de '
        + `achtergrondtaken: ${rows}.`,
      crop: new Top(jobs),
      notes: [
        'Het verloop toont de gebeurtenissen van het videogesprek; zonder Webex Meetings is het leeg ("Nog niets gebeurd.").',
        'Het laatste blok van de pagina, de verzoeken aan de koppelingen, valt onder de uitsnede.',
      ],
    });
  }),
];

export const plannerScreens = [
  screen({
    id: 'pl-01-groep-overzicht',
    title: '**Groepen → demo → Overzicht**',
    user: 'groupadmin',
    path: `/admin/groups/${GROUP}`,
    visible: 'Groepen → demo → Overzicht: de kop van de groep met de tabbladen, "Klaar om te boeken" (wat nog te regelen '
      + 'is) en "Instellingen" van de groep (duur, aanlooptijd, vooruit boeken, hoe afspraken bij medewerkers komen: de '
      + 'eigen wachtrij, agenda-uitnodiging, telefoonnummer, videogesprek, direct gesprek).',
    crop: (page) => new Top(page.getByRole('region', { name: A.groupSettings })),
    notes: ['Daaronder volgen Bezetting per week, Komende afwijkende bezetting en Zo zien deelnemers het (weggesneden).'],
  }),
  screen({
    id: 'pl-02-bezetting',
    title: '**Planning → Bezetting**: de week met tijdblokken',
    user: 'groupadmin',
    path: `/admin/groups/${GROUP}/staffing`,
    visible: 'Planning → Bezetting: "Bezetting per week", per dag de tijdblokken (09:00–12:00 en 13:00–17:00, 2 '
      + 'medewerkers) met Wijzigen, Splitsen en Verwijderen.',
    crop: (page) => new Top(page.getByRole('region', { name: A.staffingWeek })),
  }),
  step('pl-03-tijdblok-toevoegen', 'het formulier **Tijdblok toevoegen**, ingevuld vóór opslaan', async (run, id) => {
    const page = await run.staff('groupadmin');
    await page.goto(`/admin/groups/${GROUP}/staffing`);
    const form = page.getByRole('form', { name: A.addBlockForm });
    await pick(form, A.weekday, A.saturday);
    await form.getByLabel(A.from).fill('10:00');
    await form.getByLabel(A.to).fill('13:00');
    await form.getByLabel(A.capacity).fill('2');
    await pick(form, A.offer, A.offerScheduled);
    await run.shot(page, id, {
      role: ROLES.groupadmin,
      visible: 'Het formulier "Tijdblok toevoegen" ingevuld vóór opslaan: zaterdag 10:00–13:00, 2 medewerkers, aanbod '
        + 'Afspraken, label en geldigheid leeg.',
      crop: new Region(form, { margin: 16, fullWidth: false }),
      notes: ['Niet opgeslagen: de weekbezetting is ongewijzigd. Uitsnede: alleen het formulier (het staat onder de '
        + 'weekbezetting van pl-02).'],
    });
  }),
  step('pl-04-splitsen', 'het formulier **Splitsen** van een tijdblok (bijvoorbeeld lunch, 0 medewerkers)', async (run, id) => {
    const page = await run.staff('groupadmin');
    await page.goto(`/admin/groups/${GROUP}/staffing`);
    await page.getByRole('link', { name: A.splitLink({ day: 'maandag', time: '13:00–17:00' }) }).click();
    const form = page.getByRole('form', { name: A.splitForm });
    await form.waitFor({ timeout: 15_000 });
    await form.getByLabel(A.splitAt).fill('16:00');
    await form.getByLabel(A.capacity).fill('0');
    await form.getByLabel(A.label).fill('Teamoverleg');
    await run.shot(page, id, {
      role: ROLES.groupadmin,
      visible: 'Het formulier "Tijdblok splitsen" voor maandag 13:00–17:00 (2 medewerkers), ingevuld: splitsen om 16:00, '
        + 'tweede deel 0 medewerkers met het label Teamoverleg.',
      crop: new Full(),
      notes: ['Niet opgeslagen. Voorbeeld Teamoverleg in plaats van lunch: de demobezetting heeft de lunch al als gat '
        + 'tussen 12:00 en 13:00.'],
    });
  }),
  screen({
    id: 'pl-06-feestdagen',
    title: '**Planning → Feestdagen**',
    user: 'groupadmin',
    path: `/admin/groups/${GROUP}/holidays`,
    visible: 'Planning → Feestdagen van de groep.',
    crop: () => new Full(),
  }),
  screen({
    id: 'pl-14-noodsluiting',
    title: '**Noodsluiting** van de groep: het formulier, niet uitgevoerd',
    user: 'groupadmin',
    path: `/admin/groups/${GROUP}/closure`,
    visible: 'Noodsluiting van de groep: het formulier "Team noodsluiten" (begin, einde, toelichting voor deelnemers in het '
      + 'Nederlands en Engels, kop) met het aantal geplande afspraken; niet uitgevoerd.',
    crop: (page) => new Top(page.getByRole('form', { name: A.closeTeam })),
    notes: ['Niet uitgevoerd.'],
  }),
  step('pl-15-gebruik', '**Overzicht → Gebruik** (rapport van de groep)', async (run, id) => {
    const page = await run.staff('groupadmin');
    await page.goto(`/admin/groups/${GROUP}/usage`);
    const first = page.getByRole('region', { name: A.bookingsPerDay }).first();
    await run.shot(page, id, {
      role: ROLES.groupadmin,
      visible: 'Groepen → demo → Overzicht → Gebruik: het gebruiksrapport van de groep met de periode (Van, Tot, Tonen), de '
        + 'regel dat aantallen onder 5 verborgen blijven ("< 5"), en het eerste rapport: Boekingen gemaakt per dag, met '
        + 'vandaag "voorlopig".',
      crop: new Top(first),
      notes: ['Daaronder volgen nog zes rapporten (hoe boekingen eindigden, vooruit boeken, annuleringen, verplaatsingen, '
        + 'bevestiging per e-mail, noodsluitingen), weggesneden.'],
    });
  }),
  screen({
    id: 'pl-16-berichtteksten',
    title: '**Berichten → Berichtteksten**',
    user: 'groupadmin',
    path: `/admin/groups/${GROUP}/messages`,
    visible: 'Berichten → Berichtteksten van de groep (Nederlands): de teksten van de berichten aan deelnemers, per bericht '
      + 'aan te passen.',
    crop: () => new Top(),
  }),
];
