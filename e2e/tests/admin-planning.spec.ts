/**
 * Groups and planning (WP7b) walked per role through the real identity provider: the org
 * admin creates a group and sets its hours; the group admin manages opening hours and
 * exceptions of their own group but not its settings; the outsider is refused; the
 * superuser sees the changes in the audit log. axe (WCAG 2.2 AA) on every page and form.
 *
 * The three browsers run in parallel against one database, so every browser uses its own
 * times, dates and slugs, and removes what it added.
 */
import { expect, type Page, test, type TestInfo } from '@playwright/test';

import { expectNoAxeViolations } from '../helpers';
import { expectForbidden, loginAs, logout, requireKeycloak } from '../keycloak';

requireKeycloak();

const GROUP = '/admin/groups/demo';

interface BrowserPlan {
  /** Saturday block of this browser (no overlap with the other browsers). */
  opens: string;
  closes: string;
  /** Days ahead for this browser's exception. */
  daysAhead: number;
}

const PLANS: Record<string, BrowserPlan> = {
  chromium: { opens: '06:00', closes: '07:00', daysAhead: 40 },
  firefox: { opens: '07:00', closes: '08:00', daysAhead: 41 },
  webkit: { opens: '18:00', closes: '19:00', daysAhead: 42 },
};

function plan(testInfo: TestInfo): BrowserPlan {
  return PLANS[testInfo.project.name] ?? PLANS.chromium;
}

/** A date `days` ahead as the form value and as the portal writes it (Dutch, full). */
function dayAhead(days: number): { value: string; label: string } {
  const date = new Date(Date.now() + days * 86_400_000);
  date.setUTCHours(12, 0, 0, 0);
  const label = new Intl.DateTimeFormat('nl-NL', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    timeZone: 'Europe/Amsterdam',
  }).format(date);
  return { value: date.toISOString().slice(0, 10), label };
}

function tabs(page: Page) {
  return page.getByRole('navigation', { name: 'Onderdelen van de groep' });
}

test('group admin manages opening hours and exceptions, not the settings', async ({ page }, testInfo) => {
  const mine = plan(testInfo);
  await loginAs(page, 'groupadmin', GROUP);

  await expect(tabs(page).getByRole('link', { name: 'Openingstijden' })).toBeVisible();
  await expect(tabs(page).getByRole('link', { name: 'Instellingen' })).toHaveCount(0);
  await expectNoAxeViolations(page);
  await expectForbidden(page, `${GROUP}/settings`);

  // Opening hours: add this browser's Saturday block, see it, remove it again.
  await page.goto(`${GROUP}/hours`);
  await expectNoAxeViolations(page);
  await page.getByLabel('Dag', { exact: true }).selectOption({ label: 'zaterdag' });
  await page.getByLabel('Van', { exact: true }).fill(mine.opens);
  await page.getByLabel('Tot', { exact: true }).fill(mine.closes);
  await page.getByLabel('Gelijktijdige gesprekken').fill('2');
  await page.getByRole('button', { name: 'Blok toevoegen' }).press('Enter');
  await expect(page.getByRole('status')).toHaveText('Toegevoegd.');
  const block = `${mine.opens}–${mine.closes}`;
  const saturday = page.getByRole('listitem').filter({ has: page.getByRole('heading', { name: 'zaterdag' }) });
  await expect(saturday).toContainText(block);
  await expectNoAxeViolations(page);
  await page.getByRole('button', { name: `Blok zaterdag ${block} verwijderen` }).press('Enter');
  await expect(page.getByRole('status')).toHaveText('Verwijderd.');
  await expect(saturday).not.toContainText(block);

  // An overlapping block is explained and linked to its field.
  await page.getByLabel('Dag', { exact: true }).selectOption({ label: 'maandag' });
  await page.getByLabel('Van', { exact: true }).fill('10:00');
  await page.getByLabel('Tot', { exact: true }).fill('11:00');
  await page.getByRole('button', { name: 'Blok toevoegen' }).press('Enter');
  const summary = page.getByRole('alert');
  await expect(summary).toContainText('Deze tijden overlappen');
  await expectNoAxeViolations(page);

  // Exceptions: close this browser's day, see it, remove it again.
  const day = dayAhead(mine.daysAhead);
  await tabs(page).getByRole('link', { name: 'Uitzonderingen' }).click();
  await expectNoAxeViolations(page);
  await page.getByLabel('Datum', { exact: true }).fill(day.value);
  await page.getByLabel('Hele dag gesloten').check();
  await page.getByRole('button', { name: 'Uitzondering toevoegen' }).press('Enter');
  await expect(page.getByRole('status')).toHaveText('Toegevoegd.');
  await expect(page.getByRole('row', { name: new RegExp(day.label) })).toContainText('Gesloten');
  await expectNoAxeViolations(page);
  await page.getByRole('button', { name: `Uitzondering op ${day.label} verwijderen` }).press('Enter');
  await expect(page.getByRole('status')).toHaveText('Verwijderd.');
  await expect(page.getByRole('row', { name: new RegExp(day.label) })).toHaveCount(0);

  await logout(page);
});

