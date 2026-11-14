/**
 * The call page before the click that joins (ADR 0123 step 4b): the start state, the device check
 * with the browser's fake camera and microphone, a refused permission, the keyboard alone, 320 px,
 * reduced motion and no script. Nothing here reaches the video platform: the page has no SDK file
 * yet, and the join button leads to a state that says the call is not available in this page yet.
 *
 * Needs an installation with the platform switch `embedded_call_enabled` on and one confirmed
 * booking whose emailed link (`/b/{token}`) is passed in CALL_PAGE_LINK; the link trades its token
 * for the participant's cookie and the spec opens the call page of that booking. Without
 * CALL_PAGE_LINK every test is skipped. The fake devices are Chromium's; the refused permission is
 * played in every browser by a stand-in for getUserMedia that rejects the way a browser does.
 * axe (WCAG 2.2 AA) runs in every state.
 */
import { expect, test, type Page } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';

const LINK = process.env.CALL_PAGE_LINK;

test.skip(!LINK, 'CALL_PAGE_LINK (the emailed link of a booking, switch embedded_call_enabled on) is not set');
test.use({
  locale: 'en-US',
  permissions: ['camera', 'microphone'],
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
});

/** Open the booking's call page in English; the emailed link sets the participant's cookie first. */
async function openCallPage(page: Page): Promise<void> {
  await page.goto(LINK ?? '');
  await page.goto(`${new URL(page.url()).pathname.replace(/\/$/, '')}/call?lang=en`);
  await expect(page.getByRole('heading', { level: 1, name: 'Your video call' })).toBeVisible();
}

/** Count every call to getUserMedia, to prove none is made before the click. */
async function countDeviceRequests(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { deviceRequests: number };
    w.deviceRequests = 0;
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = (constraints) => {
      w.deviceRequests += 1;
      return original(constraints);
    };
  });
}

/** Make the browser refuse `refuse` (the camera, the microphone or both), as one that has them blocked. */
async function refuse(page: Page, refuse: 'camera' | 'microphone' | 'both'): Promise<void> {
  await page.addInitScript((what) => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = (constraints) => {
      const blocked =
        (constraints.video && what !== 'microphone') || (constraints.audio && what !== 'camera');
      if (blocked) return Promise.reject(new DOMException('blocked', 'NotAllowedError'));
      return original(constraints);
    };
  }, refuse);
}

const focusedText = (page: Page): Promise<string> =>
  page.evaluate(() => document.activeElement?.textContent?.trim() ?? '');

test('the start state shows the appointment and one button, and asks for no device on load', async ({ page }) => {
  await countDeviceRequests(page);
  await openCallPage(page);
  await expect(page.getByRole('button', { name: 'Test camera and microphone' })).toBeVisible();
  await expect(page.getByText('a browser with JavaScript, a camera and a microphone')).toBeHidden();
  await expect(page.locator('dl.details')).toContainText('Team');
  await page.waitForTimeout(500);
  expect(await page.evaluate(() => (window as unknown as { deviceRequests: number }).deviceRequests)).toBe(0);
  await expectNoAxeViolations(page);
});

test('the device check: picture, meter, test sound, devices and toggles', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'the fake devices are Chromium flags');
  await openCallPage(page);
  await page.getByRole('button', { name: 'Test camera and microphone' }).click();

  const heading = page.getByRole('heading', { level: 1, name: 'Check picture and sound' });
  await expect(heading).toBeFocused();
  await expect(page.getByLabel('Your own picture')).toBeVisible();
  await expect(page.getByRole('status')).toHaveText('Your camera and microphone are ready.');

  // The meter's words say it too: the fake microphone beeps, so we hear it after a while.
  await expect(page.getByText('We hear you.')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByLabel('Camera', { exact: true })).not.toBeEmpty();
  await expect(page.getByLabel('Microphone', { exact: true })).not.toBeEmpty();

  await page.getByRole('button', { name: 'Play a test sound' }).click();
  await expect(page.getByText('You now hear two tones.')).toBeVisible();

  const camera = page.getByRole('button', { name: /^Camera/ });
  await expect(camera).toHaveAttribute('aria-pressed', 'true');
  await expect(camera).toContainText('on');
  await camera.click();
  await expect(camera).toHaveAttribute('aria-pressed', 'false');
  await expect(camera).toContainText('off');
  await expect(page.getByText('Your camera is off')).toBeVisible();
  const microphone = page.getByRole('button', { name: /^Microphone/ });
  await microphone.click();
  await expect(microphone).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByText('Your microphone is off, so we do not hear you.')).toBeVisible();

  for (const control of await page.getByRole('button').all()) {
    const box = await control.boundingBox();
    if (box) {
      expect(box.width).toBeGreaterThanOrEqual(24);
      expect(box.height).toBeGreaterThanOrEqual(24);
    }
  }
  await expectNoAxeViolations(page);
});

