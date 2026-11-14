/**
 * The call page in the call, second part (ADR 0123 step 5b), walked in a browser against the test
 * double of the video SDK: sharing a screen both ways and closed captions. What a participant sees
 * and hears: the toggles and when they are there, the notice with its stop button, the three ways a
 * share ends, a closed or broken picker, the staff member's screen in place of their picture, one
 * share in a meeting, the captions (who speaks, the last lines, an interim line, the size, no live
 * region), what is announced and how often, the keyboard alone, the bar at five window sizes, forced
 * colours and reduced motion, axe on each new state, and the abuse cases of `docs/threat-model.md`
 * (a screen captured without a press, a share that outlives the call, captions kept or sent away, a
 * caption shown as markup, a speaker's platform name shown).
 *
 * The double, the made-up devices and the screen picker are those of `../call-sdk` and
 * `../call-page`. Nothing here waits for a fixed time to prove that something did not happen but
 * one frame: every other wait is for a state. No test leans on an engine's autoplay or its own rules
 * for capture: the stub decides what the picker does.
 */
import { expect, test, type Locator, type Page } from '@playwright/test';

import { displayLog, displayMode, endDisplay, said, statuses, stubDisplay, type DisplayMode } from '../call-page';
import {
  callsOf,
  caption,
  captioning,
  control,
  FAILED,
  granted,
  heading,
  IN_CALL,
  join,
  open,
  reconnection,
  recording,
  remoteShare,
  rights,
  sdk,
  state,
  stopLocalShare,
  stubToken,
  timesSaid,
  WAITING,
  type Plan,
} from '../call-sdk';
import { expectNoAxeViolations, tabTo } from '../helpers';

test.use({ locale: 'en-US' });

const SHARING = 'You are sharing your screen.';
const STOPPED = 'You stopped sharing your screen.';
const SHARE_FAILED = 'Your screen could not be shared.';
const CAPTIONS_ON = 'Captions are on.';
const CAPTIONS_OFF = 'Captions are off.';
const CAPTIONS_FAILED = 'Captions could not be started. This call may not offer them.';

const shareToggle = (page: Page) => page.getByRole('button', { name: /^Share screen/ });
const captionsToggle = (page: Page) => page.getByRole('button', { name: /^Captions/ });
const stopButton = (page: Page) => page.getByRole('button', { name: 'Stop sharing' });
const notice = (page: Page, kind: 'sharing' | 'theirs') => page.locator(`[data-call-notice="${kind}"]`);
const captionBox = (page: Page) => page.locator('[data-call-captions]');
const rowsOf = (page: Page) => captionBox(page).locator('.call-caption');
const shareView = (page: Page) => page.locator('[data-call-share-view]');
const remote = (page: Page) => page.locator('[data-call-remote]');
const leave = (page: Page) => page.getByRole('button', { name: /^Leave the (call|waiting room)$/ });
const frames = (page: Page): Promise<unknown> => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

/** The organisation's word for staff, as the page itself names the other side's picture. */
async function term(page: Page): Promise<string> {
  const label = (await remote(page).getAttribute('aria-label')) ?? '';
  return label.replace('Picture of the ', '');
}

/** Into the call; the meeting offers captions and sharing unless the plan says otherwise. */
async function inCall(page: Page, plan: Plan = {}, picker: DisplayMode = 'ok'): Promise<void> {
  await stubToken(page, granted());
  await stubDisplay(page, picker);
  await open(page, { captions: true, ...plan });
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();
}

const pressed = (locator: Locator, value: boolean) => expect(locator).toHaveAttribute('aria-pressed', String(value));

/** The participant shares: a press, the picker answers, the sending is done. */
async function shareNow(page: Page): Promise<void> {
  await shareToggle(page).click();
  await pressed(shareToggle(page), true);
  await expect(notice(page, 'sharing')).toBeVisible();
}

// ---- the participant shares a screen ------------------------------------------------------------------

test('the toggle is there only in the call, only where the browser can capture a screen and the meeting allows it', async ({ page }) => {
  await stubToken(page, granted());
  await stubDisplay(page);
  await open(page, { lobby: true, captions: true });
  await join(page);
  await expect(heading(page, WAITING)).toBeFocused();
  // Sharing and captions make no sense before admission: not offered in the lobby.
  await expect(shareToggle(page)).toBeHidden();
  await expect(captionsToggle(page)).toBeHidden();
  await control(page, 'admit');
  await expect(heading(page, IN_CALL)).toBeFocused();
  await expect(shareToggle(page)).toBeVisible();
  await pressed(shareToggle(page), false);
  await expect(captionsToggle(page)).toBeVisible();
});

