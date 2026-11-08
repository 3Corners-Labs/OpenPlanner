/**
 * One screenshot: how it is cut from the page (a crop type per kind of cut), what is covered
 * (redact.js), and the record the evidence log writes about it. No story knowledge lives here.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { blankTokens } from '../../verify-openplanner/scripts/stack-client.mjs';
import { describe, detect, drawBoxes, emailBottom, envBarBottom, pageFacts, removeBoxes } from './redact.js';
import { PRODUCT_NAME } from './workspace.mjs';

export const VIEWPORT = { width: 1280, height: 800 };
export const WIDE = { width: 1440, height: 900 };
export const MOBILE = { width: 390, height: 844 };
export const SCALE = 2;
export const EMAIL_WIDTH = 700;
export const GREY = 'rgb(208, 208, 208)';
/** CSS pixels around a box, so the anti-aliased edges of the glyphs are covered too. */
export const PAD = 2;
/** Version fields whose whole value is covered (rule 3 of redact.js). */
const VERSION_FIELDS = ['Commit', 'Nieuwste versie'];

/** Texts that show because the installation has no Webex Meetings (logged per capture). */
export const NO_VIDEO_PHRASES = [
  'Geen meeting', 'Meeting klaar', 'Videolink mislukt', 'Videolink wordt klaargezet',
  'Het videogesprek wordt nog klaargezet', 'Het videogesprek kon niet worden klaargezet',
  'Niet opgepakt', 'Deelnemer nog niet in het gesprek',
];

/** `text` with the product name replaced, for the log (the log is read like the manuals). */
export const maskProduct = (text) => String(text ?? '').replace(new RegExp(PRODUCT_NAME, 'gi'), '[productnaam]');

async function describeTarget(locator) {
  try {
    return maskProduct(await locator.first().evaluate(describe, null, { timeout: 3000 }));
  } catch {
    return 'element';
  }
}

async function box(locator) {
  return (await locator.count()) ? locator.first().boundingBox() : null;
}

/** The window as it is. */
export class Viewport {
  stretch = false;

  async clip(page, size) {
    return [{ x: 0, y: 0, width: size.width, height: size.height }, [`venster ${size.width}×${size.height}`]];
  }
}

/** The whole page. */
export class Full {
  stretch = true;

  async clip(page, size) {
    return [{ x: 0, y: 0, width: size.width, height: size.height }, ['hele pagina']];
  }
}

/** From the top of the page down to `until` (plus `margin`), or the first window height. */
export class Top {
  stretch = true;

  constructor(until = null, margin = 24) {
    this.until = until;
    this.margin = margin;
  }

  async clip(page, size) {
    const end = this.until ? await box(this.until) : null;
    if (end) {
      return [{ x: 0, y: 0, width: size.width, height: Math.min(size.height, end.y + end.height + this.margin) },
        [`van boven tot onder ${await describeTarget(this.until)} (+${this.margin} px)`]];
    }
    return [{ x: 0, y: 0, width: size.width, height: Math.min(size.height, VIEWPORT.height) },
      [`bovenste ${VIEWPORT.height} px van de pagina`]];
  }
}

/** Around `target` with a margin; optionally ending at `bottom`, optionally the full width. */
export class Region {
  stretch = true;

  constructor(target, { margin = 24, bottom = null, fullWidth = true } = {}) {
    Object.assign(this, { target, margin, bottom, fullWidth });
  }

  async clip(page, size) {
    const found = await box(this.target);
    if (!found) throw new Error(`the region ${await describeTarget(this.target)} is not on the page`);
    const crops = [];
    const area = { ...found };
    const end = this.bottom ? await box(this.bottom) : null;
    if (end) {
      area.height = end.y + end.height - area.y;
      crops.push(`afgesneden onder ${await describeTarget(this.bottom)}`);
    }
    const y0 = Math.max(0, area.y - this.margin);
    const y1 = Math.min(size.height, area.y + area.height + this.margin);
    const x0 = this.fullWidth ? 0 : Math.max(0, area.x - this.margin);
    const x1 = this.fullWidth ? size.width : Math.min(size.width, area.x + area.width + this.margin);
    crops.push(`uitsnede rond ${await describeTarget(this.target)} (marge ${this.margin} px)`);
    return [{ x: x0, y: y0, width: x1 - x0, height: y1 - y0 }, crops];
  }
}

