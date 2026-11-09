/**
 * The participant's part of the story: Sanne Jansen books, confirms by the emailed link, opens
 * her appointment and moves it; Pieter de Bakker cancels his; the emails as they arrive; the
 * group page on a phone.
 */
import { NL, SUBJECT, chooseDay, h1, mailMessage, waitForMail, waitH1 } from '../../verify-openplanner/scripts/stack-client.mjs';
import { dutchDate, stamp } from './evidence.mjs';
import { Full, Viewport } from './shots.mjs';
import {
  GROUP, PEOPLE, Skip, action, bookAndConfirm, confirmByLink, confirmCancel, confirmMove, fillContact, openCancel,
  openManageLink, openMove, pickNewMoment, recordBooking, seenMail, step, submitBooking, timeButton,
} from './story.mjs';

const { sanne, ahmed, pieter } = PEOPLE;
const AS_SANNE = 'deelnemer, via de link in de bevestigingsmail (Sanne Jansen)';
const AS_PIETER = 'deelnemer, via de link in de bevestigingsmail (Pieter de Bakker)';

/** Remember which messages `person` had before an action, under `key`. */
async function rememberMail(run, key, person) {
  run.state.seen ??= {};
  run.state.seen[key] = [...(await seenMail(run, person))];
  run.save();
}

/** The message a booking keeps under `field`, or the next one to `person` matching `subject`. */
async function mailOf(run, booking, field, person, subject, seenKey, extra = []) {
  if (!booking[field]) {
    const seen = new Set([...(run.state.seen?.[seenKey] ?? []), ...extra]);
    booking[field] = (await waitForMail(run.stack, seen, subject, person.email)).ID;
    run.save();
  }
  return mailMessage(run.stack, booking[field]);
}

/** The participant's page must still show `heading` from the step before. */
async function stillOn(run, person, heading, stepBefore) {
  const page = await run.participant(person);
  if ((await h1(page)) !== heading) throw new Skip(`de pagina van ${stepBefore} staat niet open`);
  return page;
}

