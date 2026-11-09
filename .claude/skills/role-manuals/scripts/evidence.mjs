/**
 * The evidence of the screenshots: the story state (bookings, preparation, one record per
 * capture), the clock of the group's time zone, and the evidence log the organisation reads.
 *
 * The state holds no booking-link token and no password. Observations about one installation
 * (a display fault seen on a screen, say) are not code: they live in the workspace's
 * evidence/notes.json ({"<id>": ["note", ...]}) and the log adds them to that capture.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

import { EMAIL_WIDTH, GREY, MOBILE, PAD, SCALE, VIEWPORT, WIDE, maskProduct } from './shots.mjs';

export const TZ = 'Europe/Amsterdam';

const clockParts = (date) => Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
  timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  second: '2-digit', hourCycle: 'h23', timeZoneName: 'short',
}).formatToParts(date).map((p) => [p.type, p.value]));

/** A moment as the log writes it: `YYYY-MM-DD HH:MM:SS <zone>` in the group's time zone. */
export function stamp(date = new Date()) {
  const p = clockParts(date);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second} ${p.timeZoneName}`;
}

/** Today in the group's time zone (`YYYY-MM-DD`) and the minutes since its midnight. */
export function clock(date = new Date()) {
  const p = clockParts(date);
  return { day: `${p.year}-${p.month}-${p.day}`, minutes: Number(p.hour) * 60 + Number(p.minute) + Number(p.second) / 60 };
}

/**
 * The years covered (rule 4 of redact.js) by a capture that started at `started` (a `stamp()`):
 * its year, the one before it (a capture early in January shows December) and five after it
 * (bookings two months ahead, holidays, retention and certificate dates). A number outside them,
 * such as a key size of 2048, stays. Taken from the start, so a rewritten log names the years
 * the capture covered; a start without a year throws, as rule 4 would otherwise cover nothing.
 */
export function coveredYears(started) {
  const year = Number(/^(\d{4})-/.exec(String(started ?? ''))?.[1]);
  if (!Number.isInteger(year)) throw new Error(`the capture's start ${JSON.stringify(started)} holds no year`);
  return Array.from({ length: 7 }, (_, i) => year - 1 + i);
}

/** The `n`-th weekday after the day `iso`. */
export function nextWeekday(iso, n = 1) {
  const day = new Date(`${iso}T12:00:00Z`);
  while (n > 0) {
    day.setUTCDate(day.getUTCDate() + 1);
    if (day.getUTCDay() % 6 !== 0) n -= 1;
  }
  return day.toISOString().slice(0, 10);
}

/** `YYYY-MM-DD` as the portal writes it: `dd-mm-jjjj`. */
export const dutchDate = (iso) => iso.split('-').reverse().join('-');

