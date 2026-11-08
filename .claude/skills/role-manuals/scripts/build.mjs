#!/usr/bin/env node
/**
 * Build the role manuals of a workspace from one Markdown source into Word (.docx), HTML and
 * tagged PDF. One parse feeds every renderer, so Word, HTML and PDF carry the same text. Part of
 * the role-manuals skill (../SKILL.md).
 *
 *   node .claude/skills/role-manuals/scripts/build.mjs <workspace> [sources/<file>.md ...] [--no-pdf]
 *
 * Input:  sources/*.md (front matter + the Markdown subset of the style guide), img/*.png,
 *         variables.json (product name, help desk, software version, accent colour, font).
 * Output: build/<name>.docx, build/<name>.html, build/<name>.pdf; build/manuals.json (per manual its
 *         role, tasks and figures, which check.py checks) and build/help-index.json, the contract for
 *         help in context (per task: screen, permission, shots, title, anchor). Nothing is built when
 *         a source breaks a rule: a task marker without its heading, a figure that is no
 *         img/<id>.png, or a term this organisation does not show (after its variables are filled in).
 * docx and marked come from ../package.json (npm ci there); the PDF is printed by the Chrome that
 * the verification stack's client starts (Playwright from e2e/).
 */
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import * as docx from 'docx';
import { marked } from 'marked';

import { launchBrowser } from '../../verify-openplanner/scripts/stack-client.mjs';
import { fail, hiddenTerms, openWorkspace } from './workspace.mjs';

const {
  AlignmentType, BorderStyle, Document, ExternalHyperlink, Footer, HeadingLevel, ImageRun,
  LevelFormat, Packer, PageBreak, PageNumber, Paragraph, ShadingType, Table, TableCell,
  TableOfContents, TableRow, TabStopType, TextRun, WidthType,
} = docx;

const argv = process.argv.slice(2);
let ws;
try {
  ws = openWorkspace(argv[0]);
} catch (error) {
  fail('build.mjs', error.message);
}
const VARS = ws.vars;

