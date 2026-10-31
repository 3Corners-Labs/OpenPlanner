/**
 * The admin shell at phone, tablet and desktop width (backlog SH-1, ADR 0047): axe (WCAG 2.2 AA)
 * on every admin page in pages.json at 375, 768 and 1280 px, and the menu usable with the
 * keyboard alone at each width. On tablet the side navigation is an icon rail whose label shows
 * on focus; on phone the bottom bar carries the first entries and the rest open from *More*, a
 * <details> without script. On tablet and desktop the top bar's menu button collapses the menu
 * to the rail or expands it, a form without script that holds for the session (#208).
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

    if (name !== 'phone') {
      test('the menu button collapses or expands the menu for the session', async ({ page }) => {
        await loginAs(page, 'super');
        const shell = page.locator('.admin-shell');
        const button = page.getByRole('button', { name: 'Menu in- of uitklappen' });
        const menu = adminMenu(page);
        // The rail is 3.75 rem wide, the full menu 15 rem; the label stays in the page either
        // way (clipped to a pixel on the rail), so the width tells the state, not visibility.
        const expectRail = async (rail: boolean) => {
          const box = await menu.boundingBox();
          expect(box).not.toBeNull();
          if (rail) expect(box!.width).toBeLessThan(100);
          else expect(box!.width).toBeGreaterThan(200);
          await expect(button).toHaveAttribute('aria-expanded', String(!rail));
        };
        // The tablet starts with the rail, the desktop with the full menu.
        const startsCollapsed = name === 'tablet';
        await expectRail(startsCollapsed);

        await tabTo(page, button);
        await page.keyboard.press('Enter');
        await expect(shell).toHaveClass(new RegExp(startsCollapsed ? 'nav-expanded' : 'nav-collapsed'));
        await expectRail(!startsCollapsed);
        await expectNoAxeViolations(page);

        // The choice holds on the next page, and the button turns the menu back.
        await page.goto('/admin/audit');
        await expectRail(!startsCollapsed);
        await button.click();
        await expect(shell).not.toHaveClass(new RegExp(startsCollapsed ? 'nav-expanded' : 'nav-collapsed'));
        await expectRail(startsCollapsed);
      });
    }
  });
}
