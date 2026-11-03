/**
 * An organisation's own identity provider, end to end (ADR 0083): the superuser registers the
 * development realm `andere` (dev/keycloak/andere-realm.json) for organisation `andere`, switches
 * it on and verifies the test domain `andere.test` (a test TLD needs no TXT record outside
 * production, point 12). Then staff of that organisation log in through the e-mail-first page
 * at their own realm, get rights in their organisation only (B1, B2), and an address outside
 * the provider's domain is refused (B3). The last test removes the provider again.
 *
 * Needs Keycloak with both realms and ORGANISATION_IDPS_ENABLED=true (the CI e2e stack). The
 * tests run in order: each builds on the registration of the first.
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { expectForbidden, loginAs, loginByAddress, requireKeycloak } from '../keycloak';

requireKeycloak();
test.describe.configure({ mode: 'serial' });
// One browser only: the spec registers one provider that every other spec would see.
test.skip(({ browserName }) => browserName !== 'chromium', 'shared registration: chromium only');

const PROVIDER = '/admin/organisations/andere/login/andere-idp';

test('the superuser registers the provider of andere and verifies its domain', async ({ page }) => {
  await loginAs(page, 'super', '/admin/organisations/andere/login/new');
  await page.locator('#key').fill('andere-idp');
  await page.locator('#name').fill('Andere gemeente');
  await page
    .locator('#discovery_url')
    .fill('http://keycloak:8080/realms/andere/.well-known/openid-configuration');
  await page.locator('#client_id').fill('openplanner-admin');
  await page.locator('#client_auth').selectOption('client_secret_basic');
  await page.locator('#client_secret').fill('dev-only-andere-secret');
  await page.getByRole('button', { name: 'Registreren' }).click();
  await expect(page).toHaveURL(new RegExp(`${PROVIDER}\\?done=idp_created`));

  await page.getByRole('button', { name: 'Aanzetten' }).click();
  await page.locator('#domain').fill('andere.test');
  await page.getByRole('button', { name: 'Domein toevoegen' }).click();
  await page.getByRole('button', { name: /^Controleren/ }).click();
  await expect(page).toHaveURL(/done=idp_domain_verified/);
  await expect(page.getByText('Geverifieerd', { exact: true })).toBeVisible();
  await expectNoAxeViolations(page);
});

test('staff of andere log in at their own realm and get rights there only', async ({ page }) => {
  await page.goto('/admin/login');
  await expect(page.getByLabel('Werk-e-mailadres of inlognaam')).toBeVisible();
  await expectNoAxeViolations(page);

  // `overmoedig` also claims superuser and demo: only org_admin of andere comes through.
  await loginByAddress(page, 'overmoedig@andere.test', 'overmoedig');
  await page.waitForURL((url) => url.pathname.startsWith('/admin'));
  const own = await page.goto('/admin/organisations/andere');
  expect(own?.status()).toBe(200);
  await expectForbidden(page, '/admin/organisations/demo');
  await expectForbidden(page, '/admin/settings');
});

test('an address outside the provider domain is refused', async ({ page }) => {
  // `buitendomein` signs in at realm andere, but its ID token says bas@openplanner.test.
  await loginByAddress(page, 'bas@andere.test', 'buitendomein');
  await page.waitForURL((url) => url.pathname === '/admin/callback');
  await expect(page.getByText('hoort niet bij een domein van deze inlogdienst')).toBeVisible();
});

test('the superuser removes the provider again', async ({ page }) => {
  await loginAs(page, 'super', PROVIDER);
  await page.getByRole('button', { name: 'Verwijderen', exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/organisations\/andere\/login\?done=removed/);
  await page.context().clearCookies();
  await page.goto('/admin/login');
  await expect(page).toHaveURL(new RegExp('^http://127.0.0.1:8080/'));
});
