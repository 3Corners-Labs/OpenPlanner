/**
 * The call page in the call (ADR 0123 step 5a), walked in a browser against the test double of the
 * video SDK: the panel of devices (keyboard alone, another camera, microphone or speaker, a choice
 * that fails), the notices (the recording, the connection being restored or lost), the bar that
 * stays in view, the ended state and the failure inside the call; what is announced and how often,
 * where focus goes, and the abuse cases of `docs/threat-model.md` (a device left open after a
 * change, a text from outside shown as markup, a state reached after the call was left).
 *
 * The double and the made-up devices are those of `../call-sdk` and `../call-page`. The speaker is
 * stubbed here (`setSinkId` is not in every engine): the page's contract is what it does with the
 * answer, not what an engine does with a made-up output. Nothing here waits for a fixed time to
 * prove that something did not happen except one frame: every other wait is for a state.
 */
import { expect, test, type Locator, type Page } from '@playwright/test';

import { live, said } from '../call-page';
import {
  control,
  FAILED,
  granted,
  heading,
  IN_CALL,
  join,
  open,
  reconnection,
  recording,
  saidLike,
  sdk,
  state,
  stubToken,
  timesSaid,
  WAITING,
  type Plan,
} from '../call-sdk';
import { expectNoAxeViolations, tabTo } from '../helpers';
import { fixture } from '../pages';

test.use({ locale: 'en-US' });

const RECORDING = 'This call is being recorded.';
const RESTORING = 'The connection was interrupted. We are restoring it.';
const CHANGED = 'Device changed.';
const NOT_CHANGED = 'That device did not work. Your previous device stays in use.';
const LOST = 'The connection was lost and could not be restored. You can try again.';
const NO_MEDIA = 'The picture or sound of the call could not be set up. You can try again.';

const devicesButton = (page: Page) => page.getByRole('button', { name: 'Devices' });
const panel = (page: Page) => page.getByRole('group', { name: 'Your devices' });
const pick = (page: Page, kind: 'camera' | 'microphone' | 'speaker') => page.locator(`[data-call-pick="${kind}"]`);
const notice = (page: Page, kind: 'recording' | 'restoring') => page.locator(`[data-call-notice="${kind}"]`);
const bar = (page: Page) => state(page, 'call').locator('.call-bar');
const leave = (page: Page) => page.getByRole('button', { name: 'Leave the call' });

/** Into the call, with the plan of the double. */
async function inCall(page: Page, plan: Plan = {}): Promise<void> {
  await stubToken(page, granted());
  await open(page, plan);
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();
}

/**
 * The speaker of the other side's sound, as an engine without a choice would not give it: every
 * `setSinkId` on the other side's sound is recorded in `__sinks` unless `__sinkFails` says no.
 */
async function stubSpeaker(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __sinks: string[]; __sinkFails: boolean };
    w.__sinks = [];
    w.__sinkFails = false;
    Object.defineProperty(HTMLMediaElement.prototype, 'setSinkId', {
      configurable: true,
      value(this: HTMLMediaElement, id: string): Promise<void> {
        if (w.__sinkFails) return Promise.reject(new DOMException('refused', 'NotAllowedError'));
        if (this.hasAttribute('data-call-remote-audio')) w.__sinks.push(id);
        return Promise.resolve();
      },
    });
  });
}
const sinks = (page: Page): Promise<string[]> => page.evaluate(() => (window as unknown as { __sinks: string[] }).__sinks);
const sinksFail = (page: Page, refuse: boolean): Promise<void> =>
  page.evaluate((value) => ((window as unknown as { __sinkFails: boolean }).__sinkFails = value), refuse);

/** The box of `locator` lies wholly inside the window, with no scrolling. */
async function inView(page: Page, locator: Locator, what: string): Promise<void> {
  const box = await locator.boundingBox();
  const size = page.viewportSize();
  expect(box, what).not.toBeNull();
  expect(box?.y ?? -1, `${what}: top`).toBeGreaterThanOrEqual(0);
  expect((box?.y ?? 0) + (box?.height ?? 0), `${what}: bottom`).toBeLessThanOrEqual(size?.height ?? 0);
}
const frames = (page: Page): Promise<unknown> => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

