/**
 * The preparation of the story (shot-list.md, "Voorbereiding"): the group on its own queue, the
 * product footer off, the seed's subject question off, and today a staffing exception of its own
 * block (pl-05) with one booking in it, for the queue of the agent.
 */
import { until } from '../../verify-openplanner/scripts/stack-client.mjs';
import { clock, dutchDate, hhmm } from './evidence.mjs';
import { Full } from './shots.mjs';
import { GROUP, ORG, PEOPLE, ROLES, Skip, UI, VIEWER, action, bookAndConfirm, formOf, step } from './story.mjs';

const ROUTING = 'Hoe afspraken bij medewerkers komen';
const OWN_QUEUE = 'Eigen wachtrij';
const SUBJECT_QUESTION_OFF = 'Waar gaat uw vraag over? uitzetten';
const selected = (select) => select.evaluate((s) => s.options[s.selectedIndex].text);

/** As orgadmin, put group demo on the own queue (and record what was there). */
async function ownQueue(run) {
  const page = await run.staff('orgadmin');
  await page.goto(`/admin/groups/${GROUP}/settings`);
  const before = await selected(page.getByLabel(ROUTING));
  const visibleFrom = await page.getByLabel(/^Zichtbaar in de wachtrij/).inputValue();
  run.state.queue_visible_minutes = Number(visibleFrom);
  if (before.startsWith(OWN_QUEUE)) {
    run.notePrep('1', '1', `Groep \`demo\` stond al op de eigen wachtrij ("${before}"); niets gewijzigd. `
      + `Zichtbaar in de wachtrij: ${visibleFrom} minuten vooraf.`);
    return;
  }
  const select = page.getByLabel(ROUTING);
  await select.selectOption(await select.evaluate((s, own) => [...s.options].find((o) => o.text.startsWith(own)).value, OWN_QUEUE));
  await formOf(select).getByRole('button', { name: UI.save }).click();
  await page.waitForLoadState('load');
  await page.goto(`/admin/groups/${GROUP}/settings`);
  const after = await selected(page.getByLabel(ROUTING));
  if (!after.startsWith(OWN_QUEUE)) throw new Error(`the group still says ${after}`);
  run.notePrep('1', '1', `Als \`orgadmin\`, **Groepen → demo → Instellingen**, veld "${ROUTING}": stond op "${before}", `
    + `gezet op "${after}" en opgeslagen. "Zichtbaar in de wachtrij" bleef ${visibleFrom} minuten vooraf (standaard).`);
}

/** As super, switch the product footer off through its setting (PRODUCT_FOOTER, class D). */
async function footerOff(run) {
  const page = await run.staff('super');
  await page.goto(`/admin/organisations/${ORG}/settings`);
  const before = await selected(page.getByLabel(/Afspraken via/));
  if (before !== 'Uit') {
    const select = page.getByLabel(/Afspraken via/);
    await select.selectOption({ label: 'Uit' });
    await formOf(select).getByRole('button', { name: UI.save }).click();
    await page.waitForLoadState('load');
  }
  await page.goto(`/admin/organisations/${ORG}/settings`);
  const after = await selected(page.getByLabel(/Afspraken via/));
  if (after !== 'Uit') throw new Error(`PRODUCT_FOOTER still ${after}`);
  const check = await run.participant(VIEWER, { fresh: true });
  await until('the group page without the product footer', async () => {
    await check.goto(`/g/${GROUP}`);
    return !(await check.locator('.product-footer').count());
  }, 30_000);
  run.notePrep('2', '2', 'Als `super`, **Organisaties → Demo-acme → Overzicht → Instellingen**, onderdeel Weergave, '
    + 'instelling `PRODUCT_FOOTER` (klasse D, het label noemt de productnaam): '
    + `stond op "${before}", gezet op "${after}" en opgeslagen. Daarna toont \`/g/demo\` geen voettekst meer, en het `
    + 'HTML-deel van de e-mails ook niet (zelfde instelling).');
}

/**
 * The demo seed asks "Waar gaat uw vraag over?" with options that name a sector; like the
 * footer, a real setting takes it away: the button "Uitzetten" on the booking form's questions.
 */
