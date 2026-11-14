/**
 * Lighthouse accessibility audit of every page variant in pages.json.
 * Fails (exit 1) when any variant scores below MIN_SCORE. Uses Playwright's Chromium, or the
 * browser at CHROME_PATH when set.
 */
import { chromium } from '@playwright/test';
import * as chromeLauncher from 'chrome-launcher';
import lighthouse from 'lighthouse';

import { pageVariants } from '../pages.ts';

const MIN_SCORE = 95;
const baseUrl = process.env.BASE_URL ?? 'http://127.0.0.1:8000';
const variants = pageVariants();

if (variants.length === 0) {
  console.log('No pages listed in pages.json; nothing to audit.');
  process.exit(0);
}

const chrome = await chromeLauncher.launch({
  chromePath: process.env.CHROME_PATH ?? chromium.executablePath(),
  chromeFlags: ['--headless=new', '--no-sandbox'],
});
// A page that opens only for the browser holding a cookie (pages.json `cookie`, the request page
// of ADR 0121) gets it in the browser's own cookie store, and Lighthouse keeps that store for it:
// an extra Cookie header did not reach the page (draft #826 scored the refusal: 0).
const cdp = await chromium.connectOverCDP(`http://127.0.0.1:${chrome.port}`);
const store = cdp.contexts()[0];
let failed = false;
try {
  for (const variant of variants) {
    const cookie = variant.cookie;
    if (cookie) await store.addCookies([{ ...cookie, url: baseUrl }]);
    const result = await lighthouse(new URL(variant.path, baseUrl).href, {
      port: chrome.port,
      onlyCategories: ['accessibility'],
      output: 'json',
      logLevel: 'error',
      disableStorageReset: Boolean(cookie),
    });
    const error = result?.lhr.runtimeError;
    const score = Math.round((result?.lhr.categories.accessibility.score ?? 0) * 100);
    const ok = error === undefined && score >= MIN_SCORE;
    failed ||= !ok;
    const why = error === undefined ? '' : ` (${error.code}: ${error.message})`;
    console.log(`${ok ? 'PASS' : 'FAIL'} ${score} ${variant.title}${why}`);
    if (cookie) await store.clearCookies();
  }
} finally {
  await cdp.close().catch(() => undefined);
  await chrome.kill();
}
process.exit(failed ? 1 : 0);
