/**
 * Programs that run inside the captured page (Playwright serialises each function, so every one
 * is self-contained). They find what a screenshot must not show and draw the grey boxes over it,
 * on the box the page itself reports, just before the capture.
 *
 * What is covered (the rules the evidence log states):
 * 1. the product name, when the workspace hides it, in text, fields and lists, and its mark;
 * 2. every term of `do_not_name`, also inside compounds, and a whole row (a dt with its dd), table
 *    column or form field whose label holds such a term;
 * 3. versions: release numbers, the label dev, commit ids, digests, and the values of the version
 *    fields named in `versionFields`;
 * 4. the year: each number of `years` that stands on its own, in a date, a heading or a field, so
 *    a screenshot does not show when it was taken. Day, month and time stay, and no other year
 *    is ever shown in its place.
 */

/** Return the boxes to cover: {kind, label, context, x, y, w, h} in CSS pixels of the viewport. */
export function detect({ product, terms, versionFields, versionTexts, years }) {
  const esc = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const NAME = product ? new RegExp(esc(product), 'gi') : null;
  const TERMS = terms.length
    ? new RegExp(`[\\p{L}\\p{N}_-]*(?:${terms.map(esc).join('|')})[\\p{L}\\p{N}_-]*`, 'giu')
    : null;
  const VERSION = new RegExp([
    'sha256:[0-9a-f]{12,64}', '(?<![\\w.])v?\\d+\\.\\d+\\.\\d+(?:-[0-9A-Za-z.]+)?(?![\\w.])',
    '(?<![\\w.])v\\d+\\.\\d+(?![\\w.])',
    '(?<![0-9A-Za-z])(?=[0-9a-f]*\\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,64}(?![0-9A-Za-z])',
    '(?<![\\w-])dev(?![\\w-])',
  ].join('|'), 'g');
  const NO_VERSION = versionTexts.length ? new RegExp(versionTexts.map(esc).join('|'), 'gi') : null;
  const YEAR = years.length ? new RegExp(`(?<![\\p{L}\\p{N}])(?:${years.join('|')})(?![\\p{L}\\p{N}])`, 'gu') : null;
  const patterns = [[NAME, 'product'], [TERMS, 'term'], [VERSION, 'version'], [NO_VERSION, 'version'], [YEAR, 'year']]
    .filter(([re]) => re);
  const hits = (re, text) => { re.lastIndex = 0; return re.test(text || ''); };
  const named = (text) => Boolean(TERMS) && hits(TERMS, text);
  const clean = (text) => (text || '').replace(/\s+/g, ' ').trim();
  const snippet = (text) => clean(text).slice(0, 140);
  const found = [];
  const shown = (el) => {
    if (!el) return false;
    if (typeof el.checkVisibility === 'function'
        && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const push = (kind, label, context, r) => {
    if (r && r.width >= 1 && r.height >= 1) found.push({ kind, label, context, x: r.left, y: r.top, w: r.width, h: r.height });
  };
  const block = (kind, label, el) => { if (shown(el)) push(kind, label, snippet(el.innerText), el.getBoundingClientRect()); };

  const canvas = document.createElement('canvas');
  const measure = (el, text) => {
    const ctx = canvas.getContext('2d');
    const cs = getComputedStyle(el);
    ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    return ctx.measureText(text).width;
  };
  // A match inside a form control: measured from the control's padding box.
  const inControl = (el, text, kind, re) => {
    re.lastIndex = 0;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const left = r.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
    const right = r.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight);
    let m;
    while ((m = re.exec(text))) {
      if (!m[0]) { re.lastIndex++; continue; }
      const x = left + measure(el, text.slice(0, m.index));
      // A match at the end runs to the edge, where scrolled text ends; a date field's year is
      // followed by its picker, so its box stays the year's width.
      const atEnd = el.type !== 'date' && m.index + m[0].length >= text.trimEnd().length;
      const w = atEnd ? right - x : Math.min(measure(el, m[0]) * 1.08 + 4, right - x);
      if (w > 1) push(kind, m[0], snippet(text), { left: x, top: r.top, width: w, height: r.height });
    }
  };
  const inText = (node, re, kind) => {
    const text = node.nodeValue;
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text))) {
      if (!m[0]) { re.lastIndex++; continue; }
      const range = document.createRange();
      range.setStart(node, m.index);
      range.setEnd(node, m.index + m[0].length);
      for (const r of range.getClientRects()) push(kind, m[0], snippet(node.parentElement.innerText || text), r);
    }
  };

  const skipped = ['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'OPTION', 'TITLE', 'TEXTAREA'];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => {
      const p = n.parentElement;
      if (!p || skipped.includes(p.tagName)) return NodeFilter.FILTER_REJECT;
      return patterns.some(([re]) => hits(re, n.nodeValue)) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    },
  });
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!shown(node.parentElement)) continue;
    for (const [re, kind] of patterns) inText(node, re, kind);
  }

  if (NAME) {
    for (const mark of document.querySelectorAll('svg.brand-mark')) {
      if (shown(mark)) push('mark', 'beeldmerk van het product', snippet(mark.parentElement?.parentElement?.innerText), mark.getBoundingClientRect());
    }
    for (const img of document.querySelectorAll('img')) {
      const src = `${img.currentSrc || img.src || ''} ${img.alt || ''}`;
      if ((hits(NAME, src) || /brand-mark|product-mark/i.test(src)) && shown(img)) {
        push('mark', 'afbeelding van het beeldmerk', snippet(img.alt), img.getBoundingClientRect());
      }
    }
  }

  // A date field holds 2026-11-09 but shows the browser's own format (09-11-2026 in nl-NL), so a
  // match is measured in what it shows. The portal has no other field that shows a year.
  const DAY = new Intl.DateTimeFormat(navigator.language, { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' });
  const shownValue = (el) => (el.type === 'date' && el.valueAsDate
    ? DAY.format(el.valueAsDate) : el.value || el.getAttribute('placeholder') || '');
  for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea')) {
    if (!shown(el)) continue;
    const value = shownValue(el);
    for (const [re, kind] of patterns) {
      if (!hits(re, value)) continue;
      if (el.tagName === 'TEXTAREA') push(kind, 'tekstvak', snippet(value), el.getBoundingClientRect());
      else inControl(el, value, kind, re);
    }
  }
  for (const el of document.querySelectorAll('select')) {
    if (!shown(el)) continue;
    for (const [re, kind] of patterns) {
      if (el.multiple || el.size > 1) {
        for (const o of el.options) if (hits(re, o.text)) push(kind, o.text, snippet(o.text), o.getBoundingClientRect());
      } else if (el.selectedIndex >= 0) {
        const text = el.options[el.selectedIndex].text;
        if (hits(re, text)) inControl(el, text, kind, re);
      }
    }
  }

  // Rule 2, whole blocks: a row, a column or a field whose label names a term.
  for (const dt of document.querySelectorAll('dt')) {
    const label = clean(dt.innerText);
    if (named(label)) {
      block('row', `rij ${label} (term)`, dt);
      if (dt.nextElementSibling?.tagName === 'DD') block('row', `rij ${label} (waarde)`, dt.nextElementSibling);
    } else if (versionFields.includes(label) && dt.nextElementSibling?.tagName === 'DD') {
      block('version-field', `waarde van ${label}`, dt.nextElementSibling);
    }
  }
  for (const table of document.querySelectorAll('table')) {
    const heads = [...table.querySelectorAll('thead th')];
    heads.forEach((th, index) => {
      if (!named(th.innerText)) return;
      const cells = [th];
      for (const row of table.querySelectorAll('tbody tr')) {
        const own = [...row.children];
        if (own.length === heads.length) cells.push(own[index]);
      }
      for (const cell of cells) block('column', `kolom ${clean(th.innerText)}`, cell);
    });
  }
  for (const label of document.querySelectorAll('label')) {
    if (!named(label.innerText)) continue;
    const field = label.closest('.field');
    if (field) {
      block('field', `veld ${clean(label.innerText)} (label, keuze, uitleg)`, field);
    } else {
      block('field', `veld ${clean(label.innerText)} (label)`, label);
      if (label.control) block('field', `veld ${clean(label.innerText)} (keuze)`, label.control);
    }
  }

  // A word inside a covered block needs no box of its own.
  const BLOCKS = ['row', 'column', 'field', 'version-field'];
  const big = found.filter((f) => BLOCKS.includes(f.kind));
  const within = (a, b) => a.x >= b.x - 1 && a.y >= b.y - 1 && a.x + a.w <= b.x + b.w + 1 && a.y + a.h <= b.y + b.h + 1;
  return found.filter((f) => BLOCKS.includes(f.kind) || !big.some((b) => within(f, b)));
}

