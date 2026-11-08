/**
 * The staff screens of the story: the agent's queue with today's booking (picked up and released
 * again), the agent's own pages, the screens of the organisation admin, the superuser and the
 * compliance role, and the ids that need Webex Meetings.
 */
import { clock, hhmm } from './evidence.mjs';
import { Full, Region, Top } from './shots.mjs';
import { GROUP, NotYet, ORG, ROLES, Skip, screen, step, webex } from './story.mjs';

const QUEUE_ROWS = 'table.data-table tbody tr';
const clean = (text) => text.replace(/\s+/g, ' ').trim();

/** When today's booking shows in the queue as "Nog niet begonnen", in minutes since midnight. */
function queueWindow(run) {
  const [hour, minute] = run.booking('ahmed-today').time.split(':').map(Number);
  const start = hour * 60 + minute;
  return [start - Number(run.state.queue_visible_minutes ?? 15) + 0.5, start + 8];
}

async function openQueue(run) {
  const page = await run.staff('agent');
  await page.goto(`/admin/groups/${GROUP}/queue`);
  await page.getByRole('heading', { name: 'Wachtende afspraken' }).waitFor({ timeout: 15_000 });
  return page;
}

/** Pass when a booking is in the queue; else NotYet before the story's window, Skip after it. */
async function needQueue(run) {
  const page = await openQueue(run);
  if (await page.locator(QUEUE_ROWS).count()) return page;
  if (!run.bookings['ahmed-today']) {
    throw new Skip('er staat nu geen afspraak in de wachtrij (een afspraak verschijnt daar 15 minuten voor de start)');
  }
  const [opens, closes] = queueWindow(run);
  const now = clock().minutes;
  if (now < opens) throw new NotYet(opens, `de afspraak van vandaag staat pas vanaf ${hhmm(Math.ceil(opens))} in de wachtrij`);
  throw new Skip(`de afspraak van vandaag is al begonnen (venster tot ${hhmm(Math.floor(closes))}); boek een latere tijd van vandaag`);
}

