/**
 * The call page before the click that joins (ADR 0123 step 4b), walked in a browser with the
 * browser's own fake camera and microphone: the start state, the device check, device choice,
 * every refused permission, no device found, the keyboard alone, what is announced and where
 * focus goes, and that leaving the page or switching a device off gives the tracks back.
 *
 * The page is the one of `fixtures.json` (`call_page`: the emailed link of a confirmed booking,
 * which sets the participant's cookie and redirects, like the suspended booking's link) on a
 * stack that runs with EMBEDDED_CALL_ENABLED (.github/actions/e2e-stack). Nothing here reaches
 * the video platform: the page has no SDK file yet, and the join button leads to a state that
 * says the call is not available in this page yet. The fake devices are Chromium's (the project's
 * launch flags in playwright.config.ts); stand-ins for getUserMedia play the refusals in every
 * browser. The island gates (axe, 320 px, forced colours, scripts off, CSP) run on the start
 * state through pages.json; axe also runs here in the check and the refused states.
 */
import { expect, test, type Page } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';
import { fixture } from '../pages';

test.use({ locale: 'en-US' });

type Spied = { deviceRequests: number; streams: MediaStream[]; statuses: string[] };

/** Open the call page in English through its emailed link (the cookie is set on the way). */
async function openCallPage(page: Page): Promise<void> {
  await page.goto(`${fixture('call_page')}?lang=en`);
  await expect(page.getByRole('heading', { level: 1, name: 'Your video call' })).toBeVisible();
}

/**
 * Before the page's own script runs: record every request for a device and every stream it
 * returns, and every sentence the live region gets. `plan` decides what a request does:
 * "real" asks the browser's fake devices, a name rejects like that error, "slow" waits a second.
 */
async function spy(page: Page, plan: 'real' | 'camera' | 'microphone' | 'both' | 'none' | 'slow' = 'real'): Promise<void> {
  await page.addInitScript((what) => {
    const w = window as unknown as { spied: Spied };
    w.spied = { deviceRequests: 0, streams: [], statuses: [] };
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      w.spied.deviceRequests += 1;
      const wantsVideo = Boolean(constraints?.video);
      const wantsAudio = Boolean(constraints?.audio);
      if (what === 'none') throw new DOMException('none', 'NotFoundError');
      const refused =
        (wantsVideo && (what === 'camera' || what === 'both')) || (wantsAudio && (what === 'microphone' || what === 'both'));
      if (refused) throw new DOMException('blocked', 'NotAllowedError');
      if (what === 'slow') await new Promise((resolve) => setTimeout(resolve, 1000));
      const stream = await original(constraints);
      w.spied.streams.push(stream);
      return stream;
    };
    document.addEventListener('DOMContentLoaded', () => {
      const status = document.querySelector('[data-call-status]');
      if (status) {
        new MutationObserver(() => {
          if (status.textContent) w.spied.statuses.push(status.textContent);
        }).observe(status, { childList: true, characterData: true, subtree: true });
      }
    });
  }, plan);
}

const requests = (page: Page): Promise<number> => page.evaluate(() => (window as unknown as { spied: Spied }).spied.deviceRequests);
const statuses = (page: Page): Promise<string[]> => page.evaluate(() => (window as unknown as { spied: Spied }).spied.statuses);
/** How many of the tracks the page was given are still live: [video, audio]. */
const live = (page: Page): Promise<[number, number]> =>
  page.evaluate(() => {
    const tracks = (window as unknown as { spied: Spied }).spied.streams.flatMap((s) => s.getTracks());
    const open = (kind: string): number => tracks.filter((t) => t.kind === kind && t.readyState === 'live').length;
    return [open('video'), open('audio')];
  });

/** The live region has said `text` (it keeps no history of its own, so the spy does). */
const said = (page: Page, text: string): Promise<void> => expect.poll(() => statuses(page)).toContain(text);

const begin = (page: Page) => page.getByRole('button', { name: 'Test camera and microphone' });
const checkHeading = (page: Page) => page.getByRole('heading', { level: 1, name: 'Check picture and sound' });

test('the start state shows the appointment and one button, and asks for no device on load', async ({ page }) => {
  await spy(page);
  await openCallPage(page);
  await expect(begin(page)).toBeVisible();
  await expect(page.getByText('a browser with JavaScript, a camera and a microphone')).toBeHidden();
  await expect(page.locator('dl.details')).toContainText('Team');
  await page.waitForTimeout(500);
  expect(await requests(page)).toBe(0);
  await expectNoAxeViolations(page);
});

