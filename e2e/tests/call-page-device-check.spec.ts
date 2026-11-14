/**
 * The call page before the click that joins (ADR 0123 step 4b), walked in a browser: the start
 * state, the device check, device choice, every refused permission, no device found, the keyboard
 * alone, what is announced and where focus goes, and that leaving the page or switching a device
 * off gives the tracks back.
 *
 * The page is the one of `fixtures.json` (`call_page`: the emailed link of a confirmed booking,
 * which sets the participant's cookie and redirects, like the suspended booking's link) on a
 * stack that runs with EMBEDDED_CALL_ENABLED (.github/actions/e2e-stack). Nothing here clicks the
 * join button: joining, with the test double of the video SDK, is call-page-joining.spec.ts.
 *
 * Two kinds of device. "real": Chromium's own fake camera and microphone (the project's launch
 * flags in playwright.config.ts), for the tests that need the real path, such as the meter.
 * "made-up": a stand-in for getUserMedia and enumerateDevices that hands out a canvas picture and
 * an oscillator sound with two cameras and two microphones, in every browser; it also plays a
 * refusal of one kind or of both while the other kind stays healthy. The island gates (axe, 320
 * px, forced colours, scripts off, CSP) run on the start state through pages.json; axe also runs
 * here in the check and the refused states.
 */
import { expect, test } from '@playwright/test';

import {
  begin,
  checkHeading,
  level,
  live,
  openCallPage,
  READY,
  requests,
  said,
  spy,
  statuses,
  type Spied,
} from '../call-page';
import { expectNoAxeViolations, tabTo } from '../helpers';

test.use({ locale: 'en-US' });

test('the start state shows the appointment and one button, and asks for no device on load', async ({ page }) => {
  await spy(page, { devices: 'made-up' });
  await openCallPage(page);
  await expect(begin(page)).toBeVisible();
  await expect(page.getByText('a browser with JavaScript, a camera and a microphone')).toBeHidden();
  await expect(page.locator('dl.details')).toContainText('Team');
  // The button is shown by the page's own script when it has started: nothing was asked by then.
  expect(await requests(page)).toBe(0);
  await expectNoAxeViolations(page);
});

test('the device check with the real fake devices: picture, meter, test sound', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'the real fake devices are Chromium flags');
  await spy(page);
  await openCallPage(page);
  await begin(page).click();

  await expect(checkHeading(page)).toBeFocused();
  await expect(page.getByLabel('Preview of your camera')).toBeVisible();
  await said(page, 'Checking your camera and microphone.');
  await said(page, READY);
  expect(await requests(page)).toBeGreaterThan(0);
  expect(await live(page)).toEqual([1, 1]);

  // The fake microphone beeps: the bar rises above zero, the words change, "we hear you" is said once.
  await expect.poll(() => level(page), { timeout: 20_000 }).toBeGreaterThan(0);
  await expect(page.getByText('We hear you.').first()).toBeVisible({ timeout: 20_000 });
  await said(page, 'We hear you.');
  await page.waitForTimeout(6000);
  expect((await statuses(page)).filter((s) => s === 'We hear you.')).toHaveLength(1);

  await page.getByRole('button', { name: 'Play a test sound' }).click();
  await expect(page.getByText('You now hear two tones.')).toBeVisible();
  await said(page, 'Playing the test sound.');

  // The microphone off: the bar is empty and stays empty.
  await page.getByRole('button', { name: /^Microphone/ }).click();
  // The page also holds this sentence in its hidden texts, so the meter's own line is asked.
  await expect(page.locator('[data-call-level-text]')).toHaveText('Your microphone is off, so we do not hear you.');
  await expect.poll(() => level(page)).toBe(0);
  await expectNoAxeViolations(page);
});