/** A rendered email: the whole message, down to 16 px under its last line. */
export class Email {
  stretch = true;

  async clip(page, size) {
    const bottom = await page.evaluate(emailBottom);
    return [{ x: 0, y: 0, width: size.width, height: Math.min(size.height, bottom + 16) },
      [`hele bericht, ${EMAIL_WIDTH} px breed, tot 16 px onder de laatste regel`]];
  }
}

async function settle(page) {
  await page.waitForLoadState('load');
  await page.evaluate(() => document.fonts.ready.then(() => true));
  await page.waitForTimeout(400);
}

/** Make the window as tall as the page, so a capture needs no scrolling. */
async function stretch(page) {
  const old = page.viewportSize() ?? VIEWPORT;
  await page.evaluate(() => window.scrollTo(0, 0));
  let height = old.height;
  for (let i = 0; i < 4; i++) {
    const doc = await page.evaluate(() => Math.ceil(document.documentElement.scrollHeight));
    if (doc <= height) break;
    height = Math.min(doc, 16_000);
    await page.setViewportSize({ width: old.width, height });
    await page.waitForTimeout(300);
  }
}

const pngSize = (png) => [png.readUInt32BE(16), png.readUInt32BE(20)];

/**
 * Capture `page` as `<img>/<id>.png` with `crop`, cover what `hidden` names, and return the
 * record for the evidence log. The portal's environment bar is cropped off a capture that starts
 * at the top; participant pages and emails keep what they show.
 */
export async function shoot(page, { id, img, hidden, crop = new Viewport(), role, visible, notes = [], address = null }) {
  await settle(page);
  const old = page.viewportSize() ?? VIEWPORT;
  let facts;
  let boxes;
  let clip;
  let crops;
  let png;
  try {
    if (crop.stretch) await stretch(page);
    facts = await page.evaluate(pageFacts, NO_VIDEO_PHRASES);
    boxes = await page.evaluate(detect, { ...hidden, versionFields: VERSION_FIELDS });
    [clip, crops] = await crop.clip(page, page.viewportSize() ?? old);
    if (clip.y < 1 && new URL(page.url(), 'http://x').pathname.startsWith('/admin')) {
      const bar = await page.evaluate(envBarBottom);
      if (bar > 0) {
        clip = { ...clip, y: bar, height: clip.height - bar };
        crops.push(`omgevingsbalk 'Ontwikkelomgeving' (bovenste ${bar} px) weggesneden`);
      }
    }
    await page.evaluate(drawBoxes, { boxes, pad: PAD, colour: GREY });
    png = await page.screenshot({ type: 'png', clip, animations: 'disabled' });
  } finally {
    await page.evaluate(removeBoxes).catch(() => {});
    if (crop.stretch) await page.setViewportSize(old);
  }
  const [width, height] = pngSize(png);
  const covered = [];
  for (const b of boxes) {
    const x0 = Math.round((b.x - clip.x - PAD) * SCALE);
    const y0 = Math.round((b.y - clip.y - PAD) * SCALE);
    const x1 = Math.round((b.x + b.w - clip.x + PAD) * SCALE);
    const y1 = Math.round((b.y + b.h - clip.y + PAD) * SCALE);
    if (x1 <= 0 || y1 <= 0 || x0 >= width || y0 >= height) continue;
    covered.push({
      kind: b.kind,
      text: b.label,
      context: b.context ?? '',
      box_px: [Math.max(0, x0), Math.max(0, y0), Math.min(width, x1), Math.min(height, y1)],
    });
  }
  mkdirSync(img, { recursive: true });
  writeFileSync(join(img, `${id}.png`), png);
  return {
    id,
    status: 'captured',
    role,
    address: address ?? blankTokens(page.url()),
    visible,
    headings: facts.h1,
    notices: facts.notices,
    without_webex: facts.present,
    crops,
    covered,
    size_px: [width, height],
    notes,
  };
}