// ---- the panel of devices -------------------------------------------------------------------------

test('the panel of devices opens with the keyboard, lists camera, microphone and speaker, is no trap, and Escape closes it and returns focus', async ({ page }) => {
  await stubSpeaker(page);
  await inCall(page);
  const button = devicesButton(page);
  await expect(button).toHaveAttribute('aria-expanded', 'false');
  await expect(panel(page)).toBeHidden();

  await tabTo(page, button);
  await page.keyboard.press('Enter');
  await expect(button).toHaveAttribute('aria-expanded', 'true');
  await expect(panel(page)).toBeVisible();
  await expect(button).toBeFocused(); // opening a panel does not take focus
  await expect(pick(page, 'camera').locator('option')).toHaveText(['Camera one', 'Camera two']);
  await expect(pick(page, 'microphone').locator('option')).toHaveText(['Microphone one', 'Microphone two']);
  await expect(pick(page, 'speaker').locator('option')).toHaveText(['Speaker one', 'Speaker two']);

  for (const kind of ['camera', 'microphone', 'speaker'] as const) {
    await page.keyboard.press('Tab');
    await expect(pick(page, kind)).toBeFocused();
  }
  await page.keyboard.press('Tab'); // not a trap: on to the way out
  await expect(leave(page)).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(pick(page, 'speaker')).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(panel(page)).toBeHidden();
  await expect(button).toHaveAttribute('aria-expanded', 'false');
  await expect(button).toBeFocused();
  // The button opens it again and closes it again.
  await button.click();
  await expect(panel(page)).toBeVisible();
  await button.click();
  await expect(panel(page)).toBeHidden();
  await expectNoAxeViolations(page);
});

test('choosing another camera or microphone sends the new one, gives the old one back and says so once', async ({ page }) => {
  await inCall(page);
  await devicesButton(page).click();
  expect(await live(page)).toEqual([1, 1]);

  await pick(page, 'camera').focus();
  await pick(page, 'camera').selectOption('cam-2');
  await said(page, CHANGED);
  let log = await sdk(page);
  expect(log.devices.at(-1)).toEqual({ kind: 'camera', deviceId: 'cam-2' });
  expect(log.published).toEqual(['camera']);
  // The old camera is given back: one camera is open, not two.
  await expect.poll(() => live(page)).toEqual([1, 1]);
  await expect(pick(page, 'camera')).toBeFocused(); // a change inside a state does not steal focus
  await expect(page.locator('[data-call-self="call"]')).toBeVisible();

  await pick(page, 'microphone').selectOption('mic-2');
  await expect.poll(async () => (await sdk(page)).published).toEqual(['camera', 'microphone']);
  log = await sdk(page);
  expect(log.devices.at(-1)).toEqual({ kind: 'microphone', deviceId: 'mic-2' });
  await expect.poll(() => live(page)).toEqual([1, 1]);
  await expect.poll(() => timesSaid(page, CHANGED)).toBe(2);

  // The choice is the one a later switch on uses.
  const camera = page.getByRole('button', { name: /^Camera/ });
  await camera.click();
  await expect(camera).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(() => live(page)).toEqual([0, 1]);
  await camera.click();
  await expect(camera).toHaveAttribute('aria-pressed', 'true');
  expect((await sdk(page)).devices.at(-1)).toEqual({ kind: 'camera', deviceId: 'cam-2' });
  await expect(pick(page, 'camera')).toHaveValue('cam-2');
});

test('a camera that is off only has its choice noted: nothing is taken until it is switched on', async ({ page }) => {
  await inCall(page);
  await page.getByRole('button', { name: /^Camera/ }).click();
  await expect.poll(() => live(page)).toEqual([0, 1]);
  const before = (await sdk(page)).devices.length;
  await devicesButton(page).click();
  await pick(page, 'camera').selectOption('cam-2');
  await said(page, CHANGED);
  expect((await sdk(page)).devices).toHaveLength(before);
  expect(await live(page)).toEqual([0, 1]);
  await page.getByRole('button', { name: /^Camera/ }).click();
  await expect.poll(async () => (await sdk(page)).devices.at(-1)).toEqual({ kind: 'camera', deviceId: 'cam-2' });
});

