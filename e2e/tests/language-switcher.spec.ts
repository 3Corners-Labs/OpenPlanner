/**
 * Keyboard-only use of the language switcher (no mouse): skip link, switching language,
 * remembering the choice, and browser-language detection.
 */
import { expect, test } from '@playwright/test';

test('switch to English and back with the keyboard only', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('lang', 'nl');

  // First Tab reaches the skip link, then the site name is skipped (not focusable).
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Naar de inhoud' })).toBeFocused();

  const english = page.getByRole('navigation', { name: 'Kies een taal' }).getByRole('link', { name: 'English' });
  while (!(await english.evaluate((el) => el === document.activeElement))) {
    await page.keyboard.press('Tab');
  }
  await page.keyboard.press('Enter');

  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Schedule a video call');

  // The choice is remembered without the query parameter.
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');

  const dutch = page.getByRole('navigation', { name: 'Choose a language' }).getByRole('link', { name: 'Nederlands' });
  await dutch.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('lang', 'nl');
});

test('skip link moves focus to the main content', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');

  await expect(page.locator('main')).toBeFocused();
});

test.describe('browser language', () => {
  test.use({ locale: 'en-GB' });

  test('is detected on the first visit', async ({ page }) => {
    await page.goto('/');

    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await expect(page.getByRole('link', { name: 'English' })).toHaveAttribute('aria-current', 'true');
  });
});