test('a browser that cannot capture a screen (a phone) is not offered the toggle', async ({ page }) => {
  await inCall(page, {}, 'absent');
  await expect(shareToggle(page)).toBeHidden();
  await expect(captionsToggle(page)).toBeVisible(); // captions do not need a screen
});

test('a meeting that does not allow sharing is not offered the toggle, and one that stops allowing it ends a share', async ({ page }) => {
  await inCall(page, { canShare: false });
  await expect(shareToggle(page)).toBeHidden();
  await rights(page, true, true);
  await expect(shareToggle(page)).toBeVisible();
  await shareNow(page);
  await rights(page, false, true);
  await expect(shareToggle(page)).toBeHidden();
  await expect(notice(page, 'sharing')).toBeHidden();
  expect((await displayLog(page)).stops).toBeGreaterThan(0);
});

test('nothing is captured before the press: the picker is asked once, at the press, and the screen goes to the meeting only', async ({ page }) => {
  await inCall(page);
  await frames(page);
  expect((await displayLog(page)).requests).toBe(0);
  expect(await callsOf(page, 'createDisplayStream')).toBe(0);
  await captionsToggle(page).click(); // another control of the bar is no press on sharing
  await pressed(captionsToggle(page), true);
  expect((await displayLog(page)).requests).toBe(0);

  await shareNow(page);
  expect((await displayLog(page)).requests).toBe(1);
  expect(await callsOf(page, 'createDisplayStream')).toBe(1);
  expect((await sdk(page)).published).toContain('screenShare');
});

test('while sharing a lasting notice with a stop button shows, the toggle is pressed and each change is said once', async ({ page }) => {
  await inCall(page);
  await shareNow(page);
  const notices = notice(page, 'sharing');
  await expect(notices.getByText('Sharing', { exact: true })).toBeVisible(); // a word, an icon, not colour alone
  await expect(notices.locator('svg')).toHaveCount(1);
  await expect(notices).toContainText(`The ${await term(page)} can see what you open on that screen.`);
  await said(page, SHARING);
  await expect(page.locator('[data-call-status]')).not.toHaveAttribute('aria-live', 'assertive');
  // The notice is text, not a live region of its own: the one polite region says it.
  expect(await notices.evaluate((el) => el.closest('[aria-live], [role="status"], [role="alert"]') !== null)).toBe(false);

  await stopButton(page).click();
  await said(page, STOPPED);
  await pressed(shareToggle(page), false);
  await expect(notices).toBeHidden();
  await expect(shareToggle(page)).toBeFocused(); // the button that went away had focus: it goes to the toggle
  await frames(page);
  expect(await timesSaid(page, SHARING)).toBe(1);
  expect(await timesSaid(page, STOPPED)).toBe(1);
});

for (const way of ['the stop button', "the browser's own control", 'the staff member'] as const) {
  test(`a share ends the same way from ${way}: the toggle and the notice go, the capture is given back, a new share works`, async ({ page }) => {
    await inCall(page);
    await shareNow(page);
    const before = await displayLog(page);
    if (way === 'the stop button') await stopButton(page).click();
    else if (way === 'the staff member') await stopLocalShare(page);
    else await endDisplay(page);

    await said(page, STOPPED);
    await pressed(shareToggle(page), false);
    await expect(notice(page, 'sharing')).toBeHidden();
    await expect.poll(async () => (await displayLog(page)).stops).toBeGreaterThan(before.stops); // the stream is stopped
    await expect.poll(async () => (await sdk(page)).unpublished.length).toBeGreaterThan(0); // and out of the meeting
    await frames(page);
    expect(await timesSaid(page, STOPPED)).toBe(1);

    await shareNow(page); // not stuck: a second share works
    expect((await displayLog(page)).requests).toBe(2);
  });
}

