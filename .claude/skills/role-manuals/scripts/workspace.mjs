/**
 * The workspace of one organisation's manuals: where its sources, screenshots, evidence and
 * builds live, its variables, and the guard that keeps it outside every checkout of the software.
 *
 * The scripts of this skill run from the checkout and take the workspace as their first
 * argument; an organisation's own words (its name for the service, its word for the people who
 * book, what it does not want named) live only there, in variables.json and sources/.
 * check.py applies the same guard in Python.
 */
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

/** The software's own name, hidden in text and screenshots when `hide_product_name` is set. */
export const PRODUCT_NAME = 'OpenPlanner';

/**
 * Throw when `dir` lies inside a checkout of the software (its catalogue or this skill marks
 * one). Symbolic links are followed first, so a link into a checkout is refused as well.
 */
export function guardOutsideCheckout(dir) {
  const real = existsSync(dir) ? realpathSync(dir) : dir;
  for (let at = real; ; at = dirname(at)) {
    if (existsSync(join(at, 'app', 'i18n', 'locales', 'nl.po')) || existsSync(join(at, '.claude', 'skills', 'role-manuals'))) {
      throw new Error(`${dir} lies inside the checkout ${at}; keep the workspace outside it (role-manuals SKILL.md)`);
    }
    if (dirname(at) === at) return;
  }
}

/** Resolve and guard the workspace named by `arg`, read its variables and name its folders. */
export function openWorkspace(arg) {
  if (!arg || arg.startsWith('-')) throw new Error('give the workspace folder as the first argument (role-manuals SKILL.md)');
  guardOutsideCheckout(resolve(arg));
  const dir = realpathSync(resolve(arg));
  const file = join(dir, 'variables.json');
  if (!existsSync(file)) throw new Error(`${file} is missing; set the workspace up first (role-manuals SKILL.md)`);
  const evidence = join(dir, 'evidence');
  return {
    dir,
    vars: JSON.parse(readFileSync(file, 'utf8')),
    sources: join(dir, 'sources'),
    img: join(dir, 'img'),
    build: join(dir, 'build'),
    evidence,
    state: join(evidence, 'state.json'),
    log: join(evidence, 'log.md'),
    notes: join(evidence, 'notes.json'),
    shots: join(evidence, 'shots.json'),
    shotList: join(dir, 'shot-list.md'),
  };
}

/**
 * What must not be readable in text or screenshots: the product name when `hide_product_name`
 * is set, and every term of `do_not_name`. The one list both the capture and check.py use.
 */
export function hiddenTerms(vars) {
  return {
    product: vars.hide_product_name ? PRODUCT_NAME : null,
    terms: (vars.do_not_name ?? []).filter((term) => typeof term === 'string' && term.trim()),
  };
}

/** Print `message` with the script's name and stop with exit code 2 (a precondition failed). */
export function fail(script, message) {
  console.error(`${script}: ${message}`);
  process.exit(2);
}