export const agent = [
  step('mw-01-overzicht', '**Overzicht** zoals een medewerker het ziet (wachtrij vooraan)', async (run, id) => {
    const page = await run.staff('agent');
    await page.goto('/admin/');
    await page.getByRole('heading', { name: 'Wachtrij in mijn groepen' }).waitFor({ timeout: 15_000 });
    await run.shot(page, id, {
      role: ROLES.agent,
      visible: 'Het overzicht van de medewerker: vooraan "Wachtrij in mijn groepen" met per groep het aantal wachtende '
        + 'deelnemers, de langste wachttijd tegen het doel en het aantal aangemelde medewerkers; daarnaast Meldingen, '
        + 'daaronder Mijn rol en bereik en Uw groepen.',
    });
  }),
  step('mw-02-wachtrij', '**Groepen → demo → Agenda → Wachtrij** met de afspraak van vandaag, status **Nog niet begonnen**', async (run, id) => {
    const page = await needQueue(run);
    const rows = (await page.locator(QUEUE_ROWS).allInnerTexts()).map(clean).join('; ');
    await run.shot(page, id, {
      role: ROLES.agent,
      visible: 'Groepen → demo → Agenda → Wachtrij: "Wachtende afspraken" met de knoppen "Volgende oppakken" en Vernieuwen, '
        + `en de afspraken die nu in de wachtrij staan (tijd, voornaam, afspraaknummer) met hun status: ${rows}.`,
    });
  }),
  step('mw-03-opgepakt', 'na **Volgende oppakken**: de opgepakte afspraak met **Deelnemen aan het gesprek**', async (run, id) => {
    const page = await needQueue(run);
    await page.getByRole('button', { name: 'Volgende oppakken' }).click();
    await page.getByRole('heading', { name: /^U heeft .* opgepakt/ }).first().waitFor({ timeout: 15_000 });
    run.state.claimed = page.url();
    run.save();
    const canJoin = (await page.getByRole('link', { name: /Deelnemen aan het gesprek/ }).count()) > 0;
    await run.shot(page, id, {
      role: ROLES.agent,
      visible: 'Na "Volgende oppakken": "U heeft … opgepakt" met tijd en deelnemer, de status van de deelnemer, en de knop '
        + `"Vrijgeven".${canJoin ? '' : ' De knop "Deelnemen aan het gesprek" staat er niet: zonder Webex Meetings is er geen videolink.'}`,
    });
  }),
  step('mw-04-vrijgegeven', 'na **Vrijgeven**: de afspraak staat weer in de wachtrij', async (run, id) => {
    const page = await needQueue(run);
    if (!run.state.claimed) throw new Skip('er is geen opgepakte afspraak (mw-03)');
    await page.goto(run.state.claimed);
    await page.getByRole('button', { name: 'Vrijgeven' }).first().click();
    await page.waitForLoadState('load');
    await page.locator(QUEUE_ROWS).first().waitFor({ timeout: 15_000 });
    await run.shot(page, id, {
      role: ROLES.agent,
      visible: 'Na "Vrijgeven": de melding dat de afspraak is vrijgegeven en weer in de wachtrij staat, en de afspraak weer '
        + 'in de lijst met "Nog niet begonnen".',
    });
  }),
  screen({
    id: 'mw-05-mijn-rechten',
    title: '**Mijn rechten** van de medewerker',
    user: 'agent',
    path: '/admin/rights',
    visible: 'Mijn rechten van de medewerker: de rol Medewerker voor groep demo en wat die rol mag.',
    crop: () => new Full(),
  }),
  step('mw-06-profielmenu', 'het geopende profielmenu (weergave, taal, meldingen, uitloggen)', async (run, id) => {
    const page = await run.staff('agent');
    await page.goto('/admin/');
    await page.locator('details.admin-profile > summary').click();
    await page.locator('.admin-profile__panel').waitFor({ timeout: 5000 });
    await run.shot(page, id, {
      role: ROLES.agent,
      visible: 'Het geopende profielmenu rechtsboven: naam en rol, link naar Mijn rechten, Weergave (thema), Taal, Meldingen '
        + '(balk en pop-ups) en Uitloggen.',
    });
    await page.locator('details.admin-profile > summary').click();
  }),
  screen({
    id: 'mw-07-mijn-sessies',
    title: '**Mijn rechten → Mijn sessies**',
    user: 'agent',
    path: '/admin/rights/sessions',
    visible: 'Mijn rechten → Mijn sessies: de sessies van deze medewerker in het beheerportaal, met de huidige.',
    crop: () => new Full(),
  }),
];

const full = () => new Full();
const top = () => new Top();
const org = (tab) => `/admin/organisations/${ORG}${tab}`;