test('the keyboard alone leads from the start to the state that says the call is not available yet', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'the fake devices are Chromium flags');
  await openCallPage(page);
  await tabTo(page, page.getByRole('button', { name: 'Test camera and microphone' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1, name: 'Check picture and sound' })).toBeFocused();
  await tabTo(page, page.getByRole('button', { name: 'Play a test sound' }));
  await page.keyboard.press('Space');
  await expect(page.getByText('You now hear two tones.')).toBeVisible();
  await tabTo(page, page.getByRole('button', { name: 'Join', exact: true }));
  await page.keyboard.press('Enter');
  const unavailable = page.getByRole('heading', { level: 1, name: 'The call is not available in this page yet' });
  await expect(unavailable).toBeFocused();
  expect(await focusedText(page)).toContain('not available');
  await expectNoAxeViolations(page);
});

for (const [what, heading, join] of [
  ['camera', 'Your browser does not allow the camera', 'Join without camera'],
  ['microphone', 'Your browser does not allow the microphone', 'Join without microphone'],
  ['both', 'Your browser does not allow the camera and the microphone', 'Join without camera and microphone'],
] as const) {
  test(`a refused ${what} is explained with what to click and a way on`, async ({ page }) => {
    await refuse(page, what);
    await openCallPage(page);
    await page.getByRole('button', { name: 'Test camera and microphone' }).click();
    const panel = page.getByRole('group', { name: heading });
    await expect(panel).toBeVisible();
    await expect(page.getByRole('heading', { level: 2, name: heading })).toBeFocused();
    await expect(panel.getByRole('listitem')).toHaveCount(3);
    await expect(panel.getByRole('button', { name: 'Try again' })).toBeVisible();
    await expect(page.getByRole('button', { name: join })).toBeVisible();
    await expectNoAxeViolations(page);
  });
}

test('at 320 px nothing scrolls sideways in any state', async ({ page }) => {
  await refuse(page, 'both');
  await page.setViewportSize({ width: 320, height: 800 });
  await openCallPage(page);
  const overflow = (): Promise<number> =>
    page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(await overflow()).toBeLessThanOrEqual(0);
  await page.getByRole('button', { name: 'Test camera and microphone' }).click();
  await expect(page.getByRole('group', { name: /does not allow/ })).toBeVisible();
  expect(await overflow()).toBeLessThanOrEqual(0);
  await expectNoAxeViolations(page);
});

test('forced colours and reduced motion: no violations and the meter still speaks', async ({ browser, browserName }) => {
  test.skip(browserName !== 'chromium', 'the fake devices are Chromium flags');
  const context = await browser.newContext({
    locale: 'en-US',
    forcedColors: 'active',
    reducedMotion: 'reduce',
    permissions: ['camera', 'microphone'],
  });
  const page = await context.newPage();
  await openCallPage(page);
  await page.getByRole('button', { name: 'Test camera and microphone' }).click();
  await expect(page.getByText('We hear you.')).toBeVisible({ timeout: 15_000 });
  await expectNoAxeViolations(page);
  await context.close();
});

test('scripts off: the start state says what the call needs and nothing is empty', async ({ page }) => {
  await page.route(/\/static\/.*\.js(\?.*)?$/, (route) => route.abort());
  await openCallPage(page);
  await expect(page.getByText('a browser with JavaScript, a camera and a microphone')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Test camera and microphone' })).toBeHidden();
  await expect(page.locator('main')).not.toBeEmpty();
  await expectNoAxeViolations(page);
});