test('organisation admin creates a group and gives it opening hours', async ({ page }, testInfo) => {
  const slug = `e2e-${testInfo.project.name}-${Date.now().toString(36)}`;
  await loginAs(page, 'orgadmin');

  await page.getByRole('link', { name: 'Nieuwe groep' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Nieuwe groep');
  await expectNoAxeViolations(page);
  await page.getByLabel('Naam', { exact: true }).fill(`E2E ${testInfo.project.name}`);
  await page.getByLabel('Webadres', { exact: true }).fill(slug);
  await page.getByLabel('Duur van een gesprek (minuten)').fill('20');
  await page.getByRole('button', { name: 'Groep aanmaken' }).press('Enter');

  await expect(page).toHaveURL(new RegExp(`/admin/groups/${slug}\\?done=created$`));
  await expect(page.getByRole('status')).toContainText('De groep is aangemaakt');
  await expect(page.getByText('Nog geen tijdsloten')).toBeVisible();
  await expectNoAxeViolations(page);

  await tabs(page).getByRole('link', { name: 'Instellingen' }).click();
  await expect(page.getByLabel('Webadres', { exact: true })).toHaveAttribute('readonly', '');
  await expectNoAxeViolations(page);

  await tabs(page).getByRole('link', { name: 'Openingstijden' }).click();
  await page.getByLabel('Dag', { exact: true }).selectOption({ label: 'dinsdag' });
  await page.getByLabel('Van', { exact: true }).fill('09:00');
  await page.getByLabel('Tot', { exact: true }).fill('10:00');
  await page.getByLabel('Gelijktijdige gesprekken').fill('3');
  await page.getByRole('button', { name: 'Blok toevoegen' }).press('Enter');
  await expect(page.getByRole('status')).toHaveText('Toegevoegd.');

  await tabs(page).getByRole('link', { name: 'Overzicht' }).click();
  await expect(page.getByText('3 tijdsloten, 09:00 tot 09:40, tot 3 tegelijk').first()).toBeVisible();
});

test('an organisation admin of another organisation cannot touch the planning', async ({ page }) => {
  await loginAs(page, 'outsider', `${GROUP}/hours`);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Geen toegang');
  await expectForbidden(page, `${GROUP}/exceptions`);
  await expectForbidden(page, `${GROUP}/settings`);
});

test('the superuser sees planning changes in the audit log', async ({ page }) => {
  await loginAs(page, 'super', '/admin/audit');
  const change = page
    .getByRole('row')
    .filter({ hasText: 'Openingstijden en uitzonderingen beheren' })
    .filter({ hasText: 'demo / demo' });
  await expect(change.first()).toBeVisible();
  await expect(page.getByRole('row').filter({ hasText: 'Groepen aanmaken' }).first()).toBeVisible();
});