/** `HH:MM` of minutes since midnight. */
export const hhmm = (minutes) => `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;

export function loadState(ws) {
  return existsSync(ws.state) ? JSON.parse(readFileSync(ws.state, 'utf8')) : {};
}

export function saveState(ws, state) {
  mkdirSync(dirname(ws.state), { recursive: true });
  writeFileSync(ws.state, `${JSON.stringify(state, null, 2)}\n`);
}

const STATUS = {
  confirmed: 'bevestigd',
  moved: 'verplaatst (geannuleerd, nieuwe afspraak)',
  cancelled: 'geannuleerd door de deelnemer',
  cancelled_by_planner: 'geannuleerd door de planner',
  moved_by_planner: 'verplaatst door de planner',
  waiting: 'wacht op nieuwe tijd (planner vroeg erom)',
  pending: 'wacht op bevestiging',
};

const KIND = {
  product: 'productnaam',
  mark: 'beeldmerk',
  term: 'term die niet genoemd wordt',
  row: 'rij',
  column: 'kolom',
  field: 'veld',
  version: 'versie',
  'version-field': 'versieveld',
  year: 'jaartal',
};

/**
 * The preparation of the story and the changes it makes on the way, in the order the log and the
 * shot list describe them: key (the step records it with Run.notePrep), label, who and what.
 */
export const PREPARATION = [
  { key: '1', who: '`orgadmin`', what: 'de groep `demo` op de **eigen wachtrij** (groepsinstellingen)' },
  { key: '2', who: '`super`', what: "de voettekst met de productnaam op de deelnemerspagina's uit, via de instelling die daarvoor bestaat" },
  { key: '2b', who: '`orgadmin`', what: 'de onderwerpvraag van de demovulling uit het boekingsformulier, zodat het formulier alleen de tolkvraag toont' },
  { key: '3', who: '`groupadmin`', what: 'vandaag een **afwijkende bezetting** met een eigen tijdblok, zodat er vandaag geboekt kan worden (opname `pl-05`)' },
  { key: '4', who: '`sample-data.mjs`', what: 'voorbeeldgegevens: verzonnen deelnemers boeken, bevestigen, verplaatsen en annuleren op de werkdagen na vandaag, en de planner verplaatst en annuleert er een paar; de rapporten volgen met de telling van de worker (elk kwartier)' },
  { key: 'shortage', label: 'tekort', who: '`groupadmin`', what: 'tijdens het verhaal de bezetting van de tweede dag omlaag, zodat een tijdslot een tekort krijgt (opname `pl-13`)' },
];

/** Write the evidence log from the state, the shot list and the workspace's notes. */
export function writeLog({ ws, stack, state, steps, hidden }) {
  const notes = existsSync(ws.notes) ? JSON.parse(readFileSync(ws.notes, 'utf8')) : {};
  const version = state.version ?? {};
  const local = { yes: 'ja', no: 'nee' }[stack.build.localChanges] ?? stack.build.localChanges;
  const wide = steps.filter((s) => s.wide).map((s) => s.id).join(', ');
  const terms = hidden.terms.length ? hidden.terms.map((t) => `"${t}"`).join(', ') : '(geen)';
  const years = coveredYears(state.started);
  const lines = [
    '# Bewijslog schermafdrukken',
    '',
    'Elke afbeelding in `img/` komt uit de draaiende software hieronder, via echte gebruikerspaden. '
      + `Bewerkingen: alleen bijsnijden en een effen lichtgrijze balk (${GREY}), in de pagina getekend vlak voor `
      + `de opname, op het vak dat de pagina zelf voor dat stuk tekst of dat element opgeeft (plus ${PAD} px rand). `
      + 'Gemaakt met `capture.mjs` van de skill role-manuals.',
    '',
    '## Software en opname',
    '',
    `- Commit: \`${stack.build.commit}\` (gebouwd door de repo-skill verify-openplanner, run \`${stack.id}\`, `
      + `lokale wijzigingen: ${local}); image \`${stack.build.image}\`.`,
    `- Wat het portaal toont, **Updates**: Draait nu "${version.running ?? '?'}"; Commit "${version.commit ?? '?'}"; `
      + `Kanaal "${version.channel ?? '?'}". **Overzicht → Systeemstatus**: "${version.overview ?? '?'}". `
      + 'Versies zijn in de afbeeldingen afgedekt (afdekregel 3); de commit hierboven komt uit het bouwverslag van de run.',
    `- Installatie \`${stack.instance}\` op ${stack.web} (beheerportaal ${stack.web}/admin/, identiteitsprovider `
      + `${stack.keycloak}, Mailpit ${stack.mail}); gecontroleerd via \`/api/v1/instance\` vóór elke run.`,
    `- Opname gestart ${state.started ?? '?'}; laatst bijgewerkt ${stamp()}. Tijden in ${TZ}.`,
    `- Venster ${VIEWPORT.width} × ${VIEWPORT.height}, schaal ${SCALE} (afbeeldingen ${VIEWPORT.width * SCALE} px breed); `
      + `de brede overzichten ${wide} in ${WIDE.width} × ${WIDE.height} (afbeeldingen ${WIDE.width * SCALE} px breed), `
      + `zodat hun tabellen heel zijn; mobiel ${MOBILE.width} × ${MOBILE.height}, schaal ${SCALE}. E-mail: het HTML-deel `
      + `uit Mailpit, weergegeven op ${EMAIL_WIDTH} px breed, alleen het bericht. Licht thema, taal nl-NL, beweging beperkt.`,
    '- Bij een opname van een hele pagina of een bovenstuk is het venster even zo hoog gemaakt als de pagina '
      + '(de breedte blijft gelijk); daarna weer de vorige maat.',
    '- In het beheerportaal is de omgevingsbalk "Ontwikkelomgeving" bovenaan weggesneden (bijsnijden); hij staat '
      + 'alleen op een ontwikkel- of testinstallatie.',
    `- Afdekregel 1: ${hidden.product ? 'de productnaam (ook in kleine letters, in adressen en e-mailadressen) en het beeldmerk' : 'geen: de productnaam mag in beeld'}.`,
    `- Afdekregel 2: de termen die deze organisatie niet noemt (\`do_not_name\`): ${terms}, ook in samenstellingen; `
      + 'een rij, kolom of veld waarvan het label zo\'n term is, als geheel.',
    '- Afdekregel 3: geen leesbare versie: softwareversies (zoals 0.5.1, v0.5.1, 0.5.1-rc.2 en het label dev), '
      + '"Versie onbekend", "Geen release(versie)", commit-hashes, image-id\'s en digests, en de waarden van de '
      + 'versievelden Commit en Nieuwste versie.',
    `- Afdekregel 4: het jaartal (${years[0]} tot en met ${years.at(-1)}), in datums, koppen en datumvelden, zodat een `
      + 'afbeelding niet laat zien wanneer hij gemaakt is. Dag, maand en tijd blijven staan; er komt nooit een ander '
      + 'jaartal voor in de plaats. De echte datum van elke opname staat in dit log.',
    '- Verzonnen deelnemers met adressen op voorbeeld.nl; medewerkers zijn de ontwikkelgebruikers van de '
      + 'identiteitsprovider. Geen token of wachtwoord in dit log; van een e-mail staan hier de verzonnen '
      + 'ontvanger, het onderwerp, het verzendmoment, de namen van bijlagen en de regel rond een afdekking.',
    '',
    '## Voorbereiding en verhaal',
    '',
  ];
  const prep = state.prep ?? {};
  for (const { key, label } of PREPARATION.filter((p) => prep[p.key])) {
    const text = hidden.product ? maskProduct(prep[key].text) : prep[key].text;
    lines.push(`- **${label ?? key}** (${prep[key].at}): ${text}`);
  }
  lines.push('');
  const bookings = Object.entries(state.bookings ?? {});
  if (bookings.length) {
    lines.push(
      'Boekingen (alle via de pagina gemaakt en via de link in de e-mail bevestigd, tenzij anders vermeld):',
      '',
      '| Sleutel | Wie | Dag | Tijd | Kenmerk in het adres `/b/m/…` | Stand aan het eind |',
      '|---|---|---|---|---|---|',
      ...bookings.map(([key, b]) => `| ${key} | ${b.name ?? '?'} | ${b.day ?? '?'} | ${b.time ?? '?'} | `
        + `${b.reference ?? '?'} | ${STATUS[b.status] ?? b.status ?? '?'} |`),
      '',
    );
  }
  lines.push('## Opnamen', '');
  const records = state.records ?? {};
  for (const step of steps.filter((s) => !s.action)) {
    const record = records[step.id];
    lines.push(`### ${step.id}`, '', `*Lijst:* ${step.title}`, '');
    if (step.needsWebex) {
      lines.push('- Status: **niet opgenomen**: vraagt Webex Meetings, en deze installatie heeft geen Webex. '
        + 'Op te nemen in een testomgeving met Webex Meetings; tot dan het kader "Schermafdruk volgt".', '');
      continue;
    }
    if (!record) {
      lines.push('- Status: nog niet gedraaid.', '');
      continue;
    }
    if (record.status !== 'captured') {
      lines.push(`- Status: **niet opgenomen** (${record.at}): ${record.reason}`, '');
      continue;
    }
    lines.push(`- Rol: ${record.role}`, `- Adres: \`${record.address}\``, `- Tijd: ${record.at}`, `- Zichtbaar: ${record.visible}`);
    if (record.headings?.length) lines.push(`- Kop op de pagina: ${record.headings.join(', ')}`);
    if (record.notices?.length) lines.push(`- Meldingen in beeld: ${record.notices.join(' | ')}`);
    if (record.without_webex?.length) {
      lines.push(`- Zonder Webex: het scherm toont ${record.without_webex.map((p) => `"${p}"`).join(', ')}.`);
    }
    lines.push(`- Bijsnijding: ${record.crops.join('; ')}; afbeelding ${record.size_px[0]} × ${record.size_px[1]} px.`);
    if (record.covered.length) {
      lines.push(`- Afdekkingen (${record.covered.length}):`);
      record.covered.forEach((c, n) => {
        lines.push(`  ${n + 1}. ${KIND[c.kind] ?? c.kind}: "${maskProduct(c.text)}" in "${maskProduct(c.context).replace(/\|/g, '/')}", `
          + `vak ${JSON.stringify(c.box_px)} (px op de afbeelding).`);
      });
    } else {
      lines.push('- Afdekkingen: geen (niets in beeld dat afgedekt moet worden).');
    }
    for (const note of [...(record.notes ?? []), ...(notes[step.id] ?? [])]) lines.push(`- Notitie: ${note}`);
    lines.push('');
  }
  mkdirSync(dirname(ws.log), { recursive: true });
  writeFileSync(ws.log, lines.join('\n'));
}
