#!/usr/bin/env node
/**
 * Capture the screenshots of the role manuals from the verification stack, in story order, and
 * write the evidence log. Part of the role-manuals skill (../SKILL.md).
 *
 *   node .claude/skills/role-manuals/scripts/capture.mjs <workspace> [options]
 *
 *   --list             write shot-list.md and evidence/shots.json, print the steps; drive nothing
 *   --only a,b         run only these steps (ids of the list or story actions s-...)
 *   --from <step>      run from this step onward
 *   --day YYYY-MM-DD   the agenda day of pl-07, pl-08 and the search of pl-13
 *   --no-wait          do not wait for the queue window of today's booking; record why
 *   --headed           show the browser
 *   --log-only         only rewrite evidence/log.md from evidence/state.json
 *
 * A whole run books, confirms, moves and cancels, and changes settings through the portal, so it
 * needs a fresh stack (verify-openplanner: stack.sh down and up): the booking limits leave no
 * room for a second story on one stack. Steps that only read a screen can be repeated with
 * --only. The story state (references, Mailpit ids, today's block) is kept in
 * evidence/state.json of the workspace, never a booking-link token or a password.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

import { LABEL, assertOurs, launchBrowser, loadRun, safeText } from '../../verify-openplanner/scripts/stack-client.mjs';
import { compareWithPrevious } from './compare.mjs';
import { PREPARATION, clock, coveredYears, loadState, stamp, writeLog } from './evidence.mjs';
import { agent, admin, needsWebex } from './steps-staff.mjs';
import { participant } from './steps-participant.mjs';
import { planner, plannerScreens } from './steps-planner.mjs';
import { prepare } from './steps-prepare.mjs';
import { NotYet, Run, Skip } from './story.mjs';
import { fail, hiddenTerms, openWorkspace } from './workspace.mjs';

/** Every step in story order: preparation, the participant, the planner, the agent, the screens. */
const STEPS = [...prepare, ...participant, ...planner, ...agent, ...plannerScreens, ...admin, ...needsWebex];

const ROLE_SECTIONS = [
  ['bb-', 'Deelnemers (`bb-`), niet ingelogd of via de link in hun e-mail'],
  ['mw-', 'Medewerker (`mw-`), ingelogd als `agent`'],
  ['pl-', 'Planner (`pl-`), ingelogd als `groupadmin`'],
  ['bh-', 'Beheerder (`bh-`), ingelogd als `orgadmin`, of `super` of `auditor` waar vermeld'],
];

/** Write shot-list.md (for the writers) and evidence/shots.json (for check.py) from the register. */
function writeShotList(ws) {
  const shots = STEPS.filter((s) => !s.action);
  const lines = [
    '# Opnamelijst schermafdrukken',
    '',
    'Gemaakt door `capture.mjs --list` uit het register van de skill role-manuals; niet met de hand wijzigen. '
      + 'Schrijvers gebruiken alleen deze id\'s (`![alt](img/<id>.png "Bijschrift")` en `shots=` in een taakkoppeling); '
      + 'ontbreekt een bestand, dan toont de bouwstraat het kader "Schermafdruk volgt".',
    '',
    '## Voorbereiding (het verhaal doet dit zelf en legt het vast in het bewijslog)',
    '',
    ...PREPARATION.map((p) => `- **${p.label ?? p.key}**, ${p.who}: ${p.what}.`),
    '',
  ];
  for (const [prefix, heading] of ROLE_SECTIONS) {
    lines.push(`## ${heading}`, '', '| id | Scherm en toestand |', '|---|---|');
    for (const s of shots.filter((x) => x.id.startsWith(prefix))) lines.push(`| ${s.id} | ${s.title} |`);
    lines.push('');
  }
  writeFileSync(ws.shotList, lines.join('\n'));
  mkdirSync(dirname(ws.shots), { recursive: true });
  writeFileSync(ws.shots, `${JSON.stringify(shots.map((s) => ({ id: s.id, title: s.title, needs_webex: Boolean(s.needsWebex) })), null, 2)}\n`);
}

function parseArgs(argv) {
  const args = { workspace: argv[0], only: null, from: null, day: null, list: false, noWait: false, headed: false, logOnly: false };
  for (let i = 1; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--list') args.list = true;
    else if (arg === '--no-wait') args.noWait = true;
    else if (arg === '--headed') args.headed = true;
    else if (arg === '--log-only') args.logOnly = true;
    else if (['--only', '--from', '--day'].includes(arg) && argv[i + 1]) args[arg.slice(2)] = argv[++i];
    else throw new Error(`unknown argument ${arg}; see the header of capture.mjs`);
  }
  if (args.day && !/^\d{4}-\d{2}-\d{2}$/.test(args.day)) throw new Error('--day takes YYYY-MM-DD');
  return args;
}

/** The steps to run: all, --only, or --from; the ids that need Webex Meetings never run. */
function chosen(args) {
  const names = STEPS.map((s) => s.id);
  let steps = STEPS;
  if (args.only) {
    const wanted = args.only.split(',').map((w) => w.trim()).filter(Boolean);
    const unknown = wanted.filter((w) => !names.includes(w));
    if (unknown.length) throw new Error(`unknown step(s) ${unknown.join(', ')}; see --list`);
    steps = STEPS.filter((s) => wanted.includes(s.id));
  } else if (args.from) {
    if (!names.includes(args.from)) throw new Error(`unknown step ${args.from}; see --list`);
    steps = STEPS.slice(names.indexOf(args.from));
  }
  return steps.filter((s) => !s.needsWebex);
}