export const participant = [
  step('bb-01-groepspagina', '`/g/demo`: titel en maandkalender met vrije dagen', async (run, id) => {
    const page = await run.participant(sanne, { fresh: true });
    await page.goto(`/g/${GROUP}`);
    await waitH1(page, NL.groupName);
    await page.locator('#times .time-button').first().waitFor({ timeout: 15_000 });
    await run.shot(page, id, {
      role: 'deelnemer, niet ingelogd (Sanne Jansen opent de pagina)',
      visible: 'De groepspagina: organisatienaam en taalkeuze bovenaan, de titel "Demo: videogesprek", de stappen Dag en '
        + 'tijd · Uw gegevens · Bevestigd, "Eerst mogelijk" met de knop "Kies dit moment", de maandkalender met de vrije '
        + 'dagen (vandaag door de afwijkende bezetting, en de werkdagen) en rechts de vrije tijden van de eerste vrije dag. '
        + 'Geen voettekst met de productnaam (instelling uit).',
      crop: new Full(),
    });
  }),
  step('bb-02-tijden', 'dezelfde pagina na het kiezen van een dag: de vrije tijden', async (run, id) => {
    const { first } = run.days();
    const page = await run.participant(sanne);
    if (!page.url().includes(`/g/${GROUP}`)) await page.goto(`/g/${GROUP}`);
    await chooseDay(page, first);
    const count = await page.locator('#times .time-button').count();
    await run.shot(page, id, {
      role: 'deelnemer, niet ingelogd (Sanne Jansen)',
      visible: `Dezelfde pagina na het kiezen van ${dutchDate(first)} in de kalender: rechts de ${count} vrije tijden van `
        + 'die dag, in Ochtend en Middag.',
      crop: new Full(),
    });
  }),
  step('bb-03-formulier', 'formulier **Uw gegevens** met het gekozen moment ernaast, leeg', async (run, id) => {
    const { first } = run.days();
    const page = await run.participant(sanne);
    if (!(await page.locator('#times .time-button').count())) {
      await page.goto(`/g/${GROUP}`);
      await chooseDay(page, first);
    }
    await (await timeButton(page, '10:00')).click();
    await waitH1(page, NL.formTitle);
    await run.shot(page, id, {
      role: 'deelnemer, niet ingelogd (Sanne Jansen)',
      visible: 'Het lege formulier "Uw gegevens" (voornaam, achternaam, e-mailadres, telefoonnummer optioneel, de eigen '
        + `vraag "Ik wil graag een tolk") met het gekozen moment (${dutchDate(first)}, 10:00) ernaast.`,
      crop: new Full(),
    });
  }),
  step('bb-04-foutmelding', 'formulier na **Afspraak maken** met een ongeldig e-mailadres: **Er is een probleem**', async (run, id) => {
    const page = await stillOn(run, sanne, NL.formTitle, 'bb-03');
    await fillContact(page, sanne, 'sanne.jansen.voorbeeld.nl');
    await page.getByRole('button', { name: NL.submit }).click();
    await page.getByRole('alert').first().waitFor({ timeout: 15_000 });
    await run.shot(page, id, {
      role: 'deelnemer, niet ingelogd (Sanne Jansen)',
      visible: 'Het formulier na "Afspraak maken" met het ongeldige e-mailadres "sanne.jansen.voorbeeld.nl" (zonder @): '
        + 'bovenaan "Er is een probleem" met een link naar het veld, het veld E-mailadres gemarkeerd met de foutmelding; '
        + 'voor- en achternaam blijven ingevuld.',
      crop: new Full(),
    });
  }),
  step('bb-05-controleer-mail', '**Bevestig uw afspraak** met "We hebben u een e-mail gestuurd"', async (run, id) => {
    const { first } = run.days();
    const page = await run.participant(sanne);
    await rememberMail(run, 'sanne', sanne);
    await page.getByLabel(NL.email).fill(sanne.email);
    await submitBooking(page);
    recordBooking(run, 'sanne-first', sanne, { day: first, time: '10:00', status: 'pending', booked_at: stamp() });
    await run.shot(page, id, {
      role: 'deelnemer, niet ingelogd (Sanne Jansen)',
      visible: 'De pagina "Bevestig uw afspraak" met de melding "We hebben u een e-mail gestuurd": de afspraak is gemaakt '
        + 'en wacht op bevestiging via de link in de e-mail.',
      crop: new Full(),
    });
  }),
  step('bb-06-mail-bevestigen', 'de e-mail "Bevestig uw afspraak met …", zoals de deelnemer hem ziet (HTML-deel)', async (run, id) => {
    const booking = run.booking('sanne-first');
    const message = await mailOf(run, booking, 'confirm_request_mail', sanne, SUBJECT.confirmRequest, 'sanne');
    await run.emailShot(id, message, `De e-mail "${message.Subject}" aan Sanne Jansen, het HTML-deel: aanhef, uitleg, `
      + 'wanneer en team, en de link om de afspraak te bevestigen (als linktekst; de link zelf staat niet in beeld).');
  }),
  step('bb-07-bevestigpagina', 'de pagina achter de link: knop **Afspraak bevestigen**', async (run, id) => {
    const booking = run.booking('sanne-first');
    const page = await run.participant(sanne);
    const seen = new Set([...(run.state.seen?.sanne ?? [])]);
    await confirmByLink(run, page, sanne, seen, 'sanne-first');
    if (!booking.reference) throw new Error('no reference after the confirm link');
    await run.shot(page, id, {
      role: 'deelnemer, via de link in de e-mail (Sanne Jansen)',
      visible: 'De pagina achter de bevestigingslink: kop "Afspraak bevestigen", het moment en de knop "Afspraak '
        + 'bevestigen". Het adres bevat geen token meer (/b/m/<afspraaknummer>/confirm).',
      crop: new Full(),
    });
  }),
  step('bb-08-bevestigd', '**Uw afspraak is bevestigd**', async (run, id) => {
    const page = await stillOn(run, sanne, NL.confirm, 'bb-07');
    await page.getByRole('button', { name: NL.confirm }).click();
    await page.getByRole('status').filter({ hasText: NL.confirmed }).first().waitFor({ timeout: 20_000 });
    Object.assign(run.bookings['sanne-first'], { status: 'confirmed', confirmed_at: stamp() });
    run.save();
    await run.shot(page, id, {
      role: 'deelnemer, via de link in de e-mail (Sanne Jansen)',
      visible: 'Na "Afspraak bevestigen": de melding "Uw afspraak is bevestigd" boven de afspraakpagina.',
      crop: new Full(),
    });
  }),
  step('bb-09-mail-bevestiging', 'de e-mail "Bevestiging van uw videogesprek op …"', async (run, id) => {
    const booking = run.booking('sanne-first');
    const message = await mailOf(run, booking, 'confirmation_mail', sanne, SUBJECT.confirmation, 'sanne', [booking.confirm_request_mail]);
    await run.emailShot(id, message, `De e-mail "${message.Subject}" aan Sanne Jansen, het HTML-deel: wanneer, team en de `
      + 'link om de afspraak te bekijken of te wijzigen. Zonder Webex Meetings staat er geen link om deel te nemen of een '
      + 'proefgesprek te doen.');
  }),
  step('bb-10-uw-afspraak', '**Uw afspraak**: moment, groep, status, links om te wijzigen', async (run, id) => {
    const page = await openManageLink(run, 'sanne-first');
    await run.shot(page, id, {
      role: AS_SANNE,
      visible: 'De pagina "Uw afspraak": moment, groep, status en de links "Afspraak verplaatsen" en "Afspraak annuleren".',
      crop: new Full(),
    });
  }),
  action('s-book-first-day-pieter', (run) => bookAndConfirm(run, 'pieter-first', pieter, run.days().first, '14:00')),
  action('s-book-first-day-ahmed', (run) => bookAndConfirm(run, 'ahmed-first', ahmed, run.days().first, '09:00')),
  step('bb-11-verplaatsen', '**Afspraak verplaatsen** met de kalender', async (run, id) => {
    const page = await openManageLink(run, 'sanne-first');
    await openMove(page);
    await run.shot(page, id, {
      role: AS_SANNE,
      visible: 'De pagina "Afspraak verplaatsen" met de huidige afspraak en dezelfde kalender met vrije dagen en tijden als '
        + 'bij het boeken.',
      crop: new Full(),
    });
  }),
  step('bb-12-nieuw-moment', '**Nieuw moment bevestigen** (oud en nieuw moment)', async (run, id) => {
    const { first } = run.days();
    const page = await stillOn(run, sanne, NL.move, 'bb-11');
    await pickNewMoment(page, first, '11:30');
    await run.shot(page, id, {
      role: AS_SANNE,
      visible: `"Nieuw moment bevestigen": het oude moment (${dutchDate(first)} 10:00) en het nieuwe (${dutchDate(first)} `
        + '11:30), met de knop "Verplaatsen".',
      crop: new Full(),
    });
  }),
  step('bb-13-verplaatst', '**Uw afspraak is verplaatst**', async (run, id) => {
    const page = await stillOn(run, sanne, NL.moveConfirmTitle, 'bb-12');
    await rememberMail(run, 'sanne-move', sanne);
    const reference = await confirmMove(page);
    const old = run.bookings['sanne-first'];
    old.status = 'moved';
    recordBooking(run, 'sanne-first-moved', sanne, {
      day: old.day, time: '11:30', reference, status: 'confirmed', moved_from: old.reference, moved_at: stamp(),
    });
    await run.shot(page, id, {
      role: AS_SANNE,
      visible: 'Na "Verplaatsen": de melding "Uw afspraak is verplaatst" op de afspraakpagina, met het nieuwe moment en een '
        + 'nieuw afspraaknummer.',
      crop: new Full(),
    });
  }),
  step('bb-17-mail-verplaatst', 'de e-mail "Uw videogesprek is verplaatst naar …"', async (run, id) => {
    const message = await mailOf(run, run.booking('sanne-first-moved'), 'change_mail', sanne, SUBJECT.change, 'sanne-move');
    await run.emailShot(id, message, `De e-mail "${message.Subject}" aan Sanne Jansen, het HTML-deel: het nieuwe moment, `
      + 'het team en de link om de afspraak te bekijken of te wijzigen.');
  }),
  step('bb-14-annuleren', '**Afspraak annuleren** met **Ja, annuleren**', async (run, id) => {
    const page = await openManageLink(run, 'pieter-first');
    await openCancel(page);
    await run.shot(page, id, {
      role: AS_PIETER,
      visible: 'De pagina "Afspraak annuleren" met de afspraak en de knop "Ja, annuleren".',
      crop: new Full(),
    });
  }),
  step('bb-15-geannuleerd', '**Uw afspraak is geannuleerd**', async (run, id) => {
    const page = await stillOn(run, pieter, NL.cancel, 'bb-14');
    await rememberMail(run, 'pieter-cancel', pieter);
    await confirmCancel(page);
    Object.assign(run.bookings['pieter-first'], { status: 'cancelled', cancelled_at: stamp() });
    run.save();
    await run.shot(page, id, {
      role: AS_PIETER,
      visible: 'Na "Ja, annuleren": de melding "Uw afspraak is geannuleerd".',
      crop: new Full(),
    });
  }),
  step('bb-18-mail-geannuleerd', 'de e-mail "Uw videogesprek op … is geannuleerd"', async (run, id) => {
    const message = await mailOf(run, run.booking('pieter-first'), 'cancellation_mail', pieter, SUBJECT.cancellation, 'pieter-cancel');
    await run.emailShot(id, message, `De e-mail "${message.Subject}" aan Pieter de Bakker, het HTML-deel: welke afspraak is `
      + 'geannuleerd (wanneer, team) en de zin "U kunt altijd een nieuwe afspraak maken."; het HTML-deel heeft geen link.');
  }),
  step('bb-16-mobiel', '`/g/demo` op een telefoon (390 breed)', async (run, id) => {
    const page = await run.participant({ key: 'phone' }, { mobile: true, fresh: true });
    await page.goto(`/g/${GROUP}`);
    await waitH1(page, NL.groupName);
    await page.locator('#times .time-button').first().waitFor({ timeout: 15_000 });
    await run.shot(page, id, {
      role: 'deelnemer, niet ingelogd, op een telefoon (390 × 844, schaal 2)',
      visible: 'De groepspagina op een telefoon: titel, stappen, "Eerst mogelijk" en de maandkalender onder elkaar (het '
        + 'eerste scherm).',
      crop: new Viewport(),
    });
  }),
];
