/**
 * Admin steps through the development identity provider (Keycloak, dev/keycloak/): skip or
 * fail when it is not running, log in as a test user, log out with the keyboard.
 */
import { expect, type Page, test } from '@playwright/test';

import { tabTo } from './helpers';

export const KEYCLOAK_URL = process.env.KEYCLOAK_URL ?? 'http://127.0.0.1:8080';
// Test users exist only in the development realm (dev/keycloak/openplanner-realm.json).
const PASSWORD = process.env.KEYCLOAK_TEST_PASSWORD ?? 'dev-only-password';

export type TestUser = 'super' | 'orgadmin' | 'groupadmin' | 'auditor' | 'outsider' | 'norole';

/** Skip the spec outside CI when Keycloak is not running; in CI that is a failure. */
export function requireKeycloak(): void {
  test.beforeAll(async ({ request }) => {
    const discovery = `${KEYCLOAK_URL}/realms/openplanner/.well-known/openid-configuration`;
    const reachable = await request
      .get(discovery)
      .then((response) => response.ok())
      .catch(() => false);
    if (!reachable && process.env.CI) throw new Error(`Keycloak not reachable at ${discovery}`);
    test.skip(!reachable, 'Keycloak not running; start it with docker compose --profile dev up');
  });
}

/** Open `path`, sign in at Keycloak as `user` and wait until the portal answers. */
export async function loginAs(page: Page, user: TestUser, path = '/admin/'): Promise<void> {
  await page.goto(path);
  await expect(page).toHaveURL(new RegExp(`^${KEYCLOAK_URL}/`));
  await page.locator('#username').fill(user);
  await page.locator('#password').fill(PASSWORD);
  await page.locator('#password').press('Enter');
  await page.waitForURL((url) => url.pathname.startsWith('/admin'));
}

/**
 * Log out with the keyboard: open the profile menu in the top bar (ADR 0047), press the log-out
 * button, confirm at Keycloak and land on the logged-out page.
 */
export async function logout(page: Page): Promise<void> {
  await tabTo(page, page.locator('details.admin-profile > summary'));
  await page.keyboard.press('Enter');
  await tabTo(page, page.getByRole('button', { name: 'Uitloggen' }));
  await page.keyboard.press('Enter');
  await page.waitForURL(new RegExp(`^${KEYCLOAK_URL}/.*/logout`));
  await page.locator('#kc-logout').click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('U bent uitgelogd');
}

/** The portal's side navigation. */
export function adminMenu(page: Page) {
  return page.getByRole('navigation', { name: 'Beheermenu' });
}

/** Open `path` and expect the portal's refusal page. */
export async function expectForbidden(page: Page, path: string): Promise<void> {
  const response = await page.goto(path);
  expect(response?.status()).toBe(403);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Geen toegang');
}
