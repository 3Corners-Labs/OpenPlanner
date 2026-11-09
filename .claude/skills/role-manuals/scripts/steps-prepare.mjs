/**
 * The preparation of the story (PREPARATION in evidence.mjs): the group on its own queue, the
 * product footer off, the seed's subject question off, and today a staffing exception of its own
 * block (pl-05) with one booking in it, for the queue of the agent.
 */
import { until } from '../../verify-openplanner/scripts/stack-client.mjs';
import { clock, dutchDate, hhmm } from './evidence.mjs';
import { Full } from './shots.mjs';
import { A, GROUP, ORG, PEOPLE, ROLES, Skip, VIEWER, action, bookAndConfirm, formOf, pick, step } from './story.mjs';

const selected = (page, label) => page.getByLabel(label).evaluate((s) => s.options[s.selectedIndex].text.trim());
/** Today's block must hold at least one slot, from about an hour from now (the lead time). */
const MIN_BLOCK_MINUTES = 30;

/** Save the form of the field labelled `label` and wait for the page that follows. */
async function saveFormOf(page, label) {
  await formOf(page.getByLabel(label)).getByRole('button', { name: A.save }).click();
  await page.waitForLoadState('load');
}

/** As orgadmin, put group demo on the own queue (and record what was there). */
async function ownQueue(run) {
  const page = await run.staff('orgadmin');
  await page.goto(`/admin/groups/${GROUP}/settings`);
  const before = await selected(page, A.routing);
  const visibleFrom = await page.getByLabel(A.queueVisible).inputValue();
  run.state.queue_visible_minutes = Number(visibleFrom);
  if (A.ownQueue.test(before)) {
    run.notePrep('1', `Groep \`demo\` stond al op de eigen wachtrij ("${before}"); niets gewijzigd. `
      + `Zichtbaar in de wachtrij: ${visibleFrom} minuten vooraf.`);
    return;
  }
  const after = await pick(page, A.routing, A.ownQueue);
  await saveFormOf(page, A.routing);
  await page.goto(`/admin/groups/${GROUP}/settings`);
  if (!A.ownQueue.test(await selected(page, A.routing))) throw new Error('the group is not on its own queue after saving');
  run.notePrep('1', `Als \`orgadmin\`, **Groepen → demo → Instellingen**, veld "${A.routing}": stond op "${before}", `
    + `gezet op "${after}" en opgeslagen. "Zichtbaar in de wachtrij" bleef ${visibleFrom} minuten vooraf (standaard).`);
}

/** As super, switch the product footer off through its setting (PRODUCT_FOOTER, class D). */
async function footerOff(run) {
  const page = await run.staff('super');
  await page.goto(`/admin/organisations/${ORG}/settings`);
  const before = await selected(page, A.footer);
  if (before !== A.off) {
    await pick(page, A.footer, A.off);
    await saveFormOf(page, A.footer);
  }
  await page.goto(`/admin/organisations/${ORG}/settings`);
  const after = await selected(page, A.footer);
  if (after !== A.off) throw new Error(`PRODUCT_FOOTER still ${after}`);
  const check = await run.participant(VIEWER, { fresh: true });
  await until('the group page without the product footer', async () => {
    await check.goto(`/g/${GROUP}`);
    return !(await check.locator('.product-footer').count());
  }, 30_000);
  run.notePrep('2', 'Als `super`, **Organisaties → Demo-acme → Overzicht → Instellingen**, onderdeel Weergave, '
    + 'instelling `PRODUCT_FOOTER` (klasse D, het label noemt de productnaam): '
    + `stond op "${before}", gezet op "${after}" en opgeslagen. Daarna toont \`/g/demo\` geen voettekst meer, en het `
    + 'HTML-deel van de e-mails ook niet (zelfde instelling).');
}

/** Whether the booking form's page lists `question` among its retired questions. */
async function retired(page, question) {
  const lead = A.retiredFields({ names: '' }).trim();
  const hints = await page.locator('.hint').allInnerTexts();
  return hints.some((hint) => hint.trim().startsWith(lead) && hint.includes(question));
}

/**
 * The demo seed asks "Waar gaat uw vraag over?", a choice the manuals do not describe; like the
 * footer, a real setting takes it away (the button "… uitzetten" on the booking form's
 * questions, which posts at once), so the form in the screenshots holds the interpreter question
 * only and no booking of the story needs an answer to it. "Already off" must show as such on the
 * page; a button that is missing for any other reason fails the step.
 */
async function subjectQuestionOff(run) {
  const page = await run.staff('orgadmin');
  const off = A.retireField({ label: A.subjectQuestion });
  await page.goto(`/admin/groups/${GROUP}/settings/form`);
  const button = page.getByRole('button', { name: off });
  if (!(await button.count())) {
    if (!(await retired(page, A.subjectQuestion))) throw new Error(`no button "${off}" and the question is not listed as retired`);
    run.notePrep('2b', `De vraag "${A.subjectQuestion}" stond al uit; niets gewijzigd.`);
    return;
  }
  await button.click();
  await page.waitForLoadState('load');
  await page.goto(`/admin/groups/${GROUP}/settings/form`);
  if ((await page.getByRole('button', { name: off }).count()) || !(await retired(page, A.subjectQuestion))) {
    throw new Error('the question is not retired after "Uitzetten"');
  }
  run.notePrep('2b', 'Als `orgadmin`, **Groepen → demo → Instellingen → Boekingsformulier**, bij de eigen vraag '
    + `"${A.subjectQuestion}" (keuzelijst uit de demovulling) op **Uitzetten** gedrukt: de handleidingen beschrijven `
    + 'die demovraag niet; net als bij de voettekst haalt een echte instelling haar weg in plaats van een balk. '
    + 'De vraag "Ik wil graag een tolk" bleef aan.');
}

/** Fill and send the form "Afwijkende bezetting toevoegen": an own block of `agents` agents. */
export async function addException(page, { date, from, to, agents }) {
  await page.goto(`/admin/groups/${GROUP}/exceptions`);
  const form = page.getByRole('form', { name: A.exceptionForm });
  await form.getByLabel(A.date).fill(dutchDate(date));
  await form.getByRole('radio', { name: A.ownBlock }).check();
  await form.getByLabel(A.from).fill(from);
  await form.getByLabel(A.to).fill(to);
  await form.getByLabel(A.capacity).fill(String(agents));
  await pick(form, A.offer, A.offerScheduled);
  await form.getByRole('button', { name: A.exceptionButton }).click();
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
    if (end - start < MIN_BLOCK_MINUTES) {
      throw new Skip('te laat op de dag voor een tijdblok van vandaag; draai het verhaal eerder op de dag');
    }
    block = { date: now.day, from: hhmm(start), to: hhmm(end) };
    await addException(page, { ...block, agents: 2 });
    run.state.today_block = block;
    run.notePrep('3', `Als \`groupadmin\`, **Groepen → demo → Planning → Afwijkende bezetting**: datum ${dutchDate(block.date)}, `
      + `soort "${A.ownBlock}", ${block.from}–${block.to}, 2 medewerkers, aanbod "${A.offerScheduled}", label leeg; `
      + `**${A.exceptionButton}**. Dit is ook opname ${id}.`);
  }
  await page.goto(`/admin/groups/${GROUP}/exceptions`);
  if (!(await page.getByRole('region', { name: A.upcomingExceptions }).getByText(block.from).count())) {
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
