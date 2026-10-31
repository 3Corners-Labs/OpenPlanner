/**
 * The admin shell at phone, tablet and desktop width (backlog SH-1, ADR 0047): axe (WCAG 2.2 AA)
 * on every admin page in pages.json at 375, 768 and 1280 px, and the menu usable with the
 * keyboard alone at each width. On tablet the side navigation is an icon rail whose label shows
 * on focus; on phone the bottom bar carries the first entries and the rest open from *More*, a
 * <details> without script.
 *
 * Needs Keycloak and the demo seed, like admin-roles.spec.ts.
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';
import { adminMenu, loginAs, requireKeycloak } from '../keycloak';
import { adminPageVariants } from '../pages';

requireKeycloak();

const WIDTHS = { phone: 375, tablet: 768, desktop: 1280 } as const;

for (const [name, width] of Object.entries(WIDTHS)) {
  test.describe(`${name} (${width} px)`, () => {
    test.use({ viewport: { width, height: 900 } });

    for (const variant of adminPageVariants()) {
      test(`axe: ${variant.title}`, async ({ page }) => {
        await loginAs(page, variant.user, variant.path);
        await expectNoAxeViolations(page);
      });
    }

    test('the menu works with the keyboard alone', async ({ page }) => {
      await loginAs(page, 'super');
      const menu = adminMenu(page);
      await expect(menu).toBeVisible();

      if (name === 'phone') {
        // The first entries sit in the bottom bar; the rest wait behind More.
        await expect(menu.getByRole('link', { name: 'Auditlog' })).toBeVisible();
        const staff = menu.getByRole('link', { name: 'Medewerkers' });
        await expect(staff).toBeHidden();
        const more = menu.locator('details.admin-bottombar__more > summary');
        await tabTo(page, more);
        await page.keyboard.press('Enter');
        await expect(staff).toBeVisible();
        await expectNoAxeViolations(page);
        await tabTo(page, staff);
      } else {
        const staff = menu.getByRole('link', { name: 'Medewerkers' });
        await tabTo(page, staff);
        // The rail hides the label visually until the entry has focus (its accessible name
        // stays); the desktop menu shows it at all times.
        await expect(staff.locator('span')).toBeVisible();
      }
      await page.keyboard.press('Enter');
      await expect(page).toHaveURL(/\/admin\/staff$/);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText('Medewerkers');
      // The current entry is marked where it shows: in the menu, or on More when it holds it.
      const current = name === 'phone'
        ? menu.locator('details.admin-bottombar__more:has(a[aria-current="page"]) > summary')
        : menu.locator('a[aria-current="page"]');
      await expect(current).toBeVisible();
      await expectNoAxeViolations(page);
    });
  });
}
