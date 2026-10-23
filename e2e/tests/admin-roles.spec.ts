/**
 * The admin portal walked as every role of the authorisation matrix (ADR 0014), logging in
 * through the real identity provider: Keycloak with the dev realm in dev/keycloak/. Checks
 * what each role sees and is refused, that refusals land in the audit log, that logout ends
 * the identity-provider session, and axe (WCAG 2.2 AA) on every admin page.
 *
 * Needs Keycloak (`docker compose --profile dev up`, or the CI e2e job) and the demo seed.
 * Outside CI the spec is skipped when Keycloak is not reachable; in CI that is a failure.
 */
import { expect, type Page, test } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';

const KEYCLOAK_URL = process.env.KEYCLOAK_URL ?? 'http://127.0.0.1:8080';
// Test users exist only in the development realm (dev/keycloak/openplanner-realm.json).
const PASSWORD = process.env.KEYCLOAK_TEST_PASSWORD ?? 'dev-only-password';

type TestUser = 'super' | 'orgadmin' | 'groupadmin' | 'outsider' | 'norole';

test.beforeAll(async ({ request }) => {
  const discovery = `${KEYCLOAK_URL}/realms/openplanner/.well-known/openid-configuration`;
  const reachable = await request
    .get(discovery)
    .then((response) => response.ok())
    .catch(() => false);
  if (!reachable && process.env.CI) throw new Error(`Keycloak not reachable at ${discovery}`);
  test.skip(!reachable, 'Keycloak not running; start it with docker compose --profile dev up');
});

/** Open `path`, sign in at Keycloak as `user` and wait until the portal answers. */
async function loginAs(page: Page, user: TestUser, path = '/admin/'): Promise<void> {
  await page.goto(path);
  await expect(page).toHaveURL(new RegExp(`^${KEYCLOAK_URL}/`));
  await page.locator('#username').fill(user);
  await page.locator('#password').fill(PASSWORD);
  await page.locator('#password').press('Enter');
  await page.waitForURL((url) => url.pathname.startsWith('/admin'));
}

/** Log out with the keyboard, confirm at Keycloak and land on the logged-out page. */
async function logout(page: Page): Promise<void> {
  await tabTo(page, page.getByRole('button', { name: 'Uitloggen' }));
  await page.keyboard.press('Enter');
  await page.waitForURL(new RegExp(`^${KEYCLOAK_URL}/.*/logout`));
  await page.locator('#kc-logout').click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('U bent uitgelogd');
}

function adminMenu(page: Page) {
  return page.getByRole('navigation', { name: 'Beheermenu' });
}

async function expectForbidden(page: Page, path: string): Promise<void> {
  const response = await page.goto(path);
  expect(response?.status()).toBe(403);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Geen toegang');
}

test('group admin sees only their own group and no audit log', async ({ page }) => {
  await loginAs(page, 'groupadmin');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Overzicht');
  const groups = page.getByRole('table', { name: 'Uw groepen' }).locator('tbody tr');
  await expect(groups).toHaveCount(1);
  await expect(groups.first()).toContainText('Demo: videogesprek');
  await expect(adminMenu(page).getByRole('link', { name: 'Auditlog' })).toHaveCount(0);
  await expectNoAxeViolations(page);

  await page.getByRole('link', { name: 'Demo: videogesprek' }).click();
  await expect(page).toHaveURL(/\/admin\/groups\/demo$/);
  await expectNoAxeViolations(page);

  await adminMenu(page).getByRole('link', { name: 'Mijn rechten' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Mijn rechten');
  await expectNoAxeViolations(page);

  await adminMenu(page).getByRole('link', { name: 'Rechtenmatrix' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Rechtenmatrix');
  await expectNoAxeViolations(page);

  await expectForbidden(page, '/admin/groups/andere-team');
  await expect(adminMenu(page)).toBeVisible(); // the refusal stays inside the portal
  await expectNoAxeViolations(page);
  await expectForbidden(page, '/admin/audit');

  await logout(page);
  await expectNoAxeViolations(page);
});

test('organisation admin sees the logins of their own admins, by name', async ({ page }) => {
  await loginAs(page, 'groupadmin');
  await logout(page);
  await loginAs(page, 'orgadmin');

  await adminMenu(page).getByRole('link', { name: 'Auditlog' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Auditlog');
  const login = page.getByRole('row').filter({ hasText: 'Gina Groep' }).filter({ hasText: 'Inloggen' });
  await expect(login.first()).toContainText('demo / demo');
  // Logins in another organisation stay out of sight (refusals on demo itself do show).
  const outsiderLogins = page.getByRole('row').filter({ hasText: 'Otto Anders' }).filter({ hasText: 'Inloggen' });
  await expect(outsiderLogins).toHaveCount(0);
  await expectNoAxeViolations(page);
  await expectForbidden(page, '/admin/groups/andere-team');
});

test("an outsider's refusal shows up in the superuser's audit log", async ({ page }) => {
  // Deep link: after login the portal returns to the requested page, which is refused.
  await loginAs(page, 'outsider', '/admin/groups/demo');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Geen toegang');
  await logout(page);

  // Logout ended the Keycloak session too: the next visit asks for credentials again.
  await loginAs(page, 'super', '/admin/audit');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Auditlog');
  const refusal = page
    .getByRole('row')
    .filter({ hasText: 'Otto Anders' })
    .filter({ hasText: 'Groep bekijken' })
    .filter({ hasText: 'Geweigerd' })
    .filter({ hasText: 'demo / demo' });
  await expect(refusal.first()).toBeVisible();
  await expectNoAxeViolations(page);

  await page.goto('/admin/');
  await expect(page.getByRole('table', { name: 'Uw groepen' }).locator('tbody tr')).toHaveCount(2);
  await page.goto('/admin/?lang=en');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Overview');
  await expectNoAxeViolations(page);
});

test('a user without a role is told so and gets no menu', async ({ page }) => {
  await loginAs(page, 'norole');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Geen toegang');
  await expect(page.getByText('U heeft geen rol in het beheerportaal')).toBeVisible();
  await expect(adminMenu(page).getByRole('link')).toHaveCount(0);
  await expectNoAxeViolations(page);
});
