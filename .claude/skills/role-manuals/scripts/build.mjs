// Builds the role manuals from one Markdown source into Word (.docx) and HTML; pdf.py prints the
// HTML to PDF. One parse feeds both renderers, so Word, HTML and PDF always carry the same text.
//
// Usage: node scripts/build.mjs [bron/<file>.md ...]   (default: every file in bron/)
// Input:  bron/*.md (front matter + the Markdown subset of the redactiestatuut), img/*.png,
//         variabelen.json (product name, help desk, software version, accent colour, font).
// Output: build/<name>.docx and build/<name>.html.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { marked } from 'marked';
import * as docx from 'docx';

const {
  AlignmentType, BorderStyle, Document, ExternalHyperlink, Footer, HeadingLevel, ImageRun,
  LevelFormat, Packer, PageBreak, PageNumber, Paragraph, ShadingType, Table, TableCell,
  TableOfContents, TableRow, TabStopType, TextRun, WidthType,
} = docx;

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (ROOT.split(path.sep).join('/').includes('.claude/skills')) {
  console.error('build.mjs: copy the scripts to a workspace outside the repository first (role-manuals SKILL.md).');
  process.exit(2);
}
const SRC = path.join(ROOT, 'bron');
const OUT = path.join(ROOT, 'build');
const VARS = JSON.parse(fs.readFileSync(path.join(ROOT, 'variabelen.json'), 'utf8'));

const FONT = VARS.lettertype || 'Arial';
const ACCENT = VARS.accentkleur || '1F4E79';
const PAGE_W = 11906; // A4 in DXA
const PAGE_H = 16838;
const MARGIN = 1134; // 2 cm
const CONTENT_W = PAGE_W - 2 * MARGIN;
const MAX_IMG_W = 600; // px at 96 dpi, about 15.9 cm
const MAX_IMG_H = 720;

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

/** Replace `{{name}}` by its value from variabelen.json; warn on unknown names. */
function substitute(text, file) {
  return text.replace(/\{\{(\w+)\}\}/g, (all, key) => {
    if (key in VARS) return VARS[key];
    console.warn(`${file}: unknown variable ${all}`);
    return all;
  });
}

const ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" };
const unescape = (s) => String(s ?? '').replace(/&(amp|lt|gt|quot|#39);/g, (e) => ENTITIES[e]);

/** Flatten inline tokens into runs: {text, bold, italic, code, href}. */
function inline(tokens, fmt = {}) {
  const out = [];
  for (const t of tokens || []) {
    switch (t.type) {
      case 'strong': out.push(...inline(t.tokens, { ...fmt, bold: true })); break;
      case 'em': out.push(...inline(t.tokens, { ...fmt, italic: true })); break;
      case 'codespan': out.push({ ...fmt, text: unescape(t.text), code: true }); break;
      case 'link': out.push(...inline(t.tokens, { ...fmt, href: t.href })); break;
      case 'br': out.push({ ...fmt, text: '\n' }); break;
      case 'del': out.push(...inline(t.tokens, fmt)); break;
      case 'image': out.push({ ...fmt, text: unescape(t.text) }); break;
      case 'html': break;
      case 'escape': out.push({ ...fmt, text: unescape(t.text) }); break;
      case 'text':
        if (t.tokens && t.tokens.length) out.push(...inline(t.tokens, fmt));
        else out.push({ ...fmt, text: unescape(t.text) });
        break;
      default: out.push({ ...fmt, text: unescape(t.text ?? t.raw ?? '') });
    }
  }
  return out;
}

const plain = (runs) => runs.map((r) => r.text).join('');

/** Build the block model of one document; figures are numbered in reading order. */
function parseDocument(body) {
  let figureNo = 0;
  const figure = (tok) => {
    figureNo += 1;
    const file = path.join(ROOT, tok.href);
    const id = path.basename(tok.href, '.png');
    const note = (VARS.opnames_zonder_webex || {})[id];
    const caption = unescape(tok.title || '') + (note ? ` (testopname zonder Webex Meetings: ${note})` : '');
    return {
      type: 'figure', src: tok.href, file, exists: fs.existsSync(file),
      alt: unescape(tok.text), caption, num: figureNo,
    };
  };
  const blocks = (tokens) => {
    const out = [];
    for (const t of tokens) {
      switch (t.type) {
        case 'space': break;
        case 'heading': out.push({ type: 'heading', level: t.depth, inline: inline(t.tokens) }); break;
        case 'paragraph':
        case 'text': {
          const toks = (t.tokens || [{ type: 'text', text: t.text }])
            .filter((x) => !(x.type === 'text' && !String(x.text).trim()));
          if (toks.length === 1 && toks[0].type === 'image') out.push(figure(toks[0]));
          else out.push({ type: 'para', inline: inline(t.tokens || [{ type: 'text', text: t.text }]) });
          break;
        }
        case 'list':
          out.push({
            type: 'list', ordered: t.ordered, start: Number(t.start) || 1,
            items: t.items.map((it) => ({ blocks: blocks(it.tokens) })),
          });
          break;
        case 'table':
          out.push({
            type: 'table', header: t.header.map((c) => inline(c.tokens)),
            rows: t.rows.map((r) => r.map((c) => inline(c.tokens))),
          });
          break;
        case 'blockquote': {
          const inner = blocks(t.tokens);
          const first = inner[0]?.type === 'para' ? inner[0].inline[0] : null;
          const label = first && first.bold ? first.text.replace(/:\s*$/, '') : '';
          out.push({ type: 'callout', label, blocks: inner });
          break;
        }
        case 'html': {
          if (/<!--\s*pagina\s*-->/.test(t.raw)) out.push({ type: 'pagebreak' });
          const task = t.raw.match(/<!--\s*task:\s*([\s\S]*?)-->/);
          if (task) out.push({ type: 'task', attrs: parseTask(task[1]) });
          break;
        }
        case 'code': out.push({ type: 'code', text: t.text }); break;
        case 'hr': break;
        default: console.warn(`unsupported block ${t.type}`);
      }
    }
    return out;
  };
  const model = blocks(marked.lexer(body));
  const figures = [];
  const walk = (bl) => bl.forEach((b) => {
    if (b.type === 'figure') figures.push(b);
    if (b.type === 'list') b.items.forEach((it) => walk(it.blocks));
    if (b.type === 'callout') walk(b.blocks);
  });
  walk(model);
  return { model, figures };
}

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

const isWarning = (label) => /let op|belangrijk|waarschuwing/i.test(label || '');

/** Read width and height from a PNG header. */
function pngSize(file) {
  const data = fs.readFileSync(file);
  return { width: data.readUInt32BE(16), height: data.readUInt32BE(20), data };
}

function fitImage(width, height, maxW) {
  let w = maxW;
  let h = Math.round((height * maxW) / width);
  if (h > MAX_IMG_H) { h = MAX_IMG_H; w = Math.round((width * MAX_IMG_H) / height); }
  return { w, h };
}

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
    numbering.push({
      reference,
      levels: [
        { level: 0, format: LevelFormat.DECIMAL, text: '%1.', start, alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 567, hanging: 397 } } } },
        { level: 1, format: LevelFormat.LOWER_LETTER, text: '%2.', start: 1, alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 1134, hanging: 397 } } } },
        { level: 2, format: LevelFormat.LOWER_ROMAN, text: '%3.', start: 1, alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 1701, hanging: 397 } } } },
      ],
    });
    return reference;
  };

  const runs = (inl, base = {}) => inl.flatMap((r) => r.text.split('\n').map((part, i) => {
    const opts = {
      text: part, bold: r.bold || base.bold, italics: r.italic || base.italic,
      font: r.code ? 'Consolas' : FONT, size: base.size, color: base.color,
      break: i > 0 ? 1 : undefined,
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
          out.push(new Paragraph({
            heading: b.level === 2 ? HeadingLevel.HEADING_1 : HeadingLevel.HEADING_2,
            children: runs(b.inline),
          }));
          break;
        case 'para':
          out.push(new Paragraph({ indent: ctx.indent ? { left: ctx.indent } : undefined, children: runs(b.inline) }));
          break;
        case 'list': {
          const ref = b.ordered ? orderedRef(b.start) : 'bullets';
          for (const item of b.items) {
            const [first, ...rest] = item.blocks;
            const lead = first?.type === 'para' ? first.inline : [];
            out.push(new Paragraph({ numbering: { reference: ref, level: ctx.level }, spacing: { after: 80 }, children: runs(lead) }));
            out.push(...render(first?.type === 'para' ? rest : item.blocks,
              { indent: 567 * (ctx.level + 1), level: ctx.level + 1 }));
          }
          break;
        }
        case 'table': out.push(table(b), new Paragraph({ spacing: { after: 60 }, children: [] })); break;
        case 'callout': {
          const warn = isWarning(b.label);
          const color = warn ? 'B7791F' : ACCENT;
          const fill = warn ? 'FFF6E5' : 'EEF4FA';
          for (const inner of b.blocks) {
            if (inner.type !== 'para') { out.push(...render([inner], ctx)); continue; }
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
  const missing = figures.filter((f) => !f.exists).length;
  const title = [
    new Paragraph({ spacing: { before: 2600, after: 120 }, children: [new TextRun({ text: meta.rol || '', bold: true, size: 24, color: ACCENT, font: FONT })] }),
    new Paragraph({ heading: HeadingLevel.TITLE, children: [new TextRun({ text: meta.titel })] }),
    new Paragraph({ spacing: { after: 1400 }, children: [new TextRun({ text: meta.ondertitel || '', size: 28, color: '444444', font: FONT })] }),
    metaLine('Versie', meta.versie || ''),
    metaLine('Datum', meta.datum || ''),
    metaLine('Softwareversie', meta.softwareversie || ''),
    ...(figures.length ? [metaLine('Schermafdrukken', `${figures.length - missing} uit de werkende software, op een testinstallatie met verzonnen gegevens${missing ? `; ${missing} volgen` : ''}`)] : []),
    new Paragraph({ children: [new PageBreak()] }),
    new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text: 'Inhoud' })] }),
    new TableOfContents('Inhoud', { hyperlink: true, headingStyleRange: '1-2' }),
    new Paragraph({ children: [new PageBreak()] }),
  ];
  const footer = new Footer({
    children: [new Paragraph({
      tabStops: [{ type: TabStopType.RIGHT, position: CONTENT_W }],
      children: [
        new TextRun({ text: `${meta.titel} · versie ${meta.versie}`, size: 16, color: '666666', font: FONT }),
        new TextRun({ children: ['\tPagina ', PageNumber.CURRENT, ' van ', PageNumber.TOTAL_PAGES], size: 16, color: '666666', font: FONT }),
      ],
    })],
  });
  const body = render(model);
  return new Document({
    creator: 'Bouwstraat rolhandleidingen',
    title: meta.titel,
    description: meta.ondertitel,
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
      children: [...title, ...body],
    }],
  });
}