test('a picker that is closed is no error: nothing is said and the toggle stays off', async ({ page }) => {
  await inCall(page, {}, 'cancel');
  await shareToggle(page).click();
  await expect.poll(async () => (await displayLog(page)).requests).toBe(1);
  await frames(page);
  await pressed(shareToggle(page), false);
  await expect(notice(page, 'sharing')).toBeHidden();
  expect(await callsOf(page, 'unpublishStreams')).toBe(0);
  // Not stuck: the next press asks again. The sentences of the call's first moments (connecting,
  // let in) may still wait in the region's queue, so what the closed picker said is read by order:
  // the one polite region says its sentences one after the other, so everything the closed picker
  // had said comes before the start of the next share.
  await displayMode(page, 'ok');
  await shareNow(page);
  await said(page, SHARING);
  const spoken = await statuses(page);
  expect(spoken.slice(0, spoken.indexOf(SHARING))).not.toContain(SHARE_FAILED);
  expect(spoken.slice(0, spoken.indexOf(SHARING))).not.toContain(STOPPED);
  expect(spoken.filter((sentence) => /screen/i.test(sentence))).toEqual([SHARING]);
});

test('a screen the browser cannot give is said honestly, and so is one that cannot be sent (and then the capture is given back)', async ({ page }) => {
  await inCall(page, {}, 'broken');
  await shareToggle(page).click();
  await said(page, SHARE_FAILED);
  await pressed(shareToggle(page), false);
  await expect(notice(page, 'sharing')).toBeHidden();

  const refusing = await page.context().newPage();
  await stubToken(refusing, granted());
  await stubDisplay(refusing);
  await open(refusing, { captions: true, shareSendFails: true });
  await join(refusing);
  await expect(heading(refusing, IN_CALL)).toBeFocused();
  await shareToggle(refusing).click();
  await said(refusing, SHARE_FAILED);
  await pressed(shareToggle(refusing), false);
  await expect(notice(refusing, 'sharing')).toBeHidden();
  expect((await displayLog(refusing)).stops).toBeGreaterThan(0);
  expect((await sdk(refusing)).published).not.toContain('screenShare');
  expect(await callsOf(refusing, 'unpublishStreams')).toBe(1); // a half-sent stream is taken out of the meeting
});

test('leaving, the meeting ending and the connection failing each stop a share that is running', async ({ page }) => {
  await inCall(page);
  await shareNow(page);
  await leave(page).click();
  await expect(heading(page, 'You left the call')).toBeFocused();
  await expect.poll(async () => (await displayLog(page)).stops).toBeGreaterThan(0);

  for (const how of ['end', 'fail'] as const) {
    const other = await page.context().newPage();
    await inCall(other);
    await shareNow(other);
    if (how === 'end') await control(other, 'end');
    else await reconnection(other, 'fail');
    await expect(heading(other, how === 'end' ? 'The call has ended' : FAILED)).toBeVisible();
    await expect.poll(async () => (await displayLog(other)).stops, how).toBeGreaterThan(0);
    await expect(notice(other, 'sharing')).toBeHidden();
  }
});

// ---- the staff member shares a screen -----------------------------------------------------------------

test('the staff member\'s screen takes the place of their picture, their camera is a small tile, and each change is said once', async ({ page }) => {
  await inCall(page);
  const who = await term(page);
  await expect(shareView(page)).toBeHidden();
  const full = await remote(page).boundingBox();
  await remoteShare(page, true);
  await expect(notice(page, 'theirs')).toBeVisible();
  await expect(notice(page, 'theirs')).toContainText(`The ${who} is sharing their screen.`);
  await expect(shareView(page)).toBeVisible();
  expect(await shareView(page).evaluate((el) => (el as HTMLVideoElement).srcObject !== null)).toBe(true);
  await expect(shareView(page)).toHaveAccessibleName(`Screen shared by the ${who}`);
  const tile = await remote(page).boundingBox();
  const screen = await shareView(page).boundingBox();
  const self = await page.locator('[data-call-self="call"]').boundingBox();
  expect(tile && screen && tile.width < screen.width / 2).toBe(true); // the camera is a small tile
  expect(tile && full && tile.width < full.width / 2).toBe(true);
  expect(tile && self && tile.x < self.x).toBe(true); // on the left, the participant's own on the right
  await said(page, `The ${who} is sharing their screen.`);

  await remoteShare(page, false);
  await said(page, `The ${who} stopped sharing their screen.`);
  await expect(notice(page, 'theirs')).toBeHidden();
  await expect(shareView(page)).toBeHidden();
  const back = await remote(page).boundingBox();
  expect(back && full && Math.abs(back.width - full.width) < 2).toBe(true); // their picture returns
  await frames(page);
  expect(await timesSaid(page, `The ${who} is sharing their screen.`)).toBe(1);
  expect(await timesSaid(page, `The ${who} stopped sharing their screen.`)).toBe(1);
});

