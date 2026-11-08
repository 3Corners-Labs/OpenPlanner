/**
 * What "runs past its box" means on an admin page: the one check behind tests/admin-layout.spec.ts
 * and the admin-layout journey of the verify-openplanner skill. Plain JavaScript, so both the
 * Playwright specs and the skill's Node script import it.
 */

/**
 * Return what runs past its box on the page as shown, one sentence per finding. Evaluated in the
 * page (`page.evaluate(layoutProblems)`), so it uses nothing from outside its own body.
 *
 * - The page scrolls sideways.
 * - A table region (`.table-scroll`) is narrower than its table: the right-hand column is cut off.
 * - An element outside a table region ends right of its nearest card, widget, fact
 *   (`.facts > div`) or `main`; only the outermost such element of a box is named.
 *
 * @returns {string[]} the findings, empty when everything fits
 */
export function layoutProblems() {
  const name = (el) => {
    const classes = (el.getAttribute('class') ?? '').trim().split(/\s+/).filter(Boolean);
    const text = el.textContent.trim().replace(/\s+/g, ' ').slice(0, 50);
    return `${el.tagName.toLowerCase()}${classes.map((c) => `.${c}`).join('')}${text ? ` "${text}"` : ''}`;
  };
  const problems = [];
  const root = document.documentElement;
  if (root.scrollWidth > root.clientWidth + 1) {
    problems.push(`the page scrolls sideways (${root.scrollWidth} px in a window of ${root.clientWidth})`);
  }
  const main = document.querySelector('main');
  for (const region of main.querySelectorAll('.table-scroll')) {
    const cut = region.scrollWidth - region.clientWidth;
    if (cut > 1) {
      const heading = document.getElementById(region.getAttribute('aria-labelledby') ?? '');
      const label = region.getAttribute('aria-label') ?? heading?.textContent ?? region.querySelector('caption')?.textContent ?? '';
      problems.push(`table "${label.trim()}" is cut off: ${cut} px of it lies right of its region`);
    }
  }
  const reported = [];
  for (const el of main.querySelectorAll('*')) {
    if (el.parentElement.closest('.table-scroll') || reported.some((r) => r.contains(el))) continue;
    const rect = el.getBoundingClientRect();
    const box = el.parentElement.closest('.card, .widget, .facts > div, main');
    const over = rect.width > 1 && box ? rect.right - box.getBoundingClientRect().right : 0;
    if (over > 1) {
      reported.push(el);
      problems.push(`${name(el)} runs ${Math.round(over)} px past its ${name(box).split(' ')[0]}`);
    }
  }
  return problems;
}