test('device choice and the toggles, with made-up devices', async ({ page }) => {
  await spy(page, { devices: 'made-up' });
  await openCallPage(page);
  await begin(page).click();
  await said(page, READY);
  expect(await live(page)).toEqual([1, 1]);

  const cameras = page.getByLabel('Camera', { exact: true });
  const microphones = page.getByLabel('Microphone', { exact: true });
  expect(await cameras.locator('option').count()).toBeGreaterThan(1);
  expect(await microphones.locator('option').count()).toBeGreaterThan(1);
  await microphones.selectOption('mic-2');
  await said(page, 'Microphone on.');
  expect(await live(page)).toEqual([1, 1]); // the old microphone was given back, the camera kept

  // Off means given back; the word and the pressed state agree.
  const camera = page.getByRole('button', { name: /^Camera/ });
  await expect(camera).toHaveAttribute('aria-pressed', 'true');
  await expect(camera).toContainText('on');
  await camera.click();
  await expect(camera).toHaveAttribute('aria-pressed', 'false');
  await expect(camera).toContainText('off');
  await expect(page.locator('[data-call-state="check"]').getByText('Your camera is off')).toBeVisible();
  await said(page, 'Camera off.');
  expect((await live(page))[0]).toBe(0);
  const microphone = page.getByRole('button', { name: /^Microphone/ });
  await microphone.click();
  await expect(microphone).toHaveAttribute('aria-pressed', 'false');
  expect(await live(page)).toEqual([0, 0]);

  // On again asks for that one device; a second request while it waits is ignored.
  await page.evaluate(() => ((window as unknown as { spied: Spied }).spied.delay = 600));
  const before = await requests(page);
  await camera.dblclick();
  await expect(camera).toHaveAttribute('aria-pressed', 'true');
  expect(await requests(page)).toBe(before + 1);
  expect((await live(page))[0]).toBe(1);

  for (const control of await page.getByRole('button').all()) {
    if (!(await control.isVisible())) continue;
    const box = await control.boundingBox();
    expect(box).not.toBeNull();
    expect(box?.width).toBeGreaterThanOrEqual(24);
    expect(box?.height).toBeGreaterThanOrEqual(24);
  }
  await expectNoAxeViolations(page);
});

test('leaving the page gives the devices back, and a stream that arrives late is stopped', async ({ page }) => {
  await spy(page, { devices: 'made-up' });
  await openCallPage(page);
  await begin(page).click();
  await said(page, READY);
  expect(await live(page)).toEqual([1, 1]);
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  expect(await live(page)).toEqual([0, 0]);

  const slow = await page.context().newPage();
  await spy(slow, { devices: 'made-up', delay: 1000 });
  await openCallPage(slow);
  await begin(slow).click();
  await slow.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  // Wait for the state, not the clock: the slow stream has arrived, and was stopped at once.
  await expect
    .poll(() => slow.evaluate(() => (window as unknown as { spied: Spied }).spied.streams.length))
    .toBe(1);
  expect(await live(slow)).toEqual([0, 0]);
});

test('the keyboard alone leads from the start to the join button', async ({ page }) => {
  await spy(page, { devices: 'made-up' });
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
  // The click itself, and what follows it, is walked in call-page-joining.spec.ts.
  await tabTo(page, page.getByRole('button', { name: 'Join', exact: true }));
  await expectNoAxeViolations(page);
});