test('a staff member who shares while the participant does ends the participant\'s share first, in one sentence, and there are never two', async ({ page }) => {
  await inCall(page);
  const who = await term(page);
  await shareNow(page);
  const before = await displayLog(page);
  await remoteShare(page, true);
  await said(page, `The ${who} started sharing their screen, so your own sharing has stopped.`);
  await expect(notice(page, 'sharing')).toBeHidden();
  await expect(notice(page, 'theirs')).toBeVisible();
  await expect.poll(async () => (await displayLog(page)).stops).toBeGreaterThan(before.stops);
  await pressed(shareToggle(page), false);
  await expect(shareToggle(page)).toHaveAttribute('aria-disabled', 'true');
  // A press while their screen is shared (with the keyboard: a mouse cannot press a button that says
  // it is disabled) starts no second picker and no second share, and says why, in the one region.
  await shareToggle(page).focus();
  await page.keyboard.press('Space');
  await said(page, `The ${who} is sharing their screen.`);
  expect((await displayLog(page)).requests).toBe(1);
  await pressed(shareToggle(page), false);
  expect(await timesSaid(page, SHARING)).toBe(1); // said once at the start, never again

  await remoteShare(page, false);
  await expect(shareToggle(page)).toHaveAttribute('aria-disabled', 'false');
  await shareNow(page);
});

test('a screen the staff member already shares when the participant is let in is shown and said once', async ({ page }) => {
  await stubToken(page, granted());
  await stubDisplay(page);
  await open(page, { lobby: true });
  await join(page);
  await expect(heading(page, WAITING)).toBeFocused();
  await remoteShare(page, true);
  await expect(notice(page, 'theirs')).toBeHidden(); // not in the lobby
  await control(page, 'admit');
  await expect(heading(page, IN_CALL)).toBeFocused();
  await expect(notice(page, 'theirs')).toBeVisible();
  await expect(shareView(page)).toBeVisible();
  await said(page, `The ${await term(page)} is sharing their screen.`);
});

test('the notice says that the button in the bar stops the share too, and a stop button that had focus hands it to the toggle', async ({ page }) => {
  await inCall(page);
  await shareNow(page);
  await expect(notice(page, 'sharing')).toContainText('The Share screen button in the bar at the foot of the page stops it too.');
  await stopButton(page).focus();
  await page.keyboard.press('Enter');
  await said(page, STOPPED);
  await expect(shareToggle(page)).toBeFocused();
  // Ended by the browser's own control while the stop button had focus: the same.
  await shareNow(page);
  await stopButton(page).focus();
  await endDisplay(page);
  await pressed(shareToggle(page), false);
  await expect(shareToggle(page)).toBeFocused();
  // Ended while focus was elsewhere: focus stays where it was.
  await shareNow(page);
  await page.getByRole('button', { name: /^Microphone/ }).focus();
  await stopLocalShare(page);
  await pressed(shareToggle(page), false);
  await expect(page.getByRole('button', { name: /^Microphone/ })).toBeFocused();
});

// ---- closed captions -----------------------------------------------------------------------------------

test('captions are offered only when the meeting has them, and the real-time link is asked for only at the press', async ({ page }) => {
  await inCall(page, { captions: false });
  await expect(captionsToggle(page)).toBeHidden();
  await rights(page, true, true);
  await expect(captionsToggle(page)).toBeVisible();
  await pressed(captionsToggle(page), false);

  // Joined with the automatic link off, and nothing asked of the captions before the press.
  expect((await sdk(page)).inits.map((i) => i.automaticLLM)).toEqual([false]);
  expect(await callsOf(page, 'updateLLMConnection')).toBe(0);
  expect(await callsOf(page, 'startTranscription')).toBe(0);

  await captionsToggle(page).click();
  await said(page, CAPTIONS_ON);
  await pressed(captionsToggle(page), true);
  expect(await callsOf(page, 'updateLLMConnection')).toBe(1);
  expect(await callsOf(page, 'startTranscription')).toBe(1);
  await expect.poll(async () => (await captioning(page)).language).toBe('en'); // the page's language
  expect((await captioning(page)).on).toBe(true);
});