async function subjectQuestionOff(run) {
  const page = await run.staff('orgadmin');
  await page.goto(`/admin/groups/${GROUP}/settings/form`);
  const button = page.getByRole('button', { name: SUBJECT_QUESTION_OFF });
  if (!(await button.count())) {
    run.notePrep('2b', '2b', 'De vraag "Waar gaat uw vraag over?" stond al uit; niets gewijzigd.');
    return;
  }
  await button.click();
  await page.waitForLoadState('load');
  const confirm = page.getByRole('button', { name: /^(Uitzetten|Ja)/ });
  if ((await confirm.count()) && page.url().includes('settings/form') && (await confirm.first().isVisible())) {
    await confirm.first().click();
    await page.waitForLoadState('load');
  }
  await page.goto(`/admin/groups/${GROUP}/settings/form`);
  if (await page.getByRole('button', { name: SUBJECT_QUESTION_OFF }).count()) throw new Error('the question is still on');
  run.notePrep('2b', '2b', 'Als `orgadmin`, **Groepen → demo → Instellingen → Boekingsformulier**, bij de eigen vraag '
    + '"Waar gaat uw vraag over?" (keuzelijst uit de demovulling) op **Uitzetten** gedrukt: de antwoordopties van de '
    + 'demovraag zijn sectorwoorden; net als bij de voettekst haalt een echte instelling ze weg in plaats van een balk. '
    + 'De vraag "Ik wil graag een tolk" bleef aan.');
}

/** Fill the form "Afwijkende bezetting toevoegen" for an own block of `agents` agents. */
export async function addException(page, { date, from, to, agents }) {
  await page.goto(`/admin/groups/${GROUP}/exceptions`);
  const form = page.getByRole('form', { name: UI.exceptionForm });
  await form.getByLabel('Datum').fill(dutchDate(date));
  await form.getByRole('radio', { name: 'Eigen tijdblok' }).check();
  await form.getByLabel(/^Van/).fill(from);
  await form.getByLabel(/^Tot/).fill(to);
  await form.getByLabel(/^Aantal medewerkers/).fill(String(agents));
  await form.getByLabel('Aanbod').selectOption({ label: 'Afspraken' });
  await form.getByRole('button', { name: UI.exceptionForm }).click();
  await page.waitForLoadState('load');
}

/** Preparation 3 and pl-05: today a block from about an hour from now, two agents. */
async function todaysException(run, id) {
  const page = await run.staff('groupadmin');
  let block = run.state.today_block;
  if (!block) {
    const now = clock();
    const start = Math.ceil((Math.floor(now.minutes) + 65) / 30) * 30;
    const end = Math.min(start + 180, 23 * 60 + 30);
    if (end - start < 60) throw new Skip('te laat op de dag voor een tijdblok van vandaag; draai het verhaal eerder op de dag');
    block = { date: now.day, from: hhmm(start), to: hhmm(end) };
    await addException(page, { ...block, agents: 2 });
    run.state.today_block = block;
    run.notePrep('3', '3', `Als \`groupadmin\`, **Groepen → demo → Planning → Afwijkende bezetting**: datum ${dutchDate(block.date)}, `
      + `soort "Eigen tijdblok", ${block.from}–${block.to}, 2 medewerkers, aanbod "Afspraken", label leeg; `
      + `**${UI.exceptionForm}**. Dit is ook opname ${id}.`);
  }
  await page.goto(`/admin/groups/${GROUP}/exceptions`);
  if (!(await page.getByRole('region', { name: 'Komende afwijkende bezetting' }).getByText(block.from).count())) {
    throw new Error("today's exception is not listed");
  }
  await run.shot(page, id, {
    role: ROLES.groupadmin,
    visible: 'Planning → Afwijkende bezetting van de groep: de lijst "Komende afwijkende bezetting" met de afwijking van '
      + `vandaag (${dutchDate(block.date)}, eigen tijdblok ${block.from}–${block.to}, 2 medewerkers, Afspraken) en `
      + 'daaronder het lege formulier "Afwijkende bezetting toevoegen".',
    crop: new Full(),
  });
}

/** Ahmed el Amrani books today's first slot of the exception, for the agent's queue. */
async function bookToday(run) {
  const block = run.state.today_block;
  if (!block) throw new Skip('geen tijdblok van vandaag (pl-05 niet gedraaid)');
  await bookAndConfirm(run, 'ahmed-today', PEOPLE.ahmed, block.date, block.from);
}

export const prepare = [
  action('s-prep-1-own-queue', ownQueue),
  action('s-prep-2-footer-off', footerOff),
  action('s-prep-2b-subject-question-off', subjectQuestionOff),
  step('pl-05-afwijkende-bezetting', '**Planning → Afwijkende bezetting** met de afwijking van vandaag', todaysException),
  action('s-book-today-ahmed', bookToday),
];
