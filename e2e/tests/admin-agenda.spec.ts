/**
 * The group agenda (ADR 0044) through the real identity provider: the group admin opens the
 * day view and the week view of their group in Dutch and English, reaches the week view with
 * the keyboard, and both pass axe (WCAG 2.2 AA). The outsider is refused.
 */
import { expect, test } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';
import { expectForbidden, loginAs, requireKeycloak } from '../keycloak';

requireKeycloak();

const AGENDA = '/admin/groups/demo/agenda';

for (const lang of ['nl', 'en']) {
  test(`group admin reads the day and week agenda (${lang})`, async ({ page }) => {
    await loginAs(page, 'groupadmin', AGENDA);
    // The login redirect keeps the path only; choose the language on the page itself.
    await page.goto(`${AGENDA}?lang=${lang}`);
    await expectNoAxeViolations(page);

    const week = page.getByRole('link', { name: lang === 'nl' ? 'Weekoverzicht' : 'Week view' });
    await tabTo(page, week);
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/agenda\/week/);
    await expect(page.getByRole('heading', { level: 2, name: lang === 'nl' ? /^Week van/ : /^Week of/ })).toBeVisible();
    await expectNoAxeViolations(page);
  });
}

test('an outsider sees no agenda of another organisation', async ({ page }) => {
  await loginAs(page, 'outsider', '/admin/');
  await expectForbidden(page, `${AGENDA}/week`);
});
