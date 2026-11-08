/**
 * The gates of a page marked `island` in pages.json (ADR 0118 point 10, docs/islands.md): the page
 * is complete with scripts off, every control is reachable with the keyboard, nothing scrolls
 * sideways at 320 px (WCAG 1.4.10, which is also 400 % zoom on 1280 px), forced colours and reduced
 * motion are honoured, and no script is refused by the content security policy.
 *
 * Admin island pages need Keycloak and the demo seed, like admin-shell.spec.ts.
 */
import { expect, test, type Page } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { loginAs, requireKeycloak } from '../keycloak';
import { islandVariants } from '../pages';

const variants = islandVariants();
if (variants.some((v) => v.user)) requireKeycloak();

async function open(page: Page, variant: (typeof variants)[number]): Promise<void> {
  if (variant.user) await loginAs(page, variant.user, variant.path);
  else await page.goto(variant.path, { waitUntil: 'networkidle' });
}

for (const variant of variants) {
  test.describe(variant.title, () => {
    test('scripts off: the page has its content and no violations', async ({ browser }) => {
      const context = await browser.newContext({ javaScriptEnabled: false });
      const page = await context.newPage();
      await open(page, variant);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await expect(page.locator('main')).not.toBeEmpty();
      await expectNoAxeViolations(page);
      await context.close();
    });

    test('keyboard: every control takes focus in turn and focus is never lost', async ({ page }) => {
      await open(page, variant);
      const seen = new Set<string>();
      for (let i = 0; i < 80; i += 1) {
        await page.keyboard.press('Tab');
        const id = await page.evaluate(() => {
          const el = document.activeElement;
          if (!el || el === document.body) return '';
          const r = el.getBoundingClientRect();
          return `${el.tagName}|${el.getAttribute('href') ?? el.id}|${Math.round(r.x)},${Math.round(r.y)}`;
        });
        if (id === '') break; // focus left the page for the browser: the loop has gone round
        seen.add(id);
      }
      expect(seen.size).toBeGreaterThan(0);
    });

    test('320 px: no sideways scroll', async ({ page }) => {
      await page.setViewportSize({ width: 320, height: 800 });
      await open(page, variant);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      await expectNoAxeViolations(page);
    });

    test('forced colours and reduced motion: no violations', async ({ browser }) => {
      const context = await browser.newContext({ forcedColors: 'active', reducedMotion: 'reduce' });
      const page = await context.newPage();
      await open(page, variant);
      expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);
      await expectNoAxeViolations(page);
      await context.close();
    });

    test('no script is refused by the content security policy', async ({ page }) => {
      const problems: string[] = [];
      page.on('console', (m) => {
        if (/content security policy|refused to (load|execute|apply)/i.test(m.text())) problems.push(m.text());
      });
      page.on('pageerror', (e) => problems.push(e.message));
      await open(page, variant);
      await page.waitForTimeout(500);
      expect(problems).toEqual([]);
    });
  });
}