/** Facts for the log: the h1, the visible notices and which of `phrases` the page shows. */
export function pageFacts(phrases) {
  const visible = (n) => n.offsetParent !== null;
  const text = [...document.querySelectorAll('.chip, [role=status], [role=alert], .alert, p, dd, td, li')]
    .filter(visible).map((n) => n.innerText).join('\n');
  const h1 = [...document.querySelectorAll('h1')].map((h) => h.innerText.trim()).filter(Boolean);
  const notices = [...document.querySelectorAll('[role=status], [role=alert], .alert, .notice')]
    .filter(visible).map((n) => n.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean);
  return { h1, notices: [...new Set(notices)].slice(0, 6), present: phrases.filter((p) => text.includes(p)) };
}

/** A readable name for a crop target: its kind and its heading or first words. */
export function describe(el) {
  const head = el.matches('h1,h2,h3,h4,caption,legend,label,th,dt,summary,button,a')
    ? el : el.querySelector('h1,h2,h3,h4,caption,legend,label,dt,summary');
  const text = ((head ? head.innerText : el.innerText) || '').replace(/\s+/g, ' ').trim().slice(0, 60);
  const what = {
    SECTION: 'onderdeel', FORM: 'formulier', TABLE: 'tabel', TBODY: 'tijdslot', DETAILS: 'uitleg',
    BUTTON: 'knop', DIV: 'blok', TR: 'rij', DD: 'waarde', A: 'link',
  }[el.tagName] || el.tagName.toLowerCase();
  return `${what} "${text}"`;
}

/** The bottom of the last element of an email's body, in CSS pixels. */
export function emailBottom() {
  return Math.ceil(Math.max(0, ...[...document.body.children].map((e) => e.getBoundingClientRect().bottom)));
}

/** The bottom of the portal's environment bar, or 0 when the page shows none. */
export function envBarBottom() {
  const bar = document.querySelector('.env-bar');
  if (!bar || bar.offsetParent === null) return 0;
  return Math.ceil(bar.getBoundingClientRect().bottom);
}

/** Draw the grey boxes over the page, `pad` CSS pixels wider on every side. */
export function drawBoxes({ boxes, pad, colour }) {
  const layer = document.createElement('div');
  layer.id = 'role-manuals-boxes';
  layer.setAttribute('aria-hidden', 'true');
  layer.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647';
  for (const b of boxes) {
    const box = document.createElement('div');
    box.style.cssText = `position:absolute;left:${b.x - pad}px;top:${b.y - pad}px;`
      + `width:${b.w + 2 * pad}px;height:${b.h + 2 * pad}px;background:${colour}`;
    layer.append(box);
  }
  document.body.append(layer);
}

/** Remove the grey boxes again. */
export function removeBoxes() {
  document.getElementById('role-manuals-boxes')?.remove();
}