test('captions show who speaks in the page\'s own words, never the platform\'s name, newest lines last, an interim line until its final text', async ({ page }) => {
  await inCall(page);
  const who = (await term(page)).replace(/^./, (c) => c.toUpperCase());
  await captionsToggle(page).click();
  await said(page, CAPTIONS_ON);

  await caption(page, [{ id: '1', text: 'Can you hear me?', own: false }], [{ text: 'Yes I can', own: true }]);
  await expect(rowsOf(page)).toHaveCount(2);
  await expect(rowsOf(page).nth(0).locator('.call-caption__who')).toHaveText(who);
  await expect(rowsOf(page).nth(0).locator('.call-caption__text')).toHaveText('Can you hear me?');
  await expect(rowsOf(page).nth(1).locator('.call-caption__who')).toHaveText('You');
  await expect(rowsOf(page).nth(1)).toHaveClass(/call-caption--interim/); // shown as an interim line
  expect(await rowsOf(page).nth(1).locator('.call-caption__text').evaluate((el) => getComputedStyle(el).fontStyle)).toBe('italic');

  // The final text replaces the interim line; the line told again is not doubled.
  await caption(page, [
    { id: '1', text: 'Can you hear me?', own: false },
    { id: '2', text: 'Yes I can hear you', own: true },
  ]);
  await expect(rowsOf(page)).toHaveCount(2);
  await expect(rowsOf(page).nth(1).locator('.call-caption__text')).toHaveText('Yes I can hear you');
  await expect(captionBox(page).locator('.call-caption--interim')).toHaveCount(0);

  // Only the last few lines are kept in view.
  const all = [3, 4, 5, 6].map((n) => ({ id: String(n), text: `Line ${n}`, own: n % 2 === 0 }));
  await caption(page, [{ id: '1', text: 'Can you hear me?' }, { id: '2', text: 'Yes I can hear you', own: true }, ...all]);
  await expect(rowsOf(page)).toHaveCount(3);
  await expect(rowsOf(page).locator('.call-caption__text')).toHaveText(['Line 4', 'Line 5', 'Line 6']);

  // The platform's names for the speakers are never shown, nor kept in the markup.
  await expect(page.locator('body')).not.toContainText('Platform');
  expect(await page.content()).not.toContain('Platform');
});

test('captions are ordinary text, not a live region: nothing of them reaches the one polite region', async ({ page }) => {
  await inCall(page);
  await captionsToggle(page).click();
  await said(page, CAPTIONS_ON);
  await caption(page, [{ id: '1', text: 'A caption nobody should hear twice' }], [{ text: 'and an interim one' }]);
  await expect(rowsOf(page)).toHaveCount(2);
  const inLive = await captionBox(page).evaluate((el) => ({
    around: el.closest('[aria-live], [role="status"], [role="log"], [role="alert"], [aria-atomic]') !== null,
    within: el.querySelector('[aria-live], [role="status"], [role="log"], [role="alert"], [aria-atomic]') !== null,
    group: el.getAttribute('role'),
  }));
  expect(inLive).toEqual({ around: false, within: false, group: 'group' });
  await frames(page);
  expect((await statuses(page)).join(' ')).not.toContain('caption nobody');
  expect((await statuses(page)).join(' ')).not.toContain('interim');
});

