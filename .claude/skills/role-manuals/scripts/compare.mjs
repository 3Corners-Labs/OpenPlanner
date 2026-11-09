/**
 * Compare every screenshot with its previous capture (img/previous/) in the browser, draw the
 * changed pixels red on a copy in evidence/diff/, and write evidence/diff.json for check.py.
 * Chrome does the decoding and the drawing, so the comparison needs no image library.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

/** A screenshot differs when more than this share of its pixels changed visibly. */
export const DIFF_SHARE = 0.002;
/** A pixel changed when the luminance of its difference exceeds this (0 to 255). */
const DIFF_LEVEL = 24;

/** In the page: the share of changed pixels of two PNGs, and an overlay when they differ. */
function diffInPage({ before, after, level, share }) {
  const load = (src) => new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
  return Promise.all([load(before), load(after)]).then(([a, b]) => {
    if (a.width !== b.width || a.height !== b.height) return { size: [[a.width, a.height], [b.width, b.height]] };
    const canvas = (img) => {
      const c = document.createElement('canvas');
      c.width = img.width;
      c.height = img.height;
      const ctx = c.getContext('2d');
      ctx.drawImage(img, 0, 0);
      return [c, ctx, ctx.getImageData(0, 0, img.width, img.height)];
    };
    const [, , pa] = canvas(a);
    const [cb, ctxb, pb] = canvas(b);
    let changed = 0;
    for (let i = 0; i < pa.data.length; i += 4) {
      const l = 0.299 * Math.abs(pa.data[i] - pb.data[i]) + 0.587 * Math.abs(pa.data[i + 1] - pb.data[i + 1])
        + 0.114 * Math.abs(pa.data[i + 2] - pb.data[i + 2]);
      if (l > level) {
        changed += 1;
        pb.data[i] = 220;
        pb.data[i + 1] = 0;
        pb.data[i + 2] = 0;
      }
    }
    const part = changed / (a.width * a.height);
    if (part <= share) return { share: part };
    ctxb.putImageData(pb, 0, 0);
    return { share: part, overlay: cb.toDataURL('image/png') };
  });
}

const dataUrl = (file) => `data:image/png;base64,${readFileSync(file).toString('base64')}`;

/** Compare img/ with img/previous/; write the overlays and evidence/diff.json; return the results. */
export async function compareWithPrevious(ws, browser) {
  const previous = join(ws.img, 'previous');
  const out = join(ws.evidence, 'diff');
  const file = join(ws.evidence, 'diff.json');
  if (!existsSync(previous)) {
    writeFileSync(file, `${JSON.stringify({ compared: false, results: [] }, null, 2)}\n`);
    return [];
  }
  rmSync(out, { recursive: true, force: true });
  mkdirSync(out, { recursive: true });
  const pngs = (dir) => new Set(readdirSync(dir).filter((f) => f.endsWith('.png')));
  const before = pngs(previous);
  const now = pngs(ws.img);
  const page = await browser.newPage();
  const results = [];
  try {
    for (const name of [...before].sort()) {
      const id = name.replace(/\.png$/, '');
      if (!now.has(name)) {
        results.push({ id, kind: 'gone' });
        continue;
      }
      const found = await page.evaluate(diffInPage, {
        before: dataUrl(join(previous, name)), after: dataUrl(join(ws.img, name)), level: DIFF_LEVEL, share: DIFF_SHARE,
      });
      if (found.size) results.push({ id, kind: 'size', from: found.size[0], to: found.size[1] });
      else if (found.overlay) {
        writeFileSync(join(out, name), Buffer.from(found.overlay.split(',')[1], 'base64'));
        results.push({ id, kind: 'changed', share: found.share });
      }
    }
    for (const name of [...now].filter((n) => !before.has(n)).sort()) results.push({ id: name.replace(/\.png$/, ''), kind: 'new' });
  } finally {
    await page.close();
  }
  writeFileSync(file, `${JSON.stringify({ compared: true, threshold: DIFF_SHARE, results }, null, 2)}\n`);
  return results;
}