test('a device that cannot be taken keeps the old one, says so, and the list shows the old one again', async ({ page }) => {
  await inCall(page, { deviceFails: ['cam-2'] });
  await devicesButton(page).click();
  await pick(page, 'camera').selectOption('cam-2');
  await said(page, NOT_CHANGED);
  await expect(pick(page, 'camera')).toHaveValue('cam-1');
  expect((await sdk(page)).published).toEqual([]);
  expect(await live(page)).toEqual([1, 1]);
  expect(await timesSaid(page, CHANGED)).toBe(0);
  await expectNoAxeViolations(page);
});

test('a device that cannot be sent is stopped again: nothing stays open after a refused change', async ({ page }) => {
  await inCall(page, { publishFails: true });
  await devicesButton(page).click();
  await pick(page, 'microphone').selectOption('mic-2');
  await said(page, NOT_CHANGED);
  await expect(pick(page, 'microphone')).toHaveValue('mic-1');
  // The new microphone was taken to be sent, and given back when it could not be: still one.
  await expect.poll(() => live(page)).toEqual([1, 1]);
  expect((await sdk(page)).devices.at(-1)).toEqual({ kind: 'microphone', deviceId: 'mic-2' });
  // The old one is still the one in use: a later switch on takes the old choice again.
  const microphone = page.getByRole('button', { name: /^Microphone/ });
  await microphone.click();
  await expect.poll(() => live(page)).toEqual([1, 0]);
  await microphone.click();
  await expect.poll(async () => (await sdk(page)).devices.at(-1)).toEqual({ kind: 'microphone', deviceId: 'mic-1' });
});

test('the speaker is applied to the other side at once, and a refusal keeps the old one', async ({ page }) => {
  await stubSpeaker(page);
  await inCall(page);
  await devicesButton(page).click();
  await pick(page, 'speaker').selectOption('spk-2');
  await said(page, CHANGED);
  expect(await sinks(page)).toContain('spk-2');

  await sinksFail(page, true);
  await pick(page, 'speaker').selectOption('spk-1');
  await said(page, NOT_CHANGED);
  await expect(pick(page, 'speaker')).toHaveValue('spk-2');
  expect(await sinks(page)).not.toContain('spk-1');
});

// ---- the notices -----------------------------------------------------------------------------------

test('the recording notice shows while the recording runs and is said once when it starts and once when it stops', async ({ page }) => {
  await inCall(page);
  await expect(notice(page, 'recording')).toBeHidden();
  await recording(page, true);
  await recording(page, true); // the same news again says nothing more
  await expect(notice(page, 'recording')).toBeVisible();
  // A word and an icon, not colour alone; and it says what it means in a plain sentence.
  await expect(notice(page, 'recording')).toContainText('Recording');
  await expect(notice(page, 'recording')).toContainText('This call is being recorded. The picture and sound are saved.');
  await expect(notice(page, 'recording').locator('svg')).toHaveCount(1);
  await said(page, RECORDING);
  await expect(heading(page, IN_CALL)).toBeFocused(); // a notice takes no focus
  await expectNoAxeViolations(page);

  await recording(page, false);
  await expect(notice(page, 'recording')).toBeHidden();
  await said(page, 'The recording has stopped.');
  await recording(page, false);
  await frames(page);
  expect(await timesSaid(page, RECORDING)).toBe(1);
  expect(await timesSaid(page, 'The recording has stopped.')).toBe(1);
});

test('a connection being restored shows a notice, is said once, and is said once more when it is back', async ({ page }) => {
  await inCall(page);
  await reconnection(page, 'start');
  await reconnection(page, 'start');
  await expect(notice(page, 'restoring')).toBeVisible();
  await expect(notice(page, 'restoring')).toContainText('Reconnecting');
  await said(page, RESTORING);
  await expectNoAxeViolations(page);

  await reconnection(page, 'success');
  await expect(notice(page, 'restoring')).toBeHidden();
  await said(page, 'The connection is back.');
  await reconnection(page, 'success');
  await frames(page);
  expect(await timesSaid(page, RESTORING)).toBe(1);
  expect(await timesSaid(page, 'The connection is back.')).toBe(1);
  await expect(state(page, 'call')).toBeVisible();
});