/** What the portal says about its version (Updates and the overview's system status). */
async function portalVersion(run) {
  const page = await run.staff('super');
  await page.goto('/admin/updates');
  const facts = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('dt')]
    .map((d) => [d.innerText.trim(), (d.nextElementSibling || {}).innerText || ''])));
  await page.goto('/admin/');
  const overview = await page.evaluate((label) => {
    const li = [...document.querySelectorAll('li')].find((l) => l.innerText.trim().startsWith(label));
    return li ? li.innerText.replace(/\s+/g, ' ').trim() : '';
  }, LABEL.admin.statusVersion);
  const fact = (label) => (facts[label] ?? '?').trim();
  return { running: fact(LABEL.admin.running), commit: fact(LABEL.admin.commit), channel: fact(LABEL.admin.channel), overview };
}

/** The years the capture that started at `started` covers (rule 4 of redact.js); stop without them. */
function yearsOf(ws, started) {
  try {
    return coveredYears(started);
  } catch (error) {
    return fail('capture.mjs', `${ws.state}: ${error.message}`);
  }
}

async function main() {
  let args;
  let ws;
  try {
    args = parseArgs(process.argv.slice(2));
    ws = openWorkspace(args.workspace);
  } catch (error) {
    fail('capture.mjs', error.message);
  }
  writeShotList(ws);
  if (args.list) {
    console.log(STEPS.map((s) => (s.needsWebex ? `${s.id}  (vraagt Webex Meetings)` : s.id)).join('\n'));
    return 0;
  }
  const hidden = hiddenTerms(ws.vars);
  const state = loadState(ws);
  if (args.logOnly) {
    // The stack of the capture is kept in the state, so the log can be rewritten after it is down.
    if (!state.stack) fail('capture.mjs', `${ws.state} holds no capture to write a log from`);
    writeLog({ ws, stack: state.stack, state, steps: STEPS, hidden, years: yearsOf(ws, state.started) });
    console.log(`capture.mjs: ${ws.log} rewritten from ${ws.state}`);
    return 0;
  }
  let stack;
  let steps;
  try {
    steps = chosen(args);
    stack = loadRun();
    await assertOurs(stack);
  } catch (error) {
    fail('capture.mjs', error.message);
  }
  if (state.installation && state.installation !== stack.instance) {
    fail('capture.mjs', `${ws.state} belongs to installation ${state.installation}; move it away before a run on a new stack`);
  }
  const { id, instance, web, keycloak, mail, build } = stack;
  Object.assign(state, { agenda_day: args.day, installation: instance, stack: { id, instance, web, keycloak, mail, build } });
  state.started ??= stamp();
  const years = yearsOf(ws, state.started);
  console.log(`capture.mjs: installation ${stack.instance} (run ${stack.id}, commit ${stack.build.commit.slice(0, 12)}) at ${stack.web}`);

  const safe = (text) => safeText(stack, text);
  const browser = await launchBrowser({ headless: !args.headed });
  const run = new Run({ ws, stack, browser, state, hidden, years });
  let failures = 0;
  const attempt = async (s, deferred) => {
    console.log(`- ${s.id}`);
    try {
      await run.inWindowOf(s, () => s.run(run, s.id));
    } catch (error) {
      if (error instanceof NotYet && deferred) {
        console.log(`  ..   uitgesteld: ${error.message}`);
        deferred.push([s, error.minutes]);
      } else if (error instanceof Skip || error instanceof NotYet) {
        if (s.action) console.log(`  --   ${s.id}: ${error.message}`);
        else run.skipped(s.id, error.message);
      } else {
        failures += 1;
        const message = safe(String(error.message).split('\n')[0].slice(0, 300));
        console.log(`  FAIL ${s.id}: ${message}`);
        console.log(safe(String(error.stack ?? '').split('\n').slice(1, 4).join('\n')));
        if (!s.action) run.skipped(s.id, `mislukt: ${message}`);
      }
    }
  };
  try {
    if (!state.version || !args.only) {
      state.version = await portalVersion(run);
      run.save();
    }
    const deferred = [];
    for (const s of steps) await attempt(s, deferred);
    for (const [s, minutes] of deferred) {
      const wait = (minutes - clock().minutes) * 60_000;
      if (wait > 0 && args.noWait) {
        run.skipped(s.id, 'niet gewacht op het venster van de wachtrij');
        continue;
      }
      if (wait > 0) {
        console.log(`- ${s.id}: wacht ${Math.ceil(wait / 1000)} s op het venster van de wachtrij`);
        await new Promise((resolve) => setTimeout(resolve, wait));
      }
      await attempt(s, null);
    }
    writeLog({ ws, stack, state, steps: STEPS, hidden, years });
    const changes = await compareWithPrevious(ws, browser);
    if (changes.length) console.log(`capture.mjs: ${changes.length} schermafdrukken anders dan de vorige opname (evidence/diff.json)`);
  } finally {
    await browser.close();
  }
  const done = Object.values(state.records ?? {}).filter((r) => r.status === 'captured').length;
  console.log(`capture.mjs: ${done} opnamen in ${ws.img}; log ${ws.log}; ${failures} mislukt in deze run`);
  return failures ? 1 : 0;
}

process.exit(await main());
