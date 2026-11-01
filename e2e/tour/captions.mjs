/**
 * Captions for the recorded walkthrough (ADR 0087 step 3): reads the chapter marks the spec
 * wrote (tour-output/tour.marks.json) and the texts of chapters.json, and writes
 * tour-output/tour.nl.vtt, tour.en.vtt and tour.json (when, which version, how long, and which
 * chapters have a still, tour-<chapter>.jpg, in the order of the story).
 * Usage: node tour/captions.mjs [version]
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs';

const out = new URL('../tour-output/', import.meta.url);
const { chapters } = JSON.parse(readFileSync(new URL('./chapters.json', import.meta.url), 'utf8'));
const { marks, end } = JSON.parse(readFileSync(new URL('tour.marks.json', out), 'utf8'));

/** Seconds as a WebVTT timestamp, hh:mm:ss.mmm. */
export function stamp(seconds) {
  const ms = Math.max(0, Math.round(seconds * 1000));
  const pad = (n, w = 2) => String(n).padStart(w, '0');
  return `${pad(Math.floor(ms / 3600000))}:${pad(Math.floor(ms / 60000) % 60)}:${pad(Math.floor(ms / 1000) % 60)}.${pad(ms % 1000, 3)}`;
}

/** One WebVTT document: a cue per chapter, from its mark to the next one (the last to the end). */
export function vtt(locale) {
  const text = Object.fromEntries(chapters.map((c) => [c.key, c[locale]]));
  const cues = marks.map((m, i) => {
    const until = i + 1 < marks.length ? marks[i + 1].at : end;
    return `${i + 1}\n${stamp(m.at)} --> ${stamp(until)}\n${text[m.key]}\n`;
  });
  return `WEBVTT\n\n${cues.join('\n')}`;
}

for (const locale of ['nl', 'en']) writeFileSync(new URL(`tour.${locale}.vtt`, out), vtt(locale));
writeFileSync(
  new URL('tour.json', out),
  `${JSON.stringify(
    {
      version: process.argv[2] ?? '',
      recorded: new Date().toISOString().slice(0, 10),
      seconds: Math.round(end),
      screens: chapters.map((c) => c.key).filter((key) => existsSync(new URL(`tour-${key}.jpg`, out))),
    },
    null,
    2,
  )}\n`,
);
console.log(`captions for ${marks.length} chapters, ${Math.round(end)} s`);