test('captions follow the page\'s text size, and a caption with markup is shown as text', async ({ page }) => {
  await inCall(page);
  await captionsToggle(page).click();
  await said(page, CAPTIONS_ON);
  await caption(page, [{ id: '1', text: '<img src=x data-evil="1">Mallory says hello' }]);
  await expect(rowsOf(page)).toHaveCount(1);
  await expect(page.locator('[data-evil]')).toHaveCount(0);
  await expect(rowsOf(page).first()).toContainText('<img src=x data-evil="1">Mallory says hello');

  const ratio = () =>
    captionBox(page).evaluate((el) => parseFloat(getComputedStyle(el).fontSize) / parseFloat(getComputedStyle(document.documentElement).fontSize));
  const normal = await ratio();
  expect(normal).toBeGreaterThanOrEqual(1);
  await page.evaluate(() => (document.documentElement.style.fontSize = '200%'));
  expect(await ratio()).toBeCloseTo(normal, 5); // rem: the size follows the root, no fixed pixels
  expect(await captionBox(page).evaluate((el) => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThan(30);
});

test('captions switched off stop, the lines go and the toggle says so; switched on again they start empty', async ({ page }) => {
  await inCall(page);
  await captionsToggle(page).click();
  await said(page, CAPTIONS_ON);
  await caption(page, [{ id: '1', text: 'Something said' }]);
  await expect(rowsOf(page)).toHaveCount(1);

  await captionsToggle(page).click();
  await said(page, CAPTIONS_OFF);
  await pressed(captionsToggle(page), false);
  await expect(captionBox(page)).toBeHidden();
  expect(await callsOf(page, 'stopTranscription')).toBeGreaterThan(0);
  expect((await captioning(page)).on).toBe(false);
  await caption(page, [{ id: '9', text: 'Late caption' }]); // one that still arrives is not shown
  await frames(page);
  await expect(captionBox(page)).toBeHidden();

  await captionsToggle(page).click();
  await pressed(captionsToggle(page), true);
  await expect(rowsOf(page)).toHaveCount(0);
  await frames(page);
  expect(await timesSaid(page, CAPTIONS_ON)).toBeGreaterThan(0);
  expect(await timesSaid(page, CAPTIONS_OFF)).toBe(1);
});

test('the transcription is stopped when captions go off and when the call is left, ended or fails', async ({ page }) => {
  await inCall(page);
  await captionsToggle(page).click();
  await said(page, CAPTIONS_ON);
  await captionsToggle(page).click();
  await said(page, CAPTIONS_OFF);
  expect(await callsOf(page, 'stopTranscription')).toBe(1);
  expect((await captioning(page)).on).toBe(false);

  for (const how of ['leave', 'end', 'fail'] as const) {
    const other = await page.context().newPage();
    await inCall(other);
    await captionsToggle(other).click();
    await said(other, CAPTIONS_ON);
    expect(await callsOf(other, 'stopTranscription')).toBe(0);
    if (how === 'leave') await leave(other).click();
    else if (how === 'end') await control(other, 'end');
    else await reconnection(other, 'fail');
    await expect.poll(async () => callsOf(other, 'stopTranscription'), how).toBe(1);
    expect((await captioning(other)).on).toBe(false);
  }
});

test('a long caption can be read to its start, at twice the text size on a narrow window', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await inCall(page);
  await page.evaluate(() => (document.documentElement.style.fontSize = '200%'));
  await captionsToggle(page).click();
  await said(page, CAPTIONS_ON);
  const long = Array.from({ length: 40 }, (_, n) => `word${n}`).join(' ');
  await caption(page, [{ id: '1', text: 'A short line' }, { id: '2', text: long }]);
  await expect(rowsOf(page)).toHaveCount(2);
  const box = captionBox(page);
  const measure = () =>
    box.evaluate((el) => {
      const last = el.lastElementChild as HTMLElement;
      const b = el.getBoundingClientRect();
      const r = last.getBoundingClientRect();
      return { scrolls: el.scrollHeight > el.clientHeight, top: r.top - b.top, scrollTop: el.scrollTop, max: el.scrollHeight - el.clientHeight, tab: el.getAttribute('tabindex') };
    });
  const m = await measure();
  expect(m.scrolls).toBe(true);
  expect(m.tab).toBe('0'); // it can scroll, so the keyboard can reach it
  expect(m.top).toBeGreaterThanOrEqual(-1); // the newest line begins inside the box
  // The keyboard reads on to its end, and back to its start.
  await box.focus();
  await page.keyboard.press('End');
  expect((await measure()).scrollTop).toBeGreaterThan(m.scrollTop);
  await page.keyboard.press('Home');
  expect((await measure()).scrollTop).toBe(0);
  // A box with nothing to scroll is no tab stop.
  await captionsToggle(page).click();
  await pressed(captionsToggle(page), false);
  await captionsToggle(page).click();
  await pressed(captionsToggle(page), true);
  await caption(page, [{ id: '3', text: 'Short' }]);
  await expect(rowsOf(page).last().locator('.call-caption__text')).toHaveText('Short');
  expect(await box.getAttribute('tabindex')).toBeNull();
});

test('captions that cannot be started leave the toggle off and say so in one sentence', async ({ page }) => {
  await inCall(page, { captionsFail: true });
  await captionsToggle(page).click();
  await said(page, CAPTIONS_FAILED);
  await pressed(captionsToggle(page), false);
  await expect(captionBox(page)).toBeHidden();
  await frames(page);
  expect(await timesSaid(page, CAPTIONS_FAILED)).toBe(1);
  expect(await timesSaid(page, CAPTIONS_ON)).toBe(0);
});

test('captions live in the page\'s memory only: in no storage, in no request, and gone when the call is left', async ({ page }) => {
  await inCall(page);
  const sent: string[] = [];
  page.on('request', (request) => sent.push(`${request.url()} ${request.postData() ?? ''}`));
  await captionsToggle(page).click();
  await said(page, CAPTIONS_ON);
  await caption(page, [{ id: '1', text: 'Secret words of the call' }], [{ text: 'and some interim words' }]);
  await expect(rowsOf(page)).toHaveCount(2);

  expect(sent.filter((line) => /Secret|interim/.test(line))).toEqual([]);
  expect(sent.filter((line) => !line.startsWith(`${new URL(page.url()).origin}/`))).toEqual([]); // nothing but our own origin
  const kept = await page.evaluate(async () => ({
    local: localStorage.length,
    session: sessionStorage.length,
    cookie: document.cookie.includes('Secret'),
    databases: indexedDB.databases ? (await indexedDB.databases()).length : 0,
  }));
  expect(kept).toEqual({ local: 0, session: 0, cookie: false, databases: 0 });

  await leave(page).click();
  await expect(heading(page, 'You left the call')).toBeFocused();
  await expect(page.locator('body')).not.toContainText('Secret words');
  expect(await captionBox(page).evaluate((el) => el.childElementCount)).toBe(0);
  expect(await page.content()).not.toContain('Secret words');
});

// ---- the keyboard alone ----------------------------------------------------------------------------------

test('captions and sharing are reachable and work with the keyboard alone, in the page\'s own tab order', async ({ page }) => {
  await inCall(page);
  await tabTo(page, captionsToggle(page));
  await page.keyboard.press('Enter');
  await said(page, CAPTIONS_ON);
  await pressed(captionsToggle(page), true);
  await expect(captionsToggle(page)).toBeFocused(); // a change inside a state steals no focus

  await page.keyboard.press('Tab'); // the order is the bar's: microphone, camera, captions, share
  await expect(shareToggle(page)).toBeFocused();
  await page.keyboard.press('Space');
  await said(page, SHARING);
  await pressed(shareToggle(page), true);
  await expect(shareToggle(page)).toBeFocused();

  // The toggle stops the share too, so no one has to leave the bar for the notice's button.
  await page.keyboard.press('Space');
  await said(page, STOPPED);
  await pressed(shareToggle(page), false);
  await expect(shareToggle(page)).toBeFocused();
  await expect(notice(page, 'sharing')).toBeHidden();
});

// ---- axe on each new state -----------------------------------------------------------------------------

test('axe finds nothing in the new states: sharing, their screen shared, captions with lines', async ({ page }) => {
  await inCall(page);
  await captionsToggle(page).click();
  await said(page, CAPTIONS_ON);
  await caption(page, [{ id: '1', text: 'Hello there', own: false }, { id: '2', text: 'Good morning', own: true }], [{ text: 'and' }]);
  await expect(rowsOf(page)).toHaveCount(3);
  await expectNoAxeViolations(page);
  await shareNow(page);
  await expectNoAxeViolations(page);
  await remoteShare(page, true);
  await expect(notice(page, 'theirs')).toBeVisible();
  await expectNoAxeViolations(page);
});

// ---- the bar with the new buttons -----------------------------------------------------------------------

/** The row of buttons: from the top of the highest to the bottom of the lowest of the bar's buttons. */
async function rowOf(page: Page): Promise<{ top: number; bottom: number; count: number }> {
  const boxes = await page.locator('.call-controls button:visible').evaluateAll((els) =>
    els.map((el) => {
      const r = el.getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, width: r.width, height: r.height };
    }),
  );
  for (const b of boxes) {
    expect(b.width).toBeGreaterThanOrEqual(24);
    expect(b.height).toBeGreaterThanOrEqual(24);
  }
  return { top: Math.min(...boxes.map((b) => b.top)), bottom: Math.max(...boxes.map((b) => b.bottom)), count: boxes.length };
}