for (const [what, heading, join, without] of [
  ['camera', 'Your browser does not allow the camera', 'Join without camera', 'You can also join without a camera.'],
  [
    'microphone',
    'Your browser does not allow the microphone',
    'Join without microphone',
    'You can also join without a microphone. You then hear the other side, but they do not hear you.',
  ],
  [
    'both',
    'Your browser does not allow the camera and the microphone',
    'Join without camera and microphone',
    'You can also join without a camera and microphone. You then see and hear the other side, but they do not see or hear you.',
  ],
] as const) {
  test(`a refused ${what} is explained, announced once and has a way on`, async ({ page }) => {
    await spy(page, { devices: 'made-up', refuse: what });
    await openCallPage(page);
    await begin(page).click();
    const panel = page.getByRole('group', { name: heading });
    await expect(panel).toBeVisible();
    // Focus carries the heading to a screen reader; the live region adds, and never repeats it.
    await expect(page.getByRole('heading', { level: 2, name: heading })).toBeFocused();
    await said(page, without);
    expect(await statuses(page)).not.toContain(heading);
    await expect(panel.getByRole('listitem')).toHaveCount(3);
    await expect(panel.getByRole('button', { name: 'Try again' })).toBeVisible();
    await expect(page.getByRole('button', { name: join })).toBeVisible();
    // The select of a device that is refused lists nothing but placeholders, so it is not shown.
    const cameraRefused = what !== 'microphone';
    const microphoneRefused = what !== 'camera';
    expect(await page.getByLabel('Camera', { exact: true }).isVisible()).toBe(!cameraRefused);
    expect(await page.getByLabel('Microphone', { exact: true }).isVisible()).toBe(!microphoneRefused);
    await expectNoAxeViolations(page);
  });
}

test('no camera and no microphone found says so and offers another look', async ({ page }) => {
  await spy(page, { devices: 'none' });
  await openCallPage(page);
  await begin(page).click();
  await expect(page.getByText('No camera found. You can join without a picture.')).toBeVisible();
  await expect(page.getByText('No microphone found. You can join without sound.')).toBeVisible();
  await said(page, 'No camera or microphone found. Connect one and try again.');
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Join without camera and microphone' })).toBeVisible();
  await expect(page.getByLabel('Camera', { exact: true })).toBeHidden();
  await expect(page.getByLabel('Microphone', { exact: true })).toBeHidden();
  await expectNoAxeViolations(page);
});

test('at 320 px nothing scrolls sideways in the check or the refused state', async ({ page }) => {
  await spy(page, { devices: 'made-up', refuse: 'both' });
  await page.setViewportSize({ width: 320, height: 800 });
  await openCallPage(page);
  const overflow = (): Promise<number> => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(await overflow()).toBeLessThanOrEqual(0);
  await begin(page).click();
  await expect(page.getByRole('group', { name: /does not allow/ })).toBeVisible();
  expect(await overflow()).toBeLessThanOrEqual(0);
  await expectNoAxeViolations(page);
});

test('forced colours and reduced motion: the focus ring and the meter bar stay visible', async ({ browser, browserName }) => {
  test.skip(browserName !== 'chromium', 'the real fake devices are Chromium flags');
  const context = await browser.newContext({ locale: 'en-US', forcedColors: 'active', reducedMotion: 'reduce' });
  const page = await context.newPage();
  await spy(page);
  await openCallPage(page);
  // By keyboard, as the focus ring only shows for keyboard focus (`:focus-visible`).
  await begin(page).focus();
  await page.keyboard.press('Enter');
  await expect.poll(() => level(page), { timeout: 20_000 }).toBeGreaterThan(0);

  const toggle = page.getByRole('button', { name: /^Microphone/ });
  await toggle.focus();
  const ring = await toggle.evaluate((el) => {
    const style = getComputedStyle(el);
    return { style: style.outlineStyle, width: style.outlineWidth, color: style.outlineColor };
  });
  expect(ring.style).toBe('solid');
  expect(ring.width).toBe('3px');
  expect(ring.color).not.toBe('rgba(0, 0, 0, 0)');

  const bar = await page.locator('[data-call-level]').evaluate((el) => {
    const style = getComputedStyle(el);
    return { border: style.borderTopStyle, width: style.borderTopWidth, box: el.getBoundingClientRect().height };
  });
  expect(bar.border).toBe('solid');
  expect(bar.width).toBe('1px');
  expect(bar.box).toBeGreaterThan(8);
  await expectNoAxeViolations(page);
  await context.close();
});