test('a connection lost for good ends in the failure state with its own sentence, gives everything back, and trying again works', async ({ page }) => {
  await inCall(page);
  await devicesButton(page).click();
  await reconnection(page, 'start');
  await reconnection(page, 'fail');

  await expect(heading(page, FAILED)).toBeFocused();
  await expect(page.getByText(LOST)).toBeVisible();
  await saidLike(page, /^The connection was lost and could not be restored\./);
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
  expect((await sdk(page)).left).toBe(true);
  expect(await live(page)).toEqual([0, 0]);
  await expectNoAxeViolations(page);

  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(heading(page, IN_CALL)).toBeFocused();
  // Nothing of the earlier call is left: no notice, the panel closed.
  await expect(notice(page, 'restoring')).toBeHidden();
  await expect(devicesButton(page)).toHaveAttribute('aria-expanded', 'false');
  await expect(panel(page)).toBeHidden();
});

test('the picture or sound that cannot be set up when the staff member lets the participant in ends in the failure state, and trying again works', async ({ page }) => {
  await stubToken(page, granted());
  await open(page, { lobby: true, mediaFailures: 1 });
  await join(page);
  await expect(heading(page, WAITING)).toBeFocused();
  await control(page, 'admit');

  await expect(heading(page, FAILED)).toBeFocused();
  await expect(page.getByText(NO_MEDIA)).toBeVisible();
  await saidLike(page, /^The picture or sound of the call could not be set up\./);
  await expect(page.getByRole('link', { name: /another way/i })).toBeHidden(); // the page's setting says hidden
  expect(await live(page)).toEqual([0, 0]);
  await expectNoAxeViolations(page);

  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(heading(page, WAITING)).toBeFocused();
  await control(page, 'admit');
  await expect(heading(page, IN_CALL)).toBeFocused();
});

// ---- the end ---------------------------------------------------------------------------------------

test('the ended state thanks for the call, says that the data is deleted after 24 hours, keeps the way back, and leaves nothing open', async ({ page }) => {
  await inCall(page);
  await control(page, 'end');

  await expect(heading(page, 'The call has ended')).toBeFocused();
  const ended = state(page, 'ended');
  await expect(ended).toContainText('Thank you for the call.');
  await expect(ended).toContainText('The personal data of this call is deleted 24 hours after the end of your appointment.');
  await expect(ended).not.toContainText(/anonymi[sz]ed/i);
  await expect(ended).not.toContainText(/chat/i); // no chat exists on the page yet
  await expect(ended.getByRole('link', { name: 'Back to your appointment' })).toBeVisible();
  expect(await live(page)).toEqual([0, 0]);
  await expectNoAxeViolations(page);
});

test('the left state keeps its way back', async ({ page }) => {
  await inCall(page);
  await leave(page).click();
  await expect(heading(page, 'You left the call')).toBeFocused();
  await expect(state(page, 'left').getByRole('link', { name: 'Back to your appointment' })).toBeVisible();
  await expectNoAxeViolations(page);
});

test('the sentence about deletion is in Dutch too, and says deleted', async ({ page }) => {
  await page.goto(`${fixture('call_page')}?lang=nl`);
  const ended = await state(page, 'ended').textContent();
  expect(ended).toContain('24 uur na het einde van uw afspraak verwijderd');
  expect(ended).toContain('Bedankt voor het gesprek');
  expect(ended).not.toMatch(/geanonimiseerd|chat/i);
});

// ---- abuse cases -------------------------------------------------------------------------------------

