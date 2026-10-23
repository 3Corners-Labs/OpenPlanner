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
let failed = false;
try {
  for (const variant of variants) {
    const result = await lighthouse(new URL(variant.path, baseUrl).href, {
      port: chrome.port,
      onlyCategories: ['accessibility'],
      output: 'json',
      logLevel: 'error',
    });
    const score = Math.round((result?.lhr.categories.accessibility.score ?? 0) * 100);
    const ok = score >= MIN_SCORE;
    failed ||= !ok;
    console.log(`${ok ? 'PASS' : 'FAIL'} ${score} ${variant.title}`);
  }
} finally {
  await chrome.kill();
}
process.exit(failed ? 1 : 0);