/** A branding value from variables.json, refused unless it matches `pattern`. */
function branding(key, fallback, pattern) {
  const value = VARS[key] ?? fallback;
  if (!pattern.test(value)) fail('build.mjs', `${key} in variables.json must match ${pattern}`);
  return value;
}
const FONT = branding('font', 'Arial', /^[\p{L}\p{N} .'-]{1,60}$/u); // one family name
const ACCENT = branding('accent_colour', '1F4E79', /^[0-9A-Fa-f]{6}$/);
const PAGE_W = 11906; // A4 in DXA
const PAGE_H = 16838;
const MARGIN = 1134; // 2 cm
const CONTENT_W = PAGE_W - 2 * MARGIN;
const MAX_IMG_W = 600; // px at 96 dpi, about 15.9 cm
const MAX_IMG_H = 720;
const MAX_LIST_LEVEL = 2; // the numbering configs define levels 0 to 2

// ---------------------------------------------------------------------------------------------
// Source: front matter, variables, Markdown tokens to a small block model.

/** Split the leading `---` front matter (flat `key: value` lines) from the body. */
function splitFrontMatter(text) {
  const match = text.match(/^---\n([\s\S]*?)\n---\n/);
  if (!match) return [{}, text];
  const meta = {};
  for (const line of match[1].split('\n')) {
    const kv = line.match(/^([\w-]+):\s*(.*)$/);
    if (kv) meta[kv[1]] = kv[2].trim().replace(/^"(.*)"$/, '$1');
  }
  return [meta, text.slice(match[0].length)];
}

/** Replace `{{name}}` by its text value from variables.json; warn on unknown names. */
function substitute(text, file) {
  return text.replace(/\{\{(\w+)\}\}/g, (all, key) => {
    if (typeof VARS[key] === 'string') return VARS[key];
    console.warn(`${file}: unknown variable ${all}`);
    return all;
  });
}

/**
 * A link target the manuals may carry: https, mailto, an anchor or a relative path. Control
 * characters and spaces are taken out first, as a browser does, so they hide no other scheme.
 */
function safeHref(href, file) {
  const clean = String(href).replace(/[\u0000-\u0020\u007f]/g, '');
  const scheme = /^[a-z][a-z0-9+.-]*:/i.test(clean);
  const network = /^[\\/]{2}/.test(clean); // //host or \\host: another machine, not a relative link
  if (/^(https:|mailto:|#)/i.test(clean) || (!scheme && !network)) return clean;
  console.warn(`${file}: link ${href} dropped (only https, mailto, anchors and relative links)`);
  return null;
}

/** A figure must be a screenshot of the workspace: img/<id>.png, nothing else on disk. */
const FIGURE = /^img\/[a-z0-9][a-z0-9-]*\.png$/;
const slug = (s) => s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');



const ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" };
const unescape = (s) => String(s ?? '').replace(/&(amp|lt|gt|quot|#39);/g, (e) => ENTITIES[e]);

/**
 * Flatten inline tokens into runs: {text, bold, italic, code, href}. `ctx` carries the line of
 * the first token, collects every bold span with its line (`ctx.bold`) and every error.
 */
function inline(tokens, file, ctx, fmt = {}) {
  const out = [];
  let line = ctx.line;
  for (const t of tokens || []) {
    const here = { ...ctx, line };
    switch (t.type) {
      case 'strong': {
        const runs = inline(t.tokens, file, { ...here, bold: null }, { ...fmt, bold: true });
        ctx.bold?.push({ text: runs.map((r) => r.text).join(''), line });
        out.push(...runs);
        break;
      }
      case 'em': out.push(...inline(t.tokens, file, here, { ...fmt, italic: true })); break;
      case 'codespan': out.push({ ...fmt, text: unescape(t.text), code: true }); break;
      case 'link': out.push(...inline(t.tokens, file, here, { ...fmt, href: safeHref(t.href, file) })); break;
      case 'br': out.push({ ...fmt, text: '\n' }); break;
      case 'del': out.push(...inline(t.tokens, file, here, fmt)); break;
      case 'image': out.push({ ...fmt, text: unescape(t.text) }); break;
      case 'html':
        if (/<!--\s*task:/.test(t.raw)) ctx.errors.push(`${file}:${line}: taakkoppeling midden in een alinea; zet haar op een eigen regel boven de kop`);
        break;
      case 'escape': out.push({ ...fmt, text: unescape(t.text) }); break;
      case 'text':
        if (t.tokens && t.tokens.length) out.push(...inline(t.tokens, file, here, fmt));
        else out.push({ ...fmt, text: unescape(t.text) });
        break;
      default: out.push({ ...fmt, text: unescape(t.text ?? t.raw ?? '') });
    }
    line += (String(t.raw ?? '').match(/\n/g) ?? []).length;
  }
  return out;
}

const plain = (runs) => runs.map((r) => r.text).join('');

/** Read the attributes of a `<!-- task: screen=...; permission=...; shots=a,b -->` marker. */
function parseTask(text) {
  const attrs = {};
  for (const part of text.split(';')) {
    const [key, ...rest] = part.split('=');
    if (key.trim()) attrs[key.trim()] = rest.join('=').trim();
  }
  attrs.shots = (attrs.shots || '').split(',').map((x) => x.trim()).filter(Boolean);
  return attrs;
}

const newlines = (raw) => (String(raw ?? '').match(/\n/g) ?? []).length;

/**
 * Build the block model of one source: the blocks, its figures in reading order, its tasks and
 * its bold spans, each with the line it starts on (counted from the tokens themselves). A task
 * marker belongs to the heading right after it; that heading keeps the task and gets its anchor
 * here, once. Whatever breaks a rule is returned in `errors` with its line.
 */
function parseDocument(body, firstLine, file) {
  const errors = [];
  const bold = [];
  let figureNo = 0;
  const figure = (tok, line) => {
    figureNo += 1;
    const id = path.basename(tok.href, '.png');
    if (!FIGURE.test(tok.href)) errors.push(`${file}:${line}: afbeelding ${tok.href} is geen img/<id>.png`);
    const note = (VARS.without_webex || {})[id];
    const source = FIGURE.test(tok.href) ? path.join(ws.dir, tok.href) : null;
    return {
      type: 'figure', id, src: tok.href, file: source, exists: Boolean(source && fs.existsSync(source)), line,
      alt: unescape(tok.text), caption: unescape(tok.title || '') + (note ? ` (testopname zonder Webex Meetings: ${note})` : ''),
      num: figureNo,
    };
  };
  const runs = (tokens, line) => inline(tokens, file, { line, bold, errors });
  const blocks = (tokens, first) => {
    const out = [];
    let line = first;
    for (const t of tokens) {
      switch (t.type) {
        case 'space': case 'hr': break;
        case 'heading': out.push({ type: 'heading', level: t.depth, inline: runs(t.tokens, line), line }); break;
        case 'paragraph':
        case 'text': {
          const toks = (t.tokens || [{ type: 'text', text: t.text }]).filter((x) => !(x.type === 'text' && !String(x.text).trim()));
          if (toks.length === 1 && toks[0].type === 'image') out.push(figure(toks[0], line));
          else out.push({ type: 'para', inline: runs(t.tokens || [{ type: 'text', text: t.text, raw: t.raw }], line), line });
          break;
        }
        case 'list': {
          let itemLine = line;
          const items = t.items.map((it) => {
            const item = { blocks: blocks(it.tokens, itemLine) };
            itemLine += newlines(it.raw);
            return item;
          });
          out.push({ type: 'list', ordered: t.ordered, start: Number(t.start) || 1, items, line });
          break;
        }
        case 'table': {
          // A table's rows each sit on a line of their own, after the header and the rule.
          const rows = t.rows.map((r, i) => r.map((c) => runs(c.tokens, line + 2 + i)));
          out.push({ type: 'table', header: t.header.map((c) => runs(c.tokens, line)), rows, line });
          break;
        }
        case 'blockquote': {
          const inner = blocks(t.tokens, line);
          const first0 = inner[0]?.type === 'para' ? inner[0].inline[0] : null;
          out.push({ type: 'callout', label: first0 && first0.bold ? first0.text.replace(/:\s*$/, '') : '', blocks: inner, line });
          break;
        }
        case 'html': {
          if (/<!--\s*pagebreak\s*-->/.test(t.raw)) out.push({ type: 'pagebreak', line });
          for (const task of t.raw.matchAll(/<!--\s*task:\s*([\s\S]*?)-->/g)) out.push({ type: 'task', attrs: parseTask(task[1]), line });
          break;
        }
        case 'code': out.push({ type: 'code', text: t.text, line }); break;
        default: console.warn(`${file}:${line}: unsupported block ${t.type}`);
      }
      line += newlines(t.raw);
    }
    return out;
  };
  const model = blocks(marked.lexer(body), firstLine);
  const figures = [];
  const tasks = [];
  const used = new Set();
  const resolve = (list) => list.forEach((b, i) => {
    if (b.type === 'heading' && b.level > 1) {
      let id = slug(plain(b.inline)) || 'kop';
      while (used.has(id)) id += '-x';
      used.add(id);
      b.id = id;
      if (b.task) tasks.push({ ...b.task, title: plain(b.inline), anchor: id });
    }
    if (b.type === 'task') {
      const next = list[i + 1];
      if (next?.type === 'heading' && next.level > 1) next.task = { line: b.line, ...b.attrs };
      else errors.push(`${file}:${b.line}: taakkoppeling zonder kop (niveau 2 of dieper) direct eronder`);
    }
    if (b.type === 'figure') figures.push(b);
    if (b.type === 'list') b.items.forEach((it) => resolve(it.blocks));
    if (b.type === 'callout') resolve(b.blocks);
  });
  resolve(model);
  return { model, figures, tasks, bold, errors };
}

/** The terms this organisation does not show (and the hidden product name) that `text` names. */
function hiddenIn(text) {
  const { product, terms } = hiddenTerms(VARS);
  const shown = String(text).toLowerCase();
  return [...(product ? [product] : []), ...terms].filter((term) => shown.includes(term.toLowerCase()));
}

/**
 * Every line of the filled-in source that names a hidden term, outside HTML comments, and every
 * figure caption that does (a caption also carries its note from `without_webex`).
 */
function namedTerms(text, figures, file) {
  const errors = [];
  text.split('\n').forEach((line, n) => {
    for (const term of hiddenIn(line.replace(/<!--[\s\S]*?-->/g, ''))) errors.push(`${file}:${n + 1}: noemt "${term}"`);
  });
  for (const figure of figures) {
    for (const term of hiddenIn(figure.caption)) errors.push(`${file}:${figure.line}: het bijschrift noemt "${term}"`);
  }
  return errors;
}

const isWarning = (label) => /let op|belangrijk|waarschuwing/i.test(label || '');

/** Width and height from a PNG header. */
function pngSize(file) {
  const data = fs.readFileSync(file);
  return { width: data.readUInt32BE(16), height: data.readUInt32BE(20), data };
}

function fitImage(width, height, maxW) {
  let w = maxW;
  let h = Math.round((height * maxW) / width);
  if (h > MAX_IMG_H) {
    h = MAX_IMG_H;
    w = Math.round((width * MAX_IMG_H) / height);
  }
  return { w, h };
}

const screenshotsLine = (figures) => {
  const missing = figures.filter((f) => !f.exists).length;
  return `${figures.length - missing} uit de werkende software, op een testinstallatie met verzonnen gegevens${missing ? `; ${missing} volgen` : ''}`;
};

// ---------------------------------------------------------------------------------------------
// Word renderer (docx-js).

function wordDocument(meta, model, figures) {
  const numbering = [{
    reference: 'bullets',
    levels: [0, 1, 2].map((level) => ({
      level, format: LevelFormat.BULLET, text: ['•', '–', '·'][level], alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 567 * (level + 1), hanging: 340 } } },
    })),
  }];
  let olCount = 0;
  const orderedRef = (start) => {
    olCount += 1;
    const reference = `ol-${olCount}`;
    const level = (n, format, text, first) => ({
      level: n, format, text, start: first, alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 567 * (n + 1), hanging: 397 } } },
    });
    numbering.push({
      reference,
      levels: [level(0, LevelFormat.DECIMAL, '%1.', start), level(1, LevelFormat.LOWER_LETTER, '%2.', 1), level(2, LevelFormat.LOWER_ROMAN, '%3.', 1)],
    });
    return reference;
  };

  const runs = (inl, base = {}) => inl.flatMap((r) => r.text.split('\n').map((part, i) => {
    const opts = {
      text: part, bold: r.bold || base.bold, italics: r.italic || base.italic,
      font: r.code ? 'Consolas' : FONT, size: base.size, color: base.color, break: i > 0 ? 1 : undefined,
    };
    return r.href
      ? new ExternalHyperlink({ link: r.href, children: [new TextRun({ ...opts, style: 'Hyperlink' })] })
      : new TextRun(opts);
  }));

  const figureParas = (f, indent) => {
    const caption = new Paragraph({
      alignment: AlignmentType.CENTER, spacing: { before: 40, after: 220 },
      children: [
        new TextRun({ text: `Afbeelding ${f.num}. `, bold: true, size: 18, color: '555555', font: FONT }),
        new TextRun({ text: f.caption, italics: true, size: 18, color: '555555', font: FONT }),
      ],
    });
    if (!f.exists) {
      const dashed = { style: BorderStyle.DASHED, size: 6, color: '8A96A3', space: 6 };
      return [new Paragraph({
        keepNext: true, alignment: AlignmentType.CENTER, spacing: { before: 160, after: 40 },
        indent: { left: indent + 284, right: 284 },
        shading: { type: ShadingType.CLEAR, fill: 'F2F4F7', color: 'auto' },
        border: { top: dashed, bottom: dashed, left: dashed, right: dashed },
        children: [
          new TextRun({ text: 'Schermafdruk volgt', bold: true, font: FONT, size: 20 }),
          new TextRun({ text: ` (${path.basename(f.src, '.png')})`, font: FONT, size: 18, color: '555555' }),
          new TextRun({ text: f.alt, font: FONT, size: 18, color: '555555', break: 1 }),
        ],
      }), caption];
    }
    const { width, height, data } = pngSize(f.file);
    const { w, h } = fitImage(width, height, MAX_IMG_W - Math.round(indent / 15));
    return [new Paragraph({
      keepNext: true, alignment: AlignmentType.CENTER, spacing: { before: 160, after: 40 },
      children: [new ImageRun({
        type: 'png', data, transformation: { width: w, height: h },
        altText: { title: f.caption || f.alt, description: f.alt, name: path.basename(f.src) },
      })],
    }), caption];
  };

  const table = (t) => {
    const n = t.header.length;
    const longest = (i) => Math.max(plain(t.header[i]).length, ...t.rows.map((r) => plain(r[i] || []).length));
    const weight = [...Array(n).keys()].map((i) => Math.max(Math.min(longest(i), 70), 8));
    const total = weight.reduce((a, b) => a + b, 0);
    const widths = weight.map((x) => Math.floor((CONTENT_W * x) / total));
    widths[n - 1] += CONTENT_W - widths.reduce((a, b) => a + b, 0);
    const line = { style: BorderStyle.SINGLE, size: 4, color: 'BFC7D1' };
    const cell = (inl, i, header) => new TableCell({
      width: { size: widths[i], type: WidthType.DXA },
      shading: header ? { type: ShadingType.CLEAR, fill: 'E8EEF4', color: 'auto' } : undefined,
      margins: { top: 60, bottom: 60, left: 100, right: 100 },
      children: [new Paragraph({ spacing: { after: 0 }, children: runs(inl, { bold: header, size: n >= 5 ? 17 : 19 }) })],
    });
    return new Table({
      width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: widths,
      borders: { top: line, bottom: line, left: line, right: line, insideHorizontal: line, insideVertical: line },
      rows: [
        new TableRow({ tableHeader: true, cantSplit: true, children: t.header.map((c, i) => cell(c, i, true)) }),
        ...t.rows.map((r) => new TableRow({ cantSplit: true, children: t.header.map((_, i) => cell(r[i] || [], i, false)) })),
      ],
    });
  };

  const render = (blocks, ctx = { indent: 0, level: 0 }) => {
    const out = [];
    for (const b of blocks) {
      switch (b.type) {
        case 'heading':
          if (b.level === 1) break; // the title is on the title page
          out.push(new Paragraph({ heading: b.level === 2 ? HeadingLevel.HEADING_1 : HeadingLevel.HEADING_2, children: runs(b.inline) }));
          break;
        case 'para':
          out.push(new Paragraph({ indent: ctx.indent ? { left: ctx.indent } : undefined, children: runs(b.inline) }));
          break;
        case 'list': {
          const ref = b.ordered ? orderedRef(b.start) : 'bullets';
          const level = Math.min(ctx.level, MAX_LIST_LEVEL);
          for (const item of b.items) {
            const [first, ...rest] = item.blocks;
            const lead = first?.type === 'para' ? first.inline : [];
            out.push(new Paragraph({ numbering: { reference: ref, level }, spacing: { after: 80 }, children: runs(lead) }));
            out.push(...render(first?.type === 'para' ? rest : item.blocks, { indent: 567 * (level + 1), level: ctx.level + 1 }));
          }
          break;
        }
        case 'table': out.push(table(b), new Paragraph({ spacing: { after: 60 }, children: [] })); break;
        case 'callout': {
          const warn = isWarning(b.label);
          const color = warn ? 'B7791F' : ACCENT;
          const fill = warn ? 'FFF6E5' : 'EEF4FA';
          for (const inner of b.blocks) {
            if (inner.type !== 'para') {
              out.push(...render([inner], ctx));
              continue;
            }
            out.push(new Paragraph({
              shading: { type: ShadingType.CLEAR, fill, color: 'auto' },
              border: { left: { style: BorderStyle.SINGLE, size: 24, color, space: 8 } },
              indent: { left: 170 + (ctx.indent || 0), right: 170 }, spacing: { before: 120, after: 120 },
              children: runs(inner.inline),
            }));
          }
          break;
        }
        case 'figure': out.push(...figureParas(b, ctx.indent || 0)); break;
        case 'pagebreak': out.push(new Paragraph({ children: [new PageBreak()] })); break;
        case 'code':
          for (const line of b.text.split('\n')) {
            out.push(new Paragraph({
              spacing: { after: 0 }, shading: { type: ShadingType.CLEAR, fill: 'F3F4F6', color: 'auto' },
              children: [new TextRun({ text: line || ' ', font: 'Consolas', size: 18 })],
            }));
          }
          break;
        default: break;
      }
    }
    return out;
  };

  const metaLine = (label, value) => new Paragraph({
    spacing: { after: 60 },
    children: [
      new TextRun({ text: `${label}: `, bold: true, font: FONT, size: 21, color: '444444' }),
      new TextRun({ text: value, font: FONT, size: 21, color: '444444' }),
    ],
  });
  const title = [
    new Paragraph({ spacing: { before: 2600, after: 120 }, children: [new TextRun({ text: meta.role || '', bold: true, size: 24, color: ACCENT, font: FONT })] }),
    new Paragraph({ heading: HeadingLevel.TITLE, children: [new TextRun({ text: meta.title })] }),
    new Paragraph({ spacing: { after: 1400 }, children: [new TextRun({ text: meta.subtitle || '', size: 28, color: '444444', font: FONT })] }),
    metaLine('Versie', meta.version || ''),
    metaLine('Datum', meta.date || ''),
    metaLine('Softwareversie', meta.software_version || ''),
    ...(figures.length ? [metaLine('Schermafdrukken', screenshotsLine(figures))] : []),
    new Paragraph({ children: [new PageBreak()] }),
    new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text: 'Inhoud' })] }),
    new TableOfContents('Inhoud', { hyperlink: true, headingStyleRange: '1-2' }),
    new Paragraph({ children: [new PageBreak()] }),
  ];
  const footer = new Footer({
    children: [new Paragraph({
      tabStops: [{ type: TabStopType.RIGHT, position: CONTENT_W }],
      children: [
        new TextRun({ text: meta.version ? `${meta.title} · versie ${meta.version}` : meta.title, size: 16, color: '666666', font: FONT }),
        new TextRun({ children: ['\tPagina ', PageNumber.CURRENT, ' van ', PageNumber.TOTAL_PAGES], size: 16, color: '666666', font: FONT }),
      ],
    })],
  });
  return new Document({
    creator: 'Bouwstraat rolhandleidingen',
    title: meta.title,
    description: meta.subtitle,
    features: { updateFields: true },
    styles: {
      default: {
        document: { run: { font: FONT, size: 21 }, paragraph: { spacing: { line: 288, after: 120 } } },
        title: { run: { font: FONT, size: 56, bold: true, color: ACCENT }, paragraph: { spacing: { before: 0, after: 200 }, keepNext: true } },
        heading1: { run: { font: FONT, size: 32, bold: true, color: ACCENT }, paragraph: { spacing: { before: 360, after: 160 }, keepNext: true, keepLines: true } },
        heading2: { run: { font: FONT, size: 26, bold: true, color: '1A1A1A' }, paragraph: { spacing: { before: 280, after: 120 }, keepNext: true, keepLines: true } },
        hyperlink: { run: { color: ACCENT, underline: { type: 'single' } } },
      },
    },
    numbering: { config: numbering },
    sections: [{
      properties: {
        titlePage: true,
        page: { size: { width: PAGE_W, height: PAGE_H }, margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN, footer: 567 } },
      },
      footers: { default: footer, first: new Footer({ children: [new Paragraph({ children: [] })] }) },
      children: [...title, ...render(model)],
    }],
  });
}