// ---------------------------------------------------------------------------------------------
// HTML renderer (screen and print; pdf.py prints it).

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const slug = (s) => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

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

function htmlDocument(meta, model, figures, index = []) {
  const toc = [];
  let pendingTask = null;
  const used = new Set();
  const anchor = (text) => {
    let id = slug(text) || 'kop';
    while (used.has(id)) id += '-x';
    used.add(id);
    return id;
  };
  const render = (blocks) => blocks.map((b) => {
    switch (b.type) {
      case 'heading': {
        if (b.level === 1) return '';
        const text = plain(b.inline);
        const id = anchor(text);
        toc.push({ level: b.level, text, id });
        if (pendingTask) {
          index.push({ ...pendingTask, titel: text, anker: id });
          pendingTask = null;
        }
        return `<h${b.level} id="${id}">${htmlInline(b.inline)}</h${b.level}>`;
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
      case 'callout': {
        const kind = isWarning(b.label) ? 'callout callout--let-op' : 'callout';
        return `<aside class="${kind}" aria-label="${esc(b.label || 'Opmerking')}">${render(b.blocks)}</aside>`;
      }
      case 'figure': {
        const caption = `<figcaption><span class="fignum">Afbeelding ${b.num}.</span> ${esc(b.caption)}</figcaption>`;
        if (!b.exists) {
          return `<figure class="figure figure--volgt"><div class="placeholder"><strong>Schermafdruk volgt</strong> <span>(${esc(path.basename(b.src, '.png'))})</span><br>${esc(b.alt)}</div>${caption}</figure>`;
        }
        const { width, height } = pngSize(b.file);
        const mobile = width / height < 0.8 ? ' figure--smal' : '';
        return `<figure class="figure${mobile}"><img src="../${esc(b.src)}" alt="${esc(b.alt)}" width="${Math.round(width / 2)}" height="${Math.round(height / 2)}">${caption}</figure>`;
      }
      case 'pagebreak': return '<div class="pagina" aria-hidden="true"></div>';
      case 'task': pendingTask = b.attrs; return '';
      case 'code': return `<pre><code>${esc(b.text)}</code></pre>`;
      default: return '';
    }
  }).join('\n');
  const content = render(model);
  const missing = figures.filter((f) => !f.exists).length;
  const tocHtml = toc.filter((t) => t.level === 2)
    .map((t) => `<li><a href="#${t.id}">${esc(t.text)}</a></li>`).join('');
  return `<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(meta.titel)}</title>
<style>${CSS}</style>
</head>
<body>
<header class="titelpagina">
  <p class="rol">${esc(meta.rol || '')}</p>
  <h1>${esc(meta.titel)}</h1>
  <p class="ondertitel">${esc(meta.ondertitel || '')}</p>
  <dl class="meta">
    <div><dt>Versie</dt><dd>${esc(meta.versie || '')}</dd></div>
    <div><dt>Datum</dt><dd>${esc(meta.datum || '')}</dd></div>
    <div><dt>Softwareversie</dt><dd>${esc(meta.softwareversie || '')}</dd></div>
    ${figures.length ? `<div><dt>Schermafdrukken</dt><dd>${figures.length - missing} uit de werkende software, op een testinstallatie met verzonnen gegevens${missing ? `; ${missing} volgen` : ''}</dd></div>` : ''}
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
html { font-family: ${FONT}, Helvetica, sans-serif; font-size: 10.5pt; line-height: 1.45; color: var(--tekst); background: #fff; }
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

// ---------------------------------------------------------------------------------------------

async function build(file) {
  const name = path.basename(file, '.md');
  const [rawMeta, rawBody] = splitFrontMatter(fs.readFileSync(file, 'utf8'));
  const meta = Object.fromEntries(Object.entries(rawMeta).map(([k, v]) => [k, substitute(v, name)]));
  const body = substitute(rawBody, name);
  if (!meta.titel) meta.titel = (body.match(/^#\s+(.+)$/m) || [])[1] || name;
  const { model, figures } = parseDocument(body);
  fs.mkdirSync(OUT, { recursive: true });
  const buffer = await Packer.toBuffer(wordDocument(meta, model, figures));
  fs.writeFileSync(path.join(OUT, `${name}.docx`), buffer);
  const index = [];
  fs.writeFileSync(path.join(OUT, `${name}.html`), htmlDocument(meta, model, figures, index));
  HULPINDEX.push(...index.map((e) => ({ bestand: name, rol: meta.rol, html: `${name}.html#${e.anker}`, ...e })));
  const missing = figures.filter((f) => !f.exists).map((f) => path.basename(f.src, '.png'));
  console.log(`${name}: ${figures.length} afbeeldingen, ${missing.length} volgen${missing.length ? ` (${missing.join(', ')})` : ''}`);
}

const HULPINDEX = [];
const args = process.argv.slice(2);
const files = args.length ? args.map((a) => path.resolve(a))
  : fs.readdirSync(SRC).filter((f) => f.endsWith('.md')).sort().map((f) => path.join(SRC, f));
for (const f of files) await build(f);
if (!args.length) {
  // The contract for context-driven help: per screen, the tasks per role with their permission.
  fs.writeFileSync(path.join(OUT, 'hulpindex.json'), `${JSON.stringify(HULPINDEX, null, 2)}\n`);
  console.log(`hulpindex: ${HULPINDEX.length} taken op ${new Set(HULPINDEX.map((e) => e.screen)).size} schermen`);
}