export const admin = [
  screen({
    id: 'bh-01-overzicht',
    title: '**Overzicht** (`super`): widgets, meldingen, systeemstatus',
    user: 'super',
    path: '/admin/',
    visible: 'Het overzicht van de applicatiebeheerder: widgets Meldingen, Wachtrij in mijn groepen, Afspraken vandaag per '
      + 'groep, Recente configuratiewijzigingen, Lopende taken en Systeemstatus.',
    crop: (page) => new Top(page.getByRole('region', { name: 'Systeemstatus' })),
  }),
  screen({
    id: 'bh-02-organisatie',
    title: '**Organisaties → demo → Overzicht**',
    user: 'orgadmin',
    path: org(''),
    visible: 'Organisaties → Demo-acme → Overzicht: de kop van de organisatie met tabbladen en het overzicht.',
  }),
  screen({
    id: 'bh-03-org-instellingen',
    title: '**Overzicht → Instellingen** van de organisatie',
    user: 'orgadmin',
    path: org('/settings'),
    visible: 'Overzicht → Instellingen van de organisatie: Bewaartermijnen, Weergave (tijdnotatie, tijdzone, de '
      + 'voettekstinstelling op Uit), Medewerkers, Planner, Boekingsformulier en Afspraken bevestigen, met bij elke waarde '
      + 'de bron.',
    crop: full,
  }),
  screen({
    id: 'bh-04-teksten',
    title: '**Deelnemers → Teksten** van de organisatie',
    user: 'orgadmin',
    path: org('/texts'),
    visible: "Deelnemers → Teksten van de organisatie (Nederlands): de teksten op de deelnemerspagina's, aan te passen per "
      + 'organisatie.',
    crop: top,
  }),
  screen({
    id: 'bh-05-huisstijl',
    title: '**Deelnemers** (huisstijl) van de organisatie (`super`)',
    user: 'super',
    path: org('/branding'),
    visible: 'Organisaties → Demo-acme → Deelnemers → Huisstijl: het voorbeeld van een kaart zoals deelnemers hem zien, en '
      + 'het formulier met naam in de kop, kop, accentkleur, kleuren, tekstgrootte, regelafstand, afronding, schaduw en '
      + 'Opslaan.',
    crop: (page) => new Top(page.getByRole('button', { name: 'Opslaan' }), 32),
    notes: ['Daaronder volgen Logo, Lettertypen en Themabestand (uploads), weggesneden.'],
  }),
  screen({
    id: 'bh-06-kanalen',
    title: '**Deelnemers → Kanalen** (`super`)',
    user: 'super',
    path: org('/channels'),
    visible: 'Deelnemers → Kanalen van de organisatie: e-mail (en sms) voor berichten aan deelnemers.',
    crop: top,
  }),
  screen({
    id: 'bh-07-groepen',
    title: '**Groepen**: de lijst',
    user: 'orgadmin',
    path: '/admin/groups',
    visible: 'Groepen: de lijst met de groep Demo: videogesprek van Demo-acme.',
  }),
  screen({
    id: 'bh-08-groep-nieuw',
    title: '**Nieuwe groep**: het formulier, niet opgeslagen',
    user: 'orgadmin',
    path: '/admin/groups/new',
    visible: 'Nieuwe groep: het lege formulier, niet opgeslagen.',
    crop: full,
    notes: ['Niet opgeslagen.'],
  }),
  screen({
    id: 'bh-09-groepsinstellingen',
    title: '**Groepen → demo → Instellingen**, met de eigen wachtrij gekozen',
    user: 'orgadmin',
    path: `/admin/groups/${GROUP}/settings`,
    visible: 'Groepen → demo → Instellingen: de algemene instellingen van de groep, met bij "Hoe afspraken bij medewerkers '
      + 'komen" de eigen wachtrij gekozen en de velden van de eigen wachtrij.',
    crop: (page) => new Top(page.locator('details').filter({ hasText: /Medewerkers mogen zelf/ }), 24),
    notes: ['Bijgesneden onder de laatste velden van de eigen wachtrij en het telefoonnummer; daaronder volgen de '
      + 'koppelingen, de koppeling met de identiteitsprovider, het boekingsformulier en archiveren.'],
  }),
  screen({
    id: 'bh-10-boekingsformulier',
    title: '**Instellingen → Boekingsformulier**',
    user: 'orgadmin',
    path: `/admin/groups/${GROUP}/settings/form`,
    visible: 'Instellingen → Boekingsformulier: de eigen vragen van de groep (met de uitgezette onderwerpvraag) en de '
      + 'soorten velden om toe te voegen.',
    crop: top,
  }),
  screen({
    id: 'bh-11-herinneringen',
    title: '**Berichten** (herinneringen)',
    user: 'orgadmin',
    path: `/admin/groups/${GROUP}/reminders`,
    visible: 'Berichten van de groep: de herinneringen (e-mail 24 uur vooraf) en een herinnering toevoegen.',
    crop: full,
  }),
  screen({
    id: 'bh-12-medewerkers',
    title: '**Medewerkers**: de lijst',
    user: 'orgadmin',
    path: '/admin/staff',
    visible: 'Medewerkers: de knop Medewerker uitnodigen, de lijst "Uitgenodigd, nog niet ingelogd" (twee '
      + 'ontwikkelaccounts), de filters en de tabel Medewerkers met naam, rollen, organisatie, groepen, inlogdienst en '
      + 'laatste login, actief en afgemeld.',
    crop: (page) => new Top(page.getByRole('table', { name: 'Medewerkers' })),
  }),
  step('bh-13-medewerker', 'de pagina van één medewerker', async (run, id) => {
    const page = await run.staff('orgadmin');
    await page.goto('/admin/staff');
    await page.getByRole('link', { name: /Mo Medewerker/ }).first().click();
    await page.waitForLoadState('load');
    await run.shot(page, id, {
      role: ROLES.orgadmin,
      visible: 'De pagina van één medewerker (Mo Medewerker): gegevens uit de identiteitsprovider, rollen en groepen, en wat '
        + 'de beheerder hier kan doen.',
      crop: new Full(),
    });
  }),
  screen({
    id: 'bh-14-rechtenmatrix',
    title: '**Rechtenmatrix**',
    user: 'orgadmin',
    path: '/admin/matrix',
    visible: 'De rechtenmatrix: per recht welke rol het heeft en op welk niveau (het begin van de tabel).',
    wide: true,
  }),
  screen({
    id: 'bh-15-eigen-rol',
    title: '**Rechtenmatrix → Rollen aanpassen → Nieuwe rol** (`super`), niet opgeslagen',
    user: 'super',
    path: '/admin/roles/new',
    visible: 'Rechtenmatrix → Rollen aanpassen → Nieuwe rol: "Nieuwe rol maken" (kopie van Organisatiebeheerder) met de '
      + 'namen in het Nederlands en Engels, de waarde in de identity provider, de uitleg over het toekennen, en het begin '
      + 'van de lijst Rechten; niet opgeslagen.',
    crop: (page) => new Top(page.getByRole('button', { name: 'Rol maken' })),
    notes: ['Niet opgeslagen.'],
  }),
  screen({
    id: 'bh-16-mijn-rechten',
    title: '**Mijn rechten** van de organisatiebeheerder',
    user: 'orgadmin',
    path: '/admin/rights',
    visible: 'Mijn rechten van de organisatiebeheerder: de rol Organisatiebeheerder voor organisatie demo en de rechten die '
      + 'daarbij horen.',
    crop: top,
  }),
  screen({
    id: 'bh-17-auditlog',
    title: '**Auditlog**',
    user: 'orgadmin',
    path: '/admin/audit',
    visible: 'Het auditlog van de organisatie: filters en de gebeurtenissen van deze opname (logins, wijzigingen, berichten).',
    wide: true,
  }),
  screen({
    id: 'bh-18-bewaartermijnen',
    title: '**Bewaartermijnen**',
    user: 'orgadmin',
    path: '/admin/retention',
    visible: 'Bewaartermijnen: wanneer welke gegevens worden verwijderd.',
    crop: full,
  }),
  screen({
    id: 'bh-19-insluiten',
    title: '**Overzicht → Insluiten** van de organisatie',
    user: 'orgadmin',
    path: org('/embedding'),
    visible: 'Overzicht → Insluiten (Inbedding) van de organisatie: de toegestane websites voor de widget en de code om hem '
      + 'in te sluiten.',
    crop: full,
  }),
  screen({
    id: 'bh-20-feestdagen-org',
    title: '**Feestdagen** van de organisatie',
    user: 'orgadmin',
    path: org('/holidays'),
    visible: 'Feestdagen van de organisatie: welke feestdagkalender de organisatie volgt.',
    crop: full,
  }),
  screen({
    id: 'bh-21-noodsluiting-org',
    title: '**Noodsluiting** van de organisatie: het formulier, niet uitgevoerd',
    user: 'orgadmin',
    path: org('/closure'),
    visible: 'Noodsluiting van de organisatie: het formulier om alle groepen tegelijk te sluiten; niet uitgevoerd.',
    crop: full,
    notes: ['Niet uitgevoerd.'],
  }),
  step('bh-22-integraties', '**Integraties** (`super`): alleen het deel van Webex Meetings', async (run, id) => {
    const page = await run.staff('super');
    await page.goto('/admin/integrations');
    const section = page.getByRole('region', { name: 'Webex Service App' });
    if (!(await section.count())) throw new Skip('geen onderdeel Webex Service App op de pagina Koppelingen');
    const keep = section.locator('dt').filter({ hasText: 'Verversingstoken geldig tot' }).locator('xpath=..');
    await run.shot(page, id, {
      role: ROLES.super,
      visible: 'Koppelingen (in het menu Platform), alleen het onderdeel "Webex Service App": de koppeling met Webex voor de '
        + 'videogesprekken (Service App: Niet gekoppeld; toegangs- en verversingstoken: Nog niet gebruikt).',
      crop: new Region(section, { bottom: keep, margin: 10, fullWidth: false }),
      notes: ['Uitsnede: alleen de kaart Webex Service App, tot en met het verversingstoken; de rest van de kaart en van de '
        + 'pagina vallen buiten de uitsnede. Een apart onderdeel "Webex Meetings" heeft deze pagina niet; de Service App is '
        + 'de Webex-koppeling voor de videogesprekken.'],
    });
  }),
  screen({
    id: 'bh-23-systeemstatus',
    title: '**Geavanceerde instellingen** (`super`): systeemstatus',
    user: 'super',
    path: '/admin/settings',
    visible: 'Geavanceerde instellingen → Systeemstatus: de omgeving, logbestemmingen, medewerkerbeheer en identity '
      + 'providers van organisaties (het begin van de pagina).',
    crop: top,
    notes: ['Verderop op de pagina (niet in de uitsnede): back-ups, gebruiksrapportage, adres en proxy, het certificaat en '
      + 'de lijst variabelen.'],
  }),
  screen({
    id: 'bh-24-platforminstellingen',
    title: '**Geavanceerde instellingen → Instellingen** (`super`)',
    user: 'super',
    path: '/admin/settings/values',
    visible: 'Geavanceerde instellingen → Instellingen: de platforminstellingen per onderwerp, met waarde, bron en grenzen.',
    crop: top,
  }),
  screen({
    id: 'bh-25-updates',
    title: '**Updates** (`super`)',
    user: 'super',
    path: '/admin/updates',
    visible: 'Updates: de melding dat deze installatie niet naar nieuwe versies zoekt, het blok Versie (draait nu, commit, '
      + 'kanaal, lijn, updatevenster, ondersteuning, nieuwste versie, laatst gecontroleerd) en de Geschiedenis (nog geen '
      + 'updates).',
    crop: full,
  }),
  screen({
    id: 'bh-26-onderhoud',
    title: '**Onderhoud** (`super`)',
    user: 'super',
    path: '/admin/maintenance',
    visible: 'Onderhoud: de onderhoudsmodus aan- of uitzetten.',
    crop: full,
  }),
  screen({
    id: 'bh-27-logbestemmingen',
    title: '**Logbestemmingen** (`super`)',
    user: 'super',
    path: '/admin/logging',
    visible: 'Logbestemmingen: waar het log heen gaat (hier nog geen bestemming) en een bestemming toevoegen.',
    crop: full,
  }),
  screen({
    id: 'bh-28-idp-koppeling',
    title: '**Koppeling identiteitsprovider**',
    user: 'orgadmin',
    path: '/admin/idp-mapping',
    visible: 'Koppeling identiteitsprovider: welke waarden de identiteitsprovider moet meesturen, de instellingen '
      + '(provider, adres, medewerkers beheren, bron van de rechten, claim, voorvoegsel) en het begin van de waarden voor '
      + 'Demo-acme.',
    crop: top,
  }),
  screen({
    id: 'bh-29-inloggen-org',
    title: '**Overzicht → Inloggen** van de organisatie',
    user: 'orgadmin',
    path: org('/login'),
    visible: 'Overzicht → Inloggen van de organisatie: met welke identiteitsprovider de medewerkers inloggen.',
    crop: full,
  }),
  screen({
    id: 'bh-30-handleiding',
    title: '**Handleiding** in het portaal (ingebouwde hulp)',
    user: 'orgadmin',
    path: '/admin/help',
    visible: 'De ingebouwde handleiding in het portaal: de onderwerpen, één per scherm.',
  }),
  screen({
    id: 'bh-31-contactalternatieven',
    title: '**Contact** van de organisatie',
    user: 'orgadmin',
    path: org('/contacts'),
    visible: 'Contact van de organisatie: de contactalternatieven die deelnemers zien bij een sluiting (telefoon, website, '
      + 'adres).',
    crop: top,
  }),
  screen({
    id: 'bh-32-compliance-overzicht',
    title: '**Overzicht** zoals `auditor` (compliance en privacy) het ziet',
    user: 'auditor',
    path: '/admin/',
    visible: 'Het overzicht van compliance en privacy (alleen lezen): meldingen, configuratiewijzigingen en Mijn rol en '
      + 'bereik, zonder persoonsgegevens.',
  }),
];

export const needsWebex = [
  webex('bb-20-deelnemen', '**Testomgeving met Webex Meetings:** de deelnemer opent de link en komt in het gesprek'),
  webex('bb-21-proefgesprek', '**Testomgeving met Webex Meetings:** het proefgesprek om camera en microfoon te testen'),
  webex('mw-20-gesprek', '**Testomgeving met Webex Meetings:** de medewerker in het gesprek'),
];

