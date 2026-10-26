/**
 * API clients of contact-center bots (ADR 0023) through the real identity provider: only the
 * superuser sees and manages them; registering works with the keyboard, the secret is shown
 * once, refusals are linked to their field, and every page passes axe (WCAG 2.2 AA).
 *
 * The three browsers run in parallel against one database; each registers its own client.
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';
import { adminMenu, expectForbidden, loginAs, requireKeycloak } from '../keycloak';

requireKeycloak();

test('the superuser registers a bot with the keyboard and sees the secret once', async ({
  page,
}, testInfo) => {
  await loginAs(page, 'super', '/admin/api');
  await expect(adminMenu(page).getByRole('link', { name: 'API-koppelingen' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('API-koppelingen');
  await expectNoAxeViolations(page);

  await page.getByRole('link', { name: 'Nieuwe API-client' }).click();
  await expectNoAxeViolations(page);

  // Without a scope the form is refused, with the error linked to the scopes fieldset.
  await page.getByLabel('Naam').fill(`E2E bot ${testInfo.project.name}`);
  await tabTo(page, page.getByRole('button', { name: 'Client aanmaken' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('alert')).toBeFocused();
  await expectNoAxeViolations(page);

  await page.getByLabel('Vrije tijden lezen').check();
  await tabTo(page, page.getByRole('button', { name: 'Client aanmaken' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Gegevens voor de koppeling');
  await expect(page.locator('code.secret-value').nth(1)).toHaveText(/^ops_/);
  await expectNoAxeViolations(page);

  // The detail page never shows the secret again.
  const secret = await page.locator('code.secret-value').nth(1).innerText();
  await page.getByRole('link', { name: 'Ik heb het secret opgeslagen' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(
    `E2E bot ${testInfo.project.name}`,
  );
  await expect(page.locator('body')).not.toContainText(secret);
  await expectNoAxeViolations(page);
});

test('an organisation admin cannot see or open the API clients', async ({ page }) => {
  await loginAs(page, 'orgadmin');
  await expect(adminMenu(page).getByRole('link', { name: 'API-koppelingen' })).toHaveCount(0);
  await expectForbidden(page, '/admin/api');
});
