/**
 * Branding per organisation (WP3b) through the real identity provider: only the superuser may
 * change it (HANDOVER §6); the form saves with the keyboard, refuses a light colour with a
 * linked error, and passes axe (WCAG 2.2 AA). "Accentkleur" is matched exactly: "Tekst op accentkleur"
 * (ADR 0041) contains it.
 *
 * The three browsers run in parallel against one database: each uses its own colour on the
 * "andere" organisation, so the participant specs on "demo" are not affected.
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';
import { adminMenu, expectForbidden, loginAs, requireKeycloak } from '../keycloak';

requireKeycloak();

const PAGE = '/admin/organisations/andere/branding';
const COLOURS: Record<string, string> = {
  chromium: '#154273',
  firefox: '#7a1f5c',
  webkit: '#245e3a',
};

test('the superuser changes the branding with the keyboard', async ({ page }, testInfo) => {
  await loginAs(page, 'super', '/admin/branding');
  await expect(adminMenu(page).getByRole('link', { name: 'Huisstijl' })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole('link', { name: 'Andere gemeente' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Andere gemeente');
  await expectNoAxeViolations(page);

  const accent = page.getByLabel('Accentkleur', { exact: true });

  // A colour that white text cannot be read on is refused, with the error linked to the field.
  await accent.fill('#ffbf47');
  await tabTo(page, page.getByRole('button', { name: 'Opslaan' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('alert')).toBeFocused();
  await expect(accent).toHaveAttribute('aria-invalid', 'true');
  await expectNoAxeViolations(page);

  await accent.fill(COLOURS[testInfo.project.name] ?? '#000000');
  await tabTo(page, page.getByRole('button', { name: 'Opslaan' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status')).toContainText(/opgeslagen|niets gewijzigd/);
  await expectNoAxeViolations(page);
});

test('an organisation admin cannot change branding', async ({ page }) => {
  await loginAs(page, 'orgadmin');
  await expect(adminMenu(page).getByRole('link', { name: 'Huisstijl' })).toHaveCount(0);
  await expectForbidden(page, '/admin/organisations/demo/branding');
});