/** The row is inside the window and at most half of it, where the page is, scrolled to its top and to its end. */
async function barHolds(page: Page, height: number, what: string): Promise<void> {
  for (const at of ['as it is', 'at the top', 'at the end']) {
    if (at === 'at the top') await page.evaluate(() => window.scrollTo(0, 0));
    if (at === 'at the end') await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const row = await rowOf(page);
    expect(row.top, `${what}, ${at}: top`).toBeGreaterThanOrEqual(0);
    expect(row.bottom, `${what}, ${at}: bottom`).toBeLessThanOrEqual(height);
    expect(row.bottom - row.top, `${what}, ${at}: height`).toBeLessThanOrEqual(height / 2);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
}

for (const size of [
  { width: 1280, height: 400, name: 'a wide window 400 px high' },
  { width: 1056, height: 600, name: '1056 px wide (66 rem) and 600 px high' },
  { width: 768, height: 600, name: '768 px wide and 600 px high' },
  { width: 641, height: 600, name: '641 px wide (just over 40 rem) and 600 px high' },
  { width: 320, height: 640, name: '320 px wide and 640 px high' },
  { width: 320, height: 400, name: '320 px wide and 400 px high' },
  { width: 320, height: 256, name: '400 % zoom (a 1280 by 1024 window)' },
]) {
  test(`the one bar holds in the call with sharing, captions, every notice and the panel shown, ${size.name}`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await inCall(page);
    await captionsToggle(page).click();
    await said(page, CAPTIONS_ON);
    await caption(page, [1, 2, 3, 4].map((n) => ({ id: String(n), text: `A line of the captions, number ${n}`, own: n % 2 === 0 })), [{ text: 'an interim line' }]);
    await recording(page, true);
    await reconnection(page, 'start');
    await shareNow(page);
    await expect(rowsOf(page)).toHaveCount(3);
    expect((await rowOf(page)).count).toBe(6); // microphone, camera, captions, share, devices and the way out
    await barHolds(page, size.height, 'sharing');
    // The captions are capped: they never take the picture's place or push the bar.
    const box = await captionBox(page).boundingBox();
    expect(box?.height ?? 0).toBeLessThanOrEqual(size.height / 2);

    // The panel of devices above the row: inside the window, its foot at the row's top.
    await page.getByRole('button', { name: 'Devices' }).click();
    const panel = await page.getByRole('group', { name: 'Your devices' }).boundingBox();
    const row = await rowOf(page);
    expect(panel?.y ?? -1, 'panel top').toBeGreaterThanOrEqual(0);
    expect((panel?.y ?? 0) + (panel?.height ?? 0), 'panel foot').toBeLessThanOrEqual(row.top + 1);
    await page.keyboard.press('Escape');

    await remoteShare(page, true); // the staff member's screen instead
    await expect(notice(page, 'theirs')).toBeVisible();
    await barHolds(page, size.height, 'their screen');
    await expectNoAxeViolations(page);
  });
}

