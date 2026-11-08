/**
 * Admin pages on an ordinary laptop screen, 1280 × 800 with the full menu, in Dutch and English:
 * the page never scrolls sideways, no table cuts off its last column, and nothing runs past its
 * card, widget or fact (layout.mjs). The display faults this guards were found while capturing
 * the manual's screenshots: an issuer address running over the fact beside it, the new-role
 * permissions in one row 7,000 px wide, a proxy finding running off the overview's card, and the
 * agenda's day and week, the rights matrix and the audit log cutting off their last column.
 *
 * Needs Keycloak and the demo and journey seeds, like admin-shell.spec.ts.
 */
import { expect, test } from '@playwright/test';

import { loginAs, requireKeycloak, type TestUser } from '../keycloak';
import { layoutProblems } from '../layout.mjs';
import { journey } from '../pages';

requireKeycloak();

test.use({ viewport: { width: 1280, height: 800 } });

/** The pages each user opens; the agenda shows the day of this browser's seeded call. */
function pagesOf(user: TestUser, project: string): string[] {
  if (user === 'auditor') return ['/admin/'];
  const day = new URL(journey(project).call_agenda, 'http://localhost').searchParams.get('day');
  return [
    '/admin/',
    '/admin/settings',
    '/admin/idp-mapping',
    '/admin/roles/new',
    '/admin/groups/demo',
    `/admin/groups/demo/agenda?day=${day}`,
    `/admin/groups/demo/agenda/week?week=${day}`,
    '/admin/matrix',
    '/admin/audit',
  ];
}

for (const lang of ['nl', 'en']) {
  for (const user of ['super', 'auditor'] as const) {
    test(`${user} (${lang}): nothing runs past its box at 1280 px`, async ({ page }, testInfo) => {
      await loginAs(page, user);
      const found: Record<string, string[]> = {};
      for (const path of pagesOf(user, testInfo.project.name)) {
        await page.goto(`${path}${path.includes('?') ? '&' : '?'}lang=${lang}`);
        await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
        const problems = await page.evaluate(layoutProblems);
        if (problems.length) found[path] = problems;
      }
      expect(found).toEqual({});
    });
  }
}
