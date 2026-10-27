/**
 * The admin portal walked as every role of the authorisation matrix (ADR 0014), logging in
 * through the real identity provider: Keycloak with the dev realm in dev/keycloak/. Checks
 * what each role sees and is refused, that refusals land in the audit log, that logout ends
 * the identity-provider session, and axe (WCAG 2.2 AA) on every admin page.
 *
 * Needs Keycloak (`docker compose --profile dev up`, or the CI e2e job) and the demo seed.
 * Outside CI the spec is skipped when Keycloak is not reachable; in CI that is a failure.
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { adminMenu, expectForbidden, loginAs, logout, requireKeycloak } from '../keycloak';

requireKeycloak();

test('group admin sees only their own group and its own audit log', async ({ page }) => {
  await loginAs(page, 'groupadmin');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Overzicht');
  const groups = page.getByRole('list', { name: 'Uw groepen' }).getByRole('listitem');
  await expect(groups).toHaveCount(1);
  await expect(groups.first()).toContainText('Demo: videogesprek');
  await expectNoAxeViolations(page);

  await page.getByRole('link', { name: 'Demo: videogesprek', exact: true }).click();
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

  // ADR 0031: the group admin's log holds the own group only, never the organisation or others.
  await adminMenu(page).getByRole('link', { name: 'Auditlog' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Auditlog');
  await expect(page.getByRole('row').filter({ hasText: 'andere-team' })).toHaveCount(0);
  await expect(page.getByRole('row').filter({ hasText: 'demo / demo' }).first()).toBeVisible();
  await page.getByLabel('Alleen problemen').check();
  await page.getByRole('button', { name: 'Toepassen' }).click();
  await expect(page).toHaveURL(/problems=1/);
  await expectNoAxeViolations(page);

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

test('auditor reads the matrix and the own log, but no bookings and no changes', async ({ page }) => {
  // ADR 0037: compliance and privacy, read-only, never personal data.
  await loginAs(page, 'auditor');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Overzicht');
  await expect(page.getByRole('link', { name: 'Demo: videogesprek', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Andere: team', exact: true })).toHaveCount(0);

  await adminMenu(page).getByRole('link', { name: 'Rechtenmatrix' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Rechtenmatrix');
  await expect(page.getByRole('columnheader', { name: 'Compliance en privacy (alleen lezen)' })).toBeVisible();
  await expect(page.getByText('Alleen superuser').first()).toBeVisible();
  await expect(page.getByText('Persoonsgegevens').first()).toBeVisible();
  await expectNoAxeViolations(page);

  await adminMenu(page).getByRole('link', { name: 'Auditlog' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Auditlog');
  await expect(page.getByRole('row').filter({ hasText: 'andere-team' })).toHaveCount(0);
  await expectNoAxeViolations(page);

  await expectForbidden(page, '/admin/groups/demo/agenda');
  await expectForbidden(page, '/admin/groups/demo/settings');
  await expectForbidden(page, '/admin/groups/andere-team');
  await logout(page);
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
  // Both seeded groups, in both organisations (other specs may have added more).
  const groups = page.getByRole('list', { name: 'Uw groepen' });
  await expect(groups.getByRole('link', { name: 'Demo: videogesprek', exact: true })).toBeVisible();
  await expect(groups.getByRole('link', { name: 'Andere: team', exact: true })).toBeVisible();
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