test('the buttons keep their full names for assistive technology when the window is low', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 256 });
  await inCall(page);
  await expect(page.getByRole('button', { name: /^Share screen/ })).toBeVisible();
  await expect(shareToggle(page)).toHaveAccessibleName(/^Share screen/);
  expect(await shareToggle(page).locator('.call-label-short').evaluate((el) => getComputedStyle(el).display)).not.toBe('none');
});

// ---- forced colours and reduced motion --------------------------------------------------------------------

test('forced colours and reduced motion keep the new states visible, each with a word and an edge of its own', async ({ browser, browserName }) => {
  test.skip(browserName !== 'chromium', 'forced colours are emulated in Chromium');
  const context = await browser.newContext({ locale: 'en-US', reducedMotion: 'reduce', forcedColors: 'active' });
  const page = await context.newPage();
  await inCall(page);
  await captionsToggle(page).click();
  await said(page, CAPTIONS_ON);
  await caption(page, [{ id: '1', text: 'Words on the screen' }], [{ text: 'and more' }]);
  await shareNow(page);
  await expect(notice(page, 'sharing').getByText('Sharing', { exact: true })).toBeVisible();
  await expect(rowsOf(page)).toHaveCount(2);
  const style = (locator: Locator) =>
    locator.first().evaluate((el) => {
      const s = getComputedStyle(el);
      return { border: s.borderTopStyle, left: s.borderLeftWidth, top: s.borderTopWidth };
    });
  const edge = await style(notice(page, 'sharing'));
  expect(edge.border).toBe('solid');
  expect(Number.parseFloat(edge.left)).toBeGreaterThan(Number.parseFloat(edge.top));
  const on = await style(shareToggle(page));
  expect(on.border).toBe('solid');
  expect(Number.parseFloat(on.top)).toBeGreaterThanOrEqual(3); // on: a thicker edge, not colour alone
  expect((await style(captionBox(page))).border).toBe('solid');
  await remoteShare(page, true);
  await expect(notice(page, 'theirs').getByText('Screen shared', { exact: true })).toBeVisible();
  expect((await style(remote(page))).border).toBe('solid'); // the small tile has its own edge
  await context.close();
});

test('a connection that fails takes the staff member\'s screen, the captions and the notices away with it', async ({ page }) => {
  await inCall(page);
  await remoteShare(page, true);
  await expect(shareView(page)).toBeVisible();
  await reconnection(page, 'fail');
  await expect(state(page, 'failed')).toBeVisible();
  await expect(shareView(page)).toBeHidden();
  await expect(captionBox(page)).toBeHidden();
  await expect(notice(page, 'theirs')).toBeHidden();
});