// ---------------------------------------------------------------------------------------------
// HTML renderer (screen and print; the PDF is printed from it).

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function htmlInline(inl) {
  return inl.map((r) => {
    let s = esc(r.text).replace(/\n/g, '<br>');
    if (r.code) s = `<code>${s}</code>`;
    if (r.italic) s = `<em>${s}</em>`;
    if (r.bold) s = `<strong>${s}</strong>`;
    if (r.href) s = `<a href="${esc(r.href)}">${s}</a>`;
    return s;
  }).join('');
}

/** The HTML page of one manual. */
function htmlDocument(meta, model, figures) {
  const toc = [];
  const render = (blocks) => blocks.map((b) => {
    switch (b.type) {
      case 'heading': {
        if (b.level === 1) return '';
        toc.push({ level: b.level, text: plain(b.inline), id: b.id });
        return `<h${b.level} id="${b.id}">${htmlInline(b.inline)}</h${b.level}>`;
      }
      case 'para': return `<p>${htmlInline(b.inline)}</p>`;
      case 'list': {
        const tag = b.ordered ? 'ol' : 'ul';
        const start = b.ordered && b.start !== 1 ? ` start="${b.start}"` : '';
        return `<${tag}${start}>${b.items.map((it) => `<li>${render(it.blocks)}</li>`).join('')}</${tag}>`;
      }
      case 'table':
        return `<table${b.header.length >= 5 ? ' class="breed"' : ''}><thead><tr>${b.header.map((c) => `<th scope="col">${htmlInline(c)}</th>`).join('')}</tr></thead>`
          + `<tbody>${b.rows.map((r) => `<tr>${b.header.map((_, i) => `<td>${htmlInline(r[i] || [])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
      case 'callout':
        return `<aside class="${isWarning(b.label) ? 'callout callout--let-op' : 'callout'}" aria-label="${esc(b.label || 'Opmerking')}">${render(b.blocks)}</aside>`;
      case 'figure': {
        const caption = `<figcaption><span class="fignum">Afbeelding ${b.num}.</span> ${esc(b.caption)}</figcaption>`;
        if (!b.exists) {
          return `<figure class="figure figure--volgt"><div class="placeholder"><strong>Schermafdruk volgt</strong> <span>(${esc(path.basename(b.src, '.png'))})</span><br>${esc(b.alt)}</div>${caption}</figure>`;
        }
        const { width, height } = pngSize(b.file);
        const narrow = width / height < 0.8 ? ' figure--smal' : '';
        return `<figure class="figure${narrow}"><img src="../${esc(b.src)}" alt="${esc(b.alt)}" width="${Math.round(width / 2)}" height="${Math.round(height / 2)}">${caption}</figure>`;
      }
      case 'pagebreak': return '<div class="pagina" aria-hidden="true"></div>';
      case 'code': return `<pre><code>${esc(b.text)}</code></pre>`;
      default: return '';
    }
  }).join('\n');
  const content = render(model);
  const tocHtml = toc.filter((t) => t.level === 2).map((t) => `<li><a href="#${t.id}">${esc(t.text)}</a></li>`).join('');
  return `<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(meta.title)}</title>
<style>${CSS}</style>
</head>
<body>
<header class="titelpagina">
  <p class="rol">${esc(meta.role || '')}</p>
  <h1>${esc(meta.title)}</h1>
  <p class="ondertitel">${esc(meta.subtitle || '')}</p>
  <dl class="meta">
    <div><dt>Versie</dt><dd>${esc(meta.version || '')}</dd></div>
    <div><dt>Datum</dt><dd>${esc(meta.date || '')}</dd></div>
    <div><dt>Softwareversie</dt><dd>${esc(meta.software_version || '')}</dd></div>
    ${figures.length ? `<div><dt>Schermafdrukken</dt><dd>${esc(screenshotsLine(figures))}</dd></div>` : ''}
  </dl>
</header>
<nav class="inhoud" aria-labelledby="inhoud-kop"><h2 id="inhoud-kop">Inhoud</h2><ol>${tocHtml}</ol></nav>
<main>
${content}
</main>
</body>
</html>
`;
}

const CSS = `
:root { --accent: #${ACCENT}; --tekst: #1a1a1a; --gedempt: #555; --lijn: #c9d1db; --tip: #eef4fa; --letop: #fff6e5; --letop-lijn: #b7791f; }
@page { size: A4; }
html { font-family: "${FONT}", Helvetica, sans-serif; font-size: 10.5pt; line-height: 1.45; color: var(--tekst); background: #fff; }
body { margin: 0 auto; max-width: 174mm; }
h1 { font-size: 26pt; line-height: 1.15; color: var(--accent); margin: 0 0 0.3em; }
h2 { font-size: 16pt; color: var(--accent); margin: 1.6em 0 0.5em; break-after: avoid; }
h3 { font-size: 12.5pt; margin: 1.3em 0 0.4em; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
a { color: var(--accent); }
.titelpagina { min-height: 230mm; display: flex; flex-direction: column; justify-content: center; break-after: page; }
.titelpagina .rol { font-weight: bold; color: var(--accent); font-size: 12pt; margin: 0 0 0.5em; }
.titelpagina .ondertitel { font-size: 14pt; color: #444; margin: 0 0 3em; }
.meta { display: grid; gap: 0.25em; margin: 0; }
.meta div { display: flex; gap: 0.5em; }
.meta dt { font-weight: bold; min-width: 9em; }
.meta dd { margin: 0; }
.inhoud { break-after: page; }
.inhoud ol { padding-left: 1.4em; }
.inhoud li { margin: 0.25em 0; }
ol, ul { padding-left: 1.5em; }
li { margin: 0.25em 0; }
li > p { margin: 0.2em 0; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; margin: 0.6em 0 1.1em; }
th, td { border: 1px solid var(--lijn); padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: #e8eef4; }
table.breed { font-size: 8.5pt; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
.callout { border-left: 4px solid var(--accent); background: var(--tip); padding: 0.4em 0.9em; margin: 0.9em 0; break-inside: avoid; }
.callout p { margin: 0.35em 0; }
.callout--let-op { border-left-color: var(--letop-lijn); background: var(--letop); }
.figure { margin: 0.9em 0 1.3em; text-align: center; break-inside: avoid; }
.figure img { max-width: 100%; height: auto; max-height: 175mm; border: 1px solid var(--lijn); border-radius: 3px; }
.figure--smal img { max-width: 70mm; }
figcaption { font-size: 9pt; color: var(--gedempt); margin-top: 0.35em; }
.fignum { font-weight: bold; }
.placeholder { border: 2px dashed #8a96a3; background: #f2f4f7; padding: 1.2em; font-size: 9.5pt; color: var(--gedempt); }
.placeholder strong { color: var(--tekst); }
.pagina { break-after: page; }
code { font-family: Consolas, Menlo, monospace; font-size: 0.95em; background: #f3f4f6; padding: 0 2px; }
pre { background: #f3f4f6; padding: 0.6em; overflow-x: auto; }
@media screen { body { padding: 2em 16px; } .titelpagina { min-height: auto; padding: 3em 0; } }
`;

const FOOTER = (title) => '<div style="font-family:Arial,sans-serif;font-size:8px;color:#666;width:100%;padding:0 18mm;'
  + `display:flex;justify-content:space-between"><span>${esc(title)}</span><span>Pagina <span class="pageNumber"></span> `
  + 'van <span class="totalPages"></span></span></div>';

// ---------------------------------------------------------------------------------------------

/** Read and parse one source with the workspace's variables filled in. */
function parse(file) {
  const name = path.basename(file, '.md');
  const raw = fs.readFileSync(file, 'utf8');
  const text = substitute(raw, name);
  const [meta, body] = splitFrontMatter(text);
  meta.title ||= (body.match(/^#\s+(.+)$/m) || [])[1] || name;
  const firstLine = newlines(text.slice(0, text.length - body.length)) + 1;
  const doc = parseDocument(body, firstLine, `${name}.md`);
  doc.sha256 = createHash('sha256').update(raw).digest('hex');
  return { name, meta, ...doc, errors: [...doc.errors, ...namedTerms(text, doc.figures, `${name}.md`)] };
}

/** Write one manual as Word and HTML; return its HTML file. */
async function render(doc) {
  fs.mkdirSync(ws.build, { recursive: true });
  fs.writeFileSync(path.join(ws.build, `${doc.name}.docx`), await Packer.toBuffer(wordDocument(doc.meta, doc.model, doc.figures)));
  const html = path.join(ws.build, `${doc.name}.html`);
  fs.writeFileSync(html, htmlDocument(doc.meta, doc.model, doc.figures));
  const missing = doc.figures.filter((f) => !f.exists).map((f) => f.id);
  console.log(`${doc.name}: ${doc.figures.length} afbeeldingen, ${missing.length} volgen${missing.length ? ` (${missing.join(', ')})` : ''}`);
  return { html, title: doc.meta.title };
}

/** Print each HTML manual to a tagged PDF with bookmarks, A4, a footer with title and page. */
async function printPdfs(built) {
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();
    for (const { html, title } of built) {
      await page.goto(pathToFileURL(html).href, { waitUntil: 'load' });
      await page.evaluate(() => document.fonts.ready.then(() => true));
      const target = html.replace(/\.html$/, '.pdf');
      await page.pdf({
        path: target, format: 'A4', printBackground: true, displayHeaderFooter: true,
        headerTemplate: '<span></span>', footerTemplate: FOOTER(title),
        margin: { top: '18mm', bottom: '20mm', left: '18mm', right: '18mm' }, tagged: true, outline: true,
      });
      console.log(`pdf: ${path.basename(target)}`);
    }
  } finally {
    await browser.close();
  }
}

const named = argv.slice(1).filter((a) => !a.startsWith('--'));
const files = named.length ? named.map((a) => path.resolve(a))
  : fs.readdirSync(ws.sources).filter((f) => f.endsWith('.md')).sort().map((f) => path.join(ws.sources, f));
const docs = files.map(parse);
const errors = docs.flatMap((d) => d.errors);
if (errors.length) {
  console.error(`build.mjs: niets gebouwd; ${errors.length} fout(en):\n${errors.map((e) => `  ${e}`).join('\n')}`);
  process.exit(1);
}
const built = [];
for (const doc of docs) built.push(await render(doc));
if (!named.length) {
  // What check.py checks against the matrix, the help topics and the shot list, from this one parse.
  const manuals = docs.map((d) => ({
    file: d.name, sha256: d.sha256, role: d.meta.role ?? '', tasks: d.tasks,
    figures: d.figures.map((f) => ({ id: f.id, line: f.line })), bold: d.bold,
  }));
  fs.writeFileSync(path.join(ws.build, 'manuals.json'), `${JSON.stringify(manuals, null, 2)}\n`);
  // The contract for help in context: per task its screen, permission, shots, manual and anchor.
  const index = docs.flatMap((d) => d.tasks.map(({ line, ...task }) => ({ file: d.name, role: d.meta.role, html: `${d.name}.html#${task.anchor}`, ...task })));
  fs.writeFileSync(path.join(ws.build, 'help-index.json'), `${JSON.stringify(index, null, 2)}\n`);
  console.log(`help-index: ${index.length} taken op ${new Set(index.map((e) => e.screen)).size} schermen`);
}
if (!argv.includes('--no-pdf')) await printPdfs(built);