test('the device check: picture, meter, test sound, device choice and toggles', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'the fake devices are Chromium flags');
  await spy(page);
  await openCallPage(page);
  await begin(page).click();

  await expect(checkHeading(page)).toBeFocused();
  await expect(page.getByLabel('Your own picture')).toBeVisible();
  await said(page, 'Your camera and microphone are ready.');
  expect(await requests(page)).toBeGreaterThan(0);
  expect(await live(page)).toEqual([1, 1]);

  // The fake microphone beeps: the meter's words change, and "we hear you" is said once.
  await expect(page.getByText('We hear you.').first()).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(6000);
  expect((await statuses(page)).filter((s) => s === 'We hear you.')).toHaveLength(1);

  await page.getByRole('button', { name: 'Play a test sound' }).click();
  await expect(page.getByText('You now hear two tones.')).toBeVisible();
  await said(page, 'Playing the test sound.');

  // Device choice: another microphone, when the browser has one, replaces only the microphone.
  const microphones = page.getByLabel('Microphone', { exact: true });
  const options = await microphones.locator('option').all();
  expect(options.length).toBeGreaterThan(0);
  if (options.length > 1) {
    await microphones.selectOption({ index: options.length - 1 });
    await said(page, 'Microphone on.');
    expect(await live(page)).toEqual([1, 1]);
  }
  await expect(page.getByLabel('Camera', { exact: true }).locator('option')).not.toHaveCount(0);

  // Off means given back: the track stops, the word and the pressed state agree.
  const camera = page.getByRole('button', { name: /^Camera/ });
  await expect(camera).toHaveAttribute('aria-pressed', 'true');
  await expect(camera).toContainText('on');
  await camera.click();
  await expect(camera).toHaveAttribute('aria-pressed', 'false');
  await expect(camera).toContainText('off');
  await expect(page.getByText('Your camera is off')).toBeVisible();
  await said(page, 'Camera off.');
  expect((await live(page))[0]).toBe(0);
  const microphone = page.getByRole('button', { name: /^Microphone/ });
  await microphone.click();
  await expect(microphone).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByText('Your microphone is off, so we do not hear you.')).toBeVisible();
  expect(await live(page)).toEqual([0, 0]);
  // On again asks for that one device only.
  await camera.click();
  await expect(camera).toHaveAttribute('aria-pressed', 'true');
  expect((await live(page))[0]).toBe(1);

  for (const control of await page.getByRole('button').all()) {
    const box = await control.boundingBox();
    if (box) {
      expect(box.width).toBeGreaterThanOrEqual(24);
      expect(box.height).toBeGreaterThanOrEqual(24);
    }
  }
  await expectNoAxeViolations(page);
});

test('leaving the page gives the devices back, and a stream that arrives late is stopped', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'the fake devices are Chromium flags');
  await spy(page);
  await openCallPage(page);
  await begin(page).click();
  await said(page, 'Your camera and microphone are ready.');
  expect(await live(page)).toEqual([1, 1]);
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  expect(await live(page)).toEqual([0, 0]);

  const slow = await page.context().newPage();
  await spy(slow, 'slow');
  await openCallPage(slow);
  await begin(slow).click();
  await slow.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  await slow.waitForTimeout(2000);
  expect(await live(slow)).toEqual([0, 0]);
});

test('the keyboard alone leads from the start to the state that says the call is not available yet', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'the fake devices are Chromium flags');
  await spy(page);
  await openCallPage(page);
  await tabTo(page, begin(page));
  await page.keyboard.press('Enter');
  await expect(checkHeading(page)).toBeFocused();
  await tabTo(page, page.getByRole('button', { name: 'Play a test sound' }));
  await page.keyboard.press('Space');
  await expect(page.getByText('You now hear two tones.')).toBeVisible();
  await tabTo(page, page.getByRole('button', { name: /^Microphone/ }));
  await page.keyboard.press('Space');
  await expect(page.getByRole('button', { name: /^Microphone/ })).toHaveAttribute('aria-pressed', 'false');
  await tabTo(page, page.getByRole('button', { name: 'Join', exact: true }));
  await page.keyboard.press('Enter');
  const unavailable = page.getByRole('heading', { level: 1, name: 'The call is not available in this page yet' });
  await expect(unavailable).toBeFocused();
  expect(await live(page)).toEqual([0, 0]);
  await expectNoAxeViolations(page);
});

for (const [what, heading, join] of [
  ['camera', 'Your browser does not allow the camera', 'Join without camera'],
  ['microphone', 'Your browser does not allow the microphone', 'Join without microphone'],
  ['both', 'Your browser does not allow the camera and the microphone', 'Join without camera and microphone'],
] as const) {
  test(`a refused ${what} is explained, announced and has a way on`, async ({ page }) => {
    await spy(page, what);
    await openCallPage(page);
    await begin(page).click();
    const panel = page.getByRole('group', { name: heading });
    await expect(panel).toBeVisible();
    await expect(page.getByRole('heading', { level: 2, name: heading })).toBeFocused();
    await said(page, heading);
    await expect(panel.getByRole('listitem')).toHaveCount(3);
    await expect(panel.getByRole('button', { name: 'Try again' })).toBeVisible();
    await expect(page.getByRole('button', { name: join })).toBeVisible();
    await expectNoAxeViolations(page);
  });
}

test('no camera and no microphone found says so and offers another look', async ({ page }) => {
  await spy(page, 'none');
  await openCallPage(page);
  await begin(page).click();
  await expect(page.getByText('No camera found. You can join without a picture.')).toBeVisible();
  await expect(page.getByText('No microphone found. You can join without sound.')).toBeVisible();
  await said(page, 'No camera or microphone found. Connect one and try again.');
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Join without camera and microphone' })).toBeVisible();
  await expectNoAxeViolations(page);
});

test('at 320 px nothing scrolls sideways in the check or the refused state', async ({ page }) => {
  await spy(page, 'both');
  await page.setViewportSize({ width: 320, height: 800 });
  await openCallPage(page);
  const overflow = (): Promise<number> => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(await overflow()).toBeLessThanOrEqual(0);
  await begin(page).click();
  await expect(page.getByRole('group', { name: /does not allow/ })).toBeVisible();
  expect(await overflow()).toBeLessThanOrEqual(0);
  await expectNoAxeViolations(page);
});

test('forced colours and reduced motion: no violations and the meter still speaks', async ({ browser, browserName }) => {
  test.skip(browserName !== 'chromium', 'the fake devices are Chromium flags');
  const context = await browser.newContext({ locale: 'en-US', forcedColors: 'active', reducedMotion: 'reduce' });
  const page = await context.newPage();
  await spy(page);
  await openCallPage(page);
  await begin(page).click();
  await expect(page.getByText('We hear you.').first()).toBeVisible({ timeout: 20_000 });
  await expectNoAxeViolations(page);
  await context.close();
});