test('what an event carries is never shown as text or markup, and an event of a call that is over changes nothing', async ({ page }) => {
  await inCall(page);
  const attack = { message: '<img src=x data-evil="1">Mallory says hello', name: 'Mallory' };
  await recording(page, true, attack);
  await reconnection(page, 'start', attack);
  await expect(notice(page, 'recording')).toBeVisible();
  await expect(notice(page, 'restoring')).toBeVisible();
  await expect(page.locator('[data-evil]')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('Mallory');

  await leave(page).click();
  await expect(heading(page, 'You left the call')).toBeFocused();
  const said_ = (await timesSaid(page, RESTORING)) + (await timesSaid(page, RECORDING));
  // The old meeting still talks; the page is no longer listening.
  await recording(page, false);
  await reconnection(page, 'success');
  await reconnection(page, 'fail', attack);
  await frames(page);
  await expect(heading(page, 'You left the call')).toBeFocused();
  await expect(state(page, 'left')).toBeVisible();
  await expect(state(page, 'failed')).toBeHidden();
  expect((await timesSaid(page, RESTORING)) + (await timesSaid(page, RECORDING))).toBe(said_);
  expect(await timesSaid(page, 'The recording has stopped.')).toBe(0);
  expect(await timesSaid(page, 'The connection is back.')).toBe(0);
});

// ---- the bar stays in view ----------------------------------------------------------------------------

for (const size of [
  { width: 1280, height: 400, name: 'a wide window 400 px high' },
  { width: 320, height: 400, name: '320 px wide and 400 px high' },
  { width: 320, height: 256, name: '400 % zoom (a 1280 by 1024 window)' },
]) {
  test(`with both notices and the panel shown the bar and the way out stay in view, ${size.name}`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await inCall(page);
    await recording(page, true);
    await reconnection(page, 'start');
    await expect(notice(page, 'recording')).toBeVisible();
    await expect(notice(page, 'restoring')).toBeVisible();
    await inView(page, bar(page), 'the bar');
    await inView(page, leave(page), 'the way out');
    await inView(page, devicesButton(page), 'the devices button');

    await devicesButton(page).click();
    await expect(panel(page)).toBeVisible();
    await inView(page, bar(page), 'the bar with the panel open');
    await inView(page, leave(page), 'the way out with the panel open');
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    await expectNoAxeViolations(page);
  });
}

test('every control of the call is at least 24 by 24 pixels, with the panel and the notices shown', async ({ page }) => {
  await stubSpeaker(page);
  await inCall(page);
  await recording(page, true);
  await devicesButton(page).click();
  for (const control_ of [...(await page.getByRole('button').all()), ...(await page.getByRole('combobox').all())]) {
    if (!(await control_.isVisible())) continue;
    const box = await control_.boundingBox();
    expect(box?.width).toBeGreaterThanOrEqual(24);
    expect(box?.height).toBeGreaterThanOrEqual(24);
  }
});

test('reduced motion and forced colours keep the notices and the panel readable', async ({ browser, browserName }) => {
  test.skip(browserName !== 'chromium', 'forced colours are emulated in Chromium');
  const context = await browser.newContext({ locale: 'en-US', reducedMotion: 'reduce', forcedColors: 'active' });
  const page = await context.newPage();
  await inCall(page);
  await recording(page, true);
  await devicesButton(page).click();
  await expect(notice(page, 'recording')).toBeVisible();
  const style = (selector: string) =>
    page.locator(selector).first().evaluate((el) => {
      const s = getComputedStyle(el);
      return { animation: s.animationName, transition: s.transitionDuration, border: s.borderTopStyle, left: s.borderLeftWidth, top: s.borderTopWidth };
    });
  const notices = await style('[data-call-notice="recording"]');
  expect(notices.animation).toBe('none');
  expect(notices.border).toBe('solid'); // the notice keeps an edge when the colours are the system's
  expect(Number.parseFloat(notices.left)).toBeGreaterThan(Number.parseFloat(notices.top));
  const opened = await style('[data-call-devices-button]');
  expect(opened.border).toBe('solid');
  expect(Number.parseFloat(opened.top)).toBeGreaterThanOrEqual(3); // open: a thicker edge, not colour alone
  const box = await style('[data-call-devices-panel]');
  expect(box.border).toBe('solid');
  expect(box.transition).toBe('0s');
  await context.close();
});
