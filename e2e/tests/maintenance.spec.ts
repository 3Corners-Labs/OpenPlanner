/**
 * The citizen maintenance page (ADR 0038, MM-3): axe (WCAG 2.2 AA) with and without a contact
 * block, in Dutch and English, and the admin side of switching it.
 *
 * Maintenance closes every citizen page of the installation, so this spec runs in its own CI
 * step after the others (MAINTENANCE_E2E=1) and switches maintenance off again at the end.
 * Needs Keycloak and the demo seed, like admin-roles.spec.ts.
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { loginAs, requireKeycloak } from '../keycloak';

test.describe.configure({ mode: 'serial' });
test.skip(!process.env.MAINTENANCE_E2E, 'runs in its own step: maintenance closes every page');
requireKeycloak();

const PAGES: [string, string][] = [
  ['without contact block', '/g/demo'],
  ['with contact block', '/g/andere-team'],
  ['under a management link', `/b/${'A'.repeat(43)}/cancel`],
];
const TITLES: Record<string, string> = {
  nl: 'We voeren onderhoud uit',
  en: 'We are carrying out maintenance',
};

test('superuser gives one organisation a contact block and switches maintenance on', async ({
  page,
}) => {
  await loginAs(page, 'super', '/admin/organisations/andere/contacts');
  await page.locator('#contact_1_kind').selectOption('phone');
  await page.locator('#contact_1_value').fill('14 020');
  await page.getByRole('button', { name: 'Opslaan' }).click();

  await page.goto('/admin/maintenance');
  await page.locator('#reason').selectOption('update');
  await page.getByRole('button', { name: 'Onderhoudsmodus aanzetten' }).click();
  await expect(page).toHaveURL(/done=maintenance_on/);
  await expect(page.getByRole('note', { name: 'Onderhoudsmodus' })).toBeVisible();
  await expectNoAxeViolations(page);
});

for (const [name, path] of PAGES) {
  for (const locale of ['nl', 'en']) {
    test(`axe: maintenance page ${name} [${locale}]`, async ({ page }) => {
      const response = await page.goto(`${path}?lang=${locale}`);
      expect(response?.status()).toBe(503);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(TITLES[locale]);
      await expectNoAxeViolations(page);
    });
  }
}

test('superuser switches maintenance off and the booking page is back', async ({ page }) => {
  await loginAs(page, 'super', '/admin/maintenance');
  await page.getByRole('button', { name: 'Onderhoudsmodus uitzetten' }).click();
  await expect(page).toHaveURL(/done=maintenance_off/);

  const response = await page.goto('/g/demo');
  expect(response?.status()).toBe(200);
});
