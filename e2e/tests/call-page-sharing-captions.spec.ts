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

import { displayLog, displayMode, endDisplay, releaseDisplay, said, spy, statuses, stubDisplay, type DisplayMode } from '../call-page';
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
  releasePublish,
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
import { fixture } from '../pages';

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

/** The organisation's word for staff, as the page itself names the other side's picture (no name is known here). */
async function term(page: Page): Promise<string> {
  return (await remote(page).getAttribute('aria-label')) ?? '';
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

test('two presses in a row give one picker, one share and a stop that works', async ({ page }) => {
  await inCall(page, {}, 'held');
  await shareToggle(page).click(); // the picker is open
  await shareToggle(page).click(); // a second press does not end a share that is starting
  await frames(page);
  expect((await displayLog(page)).requests).toBe(1);
  expect(await callsOf(page, 'unpublishStreams')).toBe(0);
  await releaseDisplay(page);
  await pressed(shareToggle(page), true);
  await expect(notice(page, 'sharing')).toBeVisible();
  expect((await sdk(page)).published.filter((kind) => kind === 'screenShare')).toHaveLength(1);
  expect((await displayLog(page)).stops).toBe(0); // nothing was taken out while it was being sent
  await shareToggle(page).click();
  await said(page, STOPPED);
  await pressed(shareToggle(page), false);
  expect((await displayLog(page)).stops).toBeGreaterThan(0);
  await expect.poll(async () => callsOf(page, 'unpublishStreams')).toBe(1);
});

test('a press while the screen is being sent ends nothing: one stream in the meeting, none taken out, and a stop that works after', async ({ page }) => {
  await inCall(page, { holdPublish: true });
  await shareToggle(page).click();
  await expect.poll(async () => callsOf(page, 'publishStreams')).toBe(1); // the screen is on its way
  await shareToggle(page).click(); // presses while it is being sent
  await shareToggle(page).click();
  await frames(page);
  expect(await callsOf(page, 'unpublishStreams')).toBe(0);
  expect((await displayLog(page)).stops).toBe(0); // the capture is still there
  expect((await displayLog(page)).requests).toBe(1);
  await releasePublish(page);
  await pressed(shareToggle(page), true);
  await expect(notice(page, 'sharing')).toBeVisible();
  expect((await sdk(page)).published.filter((kind) => kind === 'screenShare')).toHaveLength(1);
  expect(await callsOf(page, 'unpublishStreams')).toBe(0);
  await shareToggle(page).click(); // and the stop works
  await said(page, STOPPED);
  await pressed(shareToggle(page), false);
  expect((await displayLog(page)).stops).toBeGreaterThan(0);
  await expect.poll(async () => callsOf(page, 'unpublishStreams')).toBe(1);
});

test('a share that ends while it is being sent is not said as on or off, and the stream that lands is taken out again', async ({ page }) => {
  await inCall(page, { holdPublish: true });
  await shareToggle(page).click();
  await expect.poll(async () => callsOf(page, 'publishStreams')).toBe(1);
  await endDisplay(page); // the browser's own control, before the screen has landed
  await pressed(shareToggle(page), false);
  await expect(notice(page, 'sharing')).toBeHidden();
  expect((await displayLog(page)).stops).toBeGreaterThan(0);
  const before = await callsOf(page, 'unpublishStreams');
  await releasePublish(page);
  await expect.poll(async () => callsOf(page, 'unpublishStreams')).toBe(before + 1); // out again after it landed
  await captionsToggle(page).click(); // a later sentence: what the region said before it is in order
  await said(page, CAPTIONS_ON);
  const spoken = await statuses(page);
  expect(spoken).not.toContain(SHARING);
  expect(spoken).not.toContain(STOPPED);
});

test('a meeting that stops allowing sharing while the picker is open gets no share when the picker answers', async ({ page }) => {
  await inCall(page, {}, 'held');
  await shareToggle(page).click();
  await expect.poll(async () => (await displayLog(page)).requests).toBe(1);
  await rights(page, false, true);
  await expect(shareToggle(page)).toBeHidden();
  await releaseDisplay(page);
  await expect.poll(async () => (await displayLog(page)).stops).toBeGreaterThan(0); // the screen is given back
  await expect(notice(page, 'sharing')).toBeHidden();
  expect((await sdk(page)).published).not.toContain('screenShare');
  await rights(page, true, true);
  await pressed(shareToggle(page), false); // and the state is off: a new press is possible
  await displayMode(page, 'ok');
  await shareNow(page);
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
  // The stop button had focus, then focus went elsewhere: the share ends and focus stays where it is.
  await shareNow(page);
  await stopButton(page).focus();
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
  const who = await term(page); // the label of the picture: no name is known here, so the team's word alone
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

// One test for each way the transcription ends; each joins once and waits on the double's own log
// (the call was made), not on a sentence of the polite region, whose queue is not what is tested.
for (const how of ['off', 'left', 'ended', 'failed'] as const) {
  test(`the transcription is stopped when ${how === 'off' ? 'captions go off' : `the call is ${how}`}`, async ({ page }) => {
    await inCall(page);
    await captionsToggle(page).click();
    await pressed(captionsToggle(page), true);
    await expect.poll(async () => (await captioning(page)).on).toBe(true);
    expect(await callsOf(page, 'stopTranscription')).toBe(0);
    if (how === 'off') await captionsToggle(page).click();
    else if (how === 'left') await leave(page).click();
    else if (how === 'ended') await control(page, 'end');
    else await reconnection(page, 'fail');
    await expect.poll(async () => callsOf(page, 'stopTranscription')).toBe(1);
    expect((await captioning(page)).on).toBe(false);
  });
}

test('a long caption can be read to its start and to its end with the keyboard, at twice the text size on a narrow window', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await inCall(page);
  await page.evaluate(() => (document.documentElement.style.fontSize = '200%'));
  await captionsToggle(page).click();
  await pressed(captionsToggle(page), true);
  const words = Array.from({ length: 40 }, (_, n) => `word${n}`);
  await caption(page, [{ id: '1', text: 'A short line' }, { id: '2', text: words.join(' ') }]);
  await expect(rowsOf(page)).toHaveCount(2);
  const box = captionBox(page);
  // Is the first (or last) word of the newest line inside the box's visible area? Client rects.
  const visible = (which: 'first' | 'last') =>
    box.evaluate((el, end) => {
      const text = (el.lastElementChild as HTMLElement).querySelector('.call-caption__text')?.firstChild as Text;
      const range = document.createRange();
      if (end === 'first') range.setStart(text, 0), range.setEnd(text, 5);
      else range.setStart(text, text.length - 5), range.setEnd(text, text.length);
      const r = range.getBoundingClientRect();
      const b = el.getBoundingClientRect();
      return r.top >= b.top - 1 && r.bottom <= b.bottom + 1;
    }, which);
  const scrolls = () => box.evaluate((el) => el.scrollHeight > el.clientHeight);
  expect(await scrolls()).toBe(true);
  expect(await box.getAttribute('tabindex')).toBe('0'); // higher than itself: a tab stop
  expect(await visible('first')).toBe(true); // the newest line begins inside the box
  expect(await visible('last')).toBe(false); // and does not end there

  await box.focus();
  await page.keyboard.press('End');
  await expect.poll(() => visible('last')).toBe(true); // the keyboard reaches its end
  await page.keyboard.press('Home');
  await expect.poll(() => box.evaluate((el) => el.scrollTop)).toBe(0); // a key scrolls over a few frames
  for (let i = 0; i < 30 && !(await visible('first')); i++) await page.keyboard.press('PageDown'); // and back to its start
  expect(await visible('first')).toBe(true);

  // A box with nothing higher than itself is no tab stop.
  await captionsToggle(page).click();
  await pressed(captionsToggle(page), false);
  await captionsToggle(page).click();
  await pressed(captionsToggle(page), true);
  await caption(page, [{ id: '3', text: 'Short' }]);
  await expect(rowsOf(page)).toHaveCount(1);
  expect(await scrolls()).toBe(false);
  expect(await box.getAttribute('tabindex')).toBeNull();
});

const LONG = (word: string): string => Array.from({ length: 40 }, (_, n) => `${word}${n}`).join(' ');

test('a reader who scrolled back is not moved by a change that brings no line, nor by an interim line that grows, and is by a new line', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await inCall(page);
  await page.evaluate(() => (document.documentElement.style.fontSize = '200%'));
  await captionsToggle(page).click();
  await pressed(captionsToggle(page), true);
  const first = { id: '1', text: LONG('one') };
  const second = { id: '2', text: LONG('two') };
  await caption(page, [first, second], [{ text: 'a' }]); // two long lines and an interim one
  const box = captionBox(page);
  const top = () => box.evaluate((el) => el.scrollTop);
  const middle = await box.evaluate((el) => {
    el.scrollTop = Math.floor((el.scrollHeight - el.clientHeight) / 2); // the reader is in the middle
    return el.scrollTop;
  });
  expect(middle).toBeGreaterThan(0);
  expect(middle).toBeLessThan(await box.evaluate((el) => el.scrollHeight - el.clientHeight));

  await page.getByRole('button', { name: /^Microphone/ }).click(); // a change that brings no line
  await pressed(page.getByRole('button', { name: /^Microphone/ }), false);
  await recording(page, true); // and another
  await expect(notice(page, 'recording')).toBeVisible();
  expect(await top()).toBe(middle);
  for (const growing of ['a b', 'a b c', 'a b c d']) {
    await caption(page, [first, second], [{ text: growing }]); // the interim line grows
    await expect(rowsOf(page).last().locator('.call-caption__text')).toHaveText(growing);
    expect(await top(), growing).toBe(middle);
  }
  expect(await box.evaluate((el) => (el as HTMLElement).dataset.newest ?? '')).not.toContain('a b'); // no caption text in an attribute

  await caption(page, [first, second, { id: '3', text: LONG('three') }]); // a new final line
  await expect.poll(top).toBeGreaterThan(middle); // moves the reader to the start of that line
  const place = await box.evaluate((el) => {
    const last = el.lastElementChild as HTMLElement;
    return { top: el.scrollTop, start: last.offsetTop, end: el.scrollHeight - el.clientHeight };
  });
  expect(Math.abs(place.top - Math.min(place.end, place.start)), JSON.stringify(place)).toBeLessThanOrEqual(1);
});

test('the box stays a tab stop while it has focus, and is none once it has not and cannot scroll', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await inCall(page);
  await captionsToggle(page).click();
  await pressed(captionsToggle(page), true);
  await caption(page, [{ id: '1', text: LONG('one') }]);
  const box = captionBox(page);
  const m0 = await box.evaluate((el) => ({ sh: el.scrollHeight, ch: el.clientHeight }));
  expect(m0.sh > m0.ch, `long line: scroll ${m0.sh} client ${m0.ch}`).toBe(true);
  expect(await box.getAttribute('tabindex')).toBe('0');
  await box.focus();
  // Three short lines push the long one out: the box can no longer scroll, but it has focus.
  await caption(page, [{ id: '1', text: LONG('one') }, { id: '2', text: 'Two' }, { id: '3', text: 'Three' }, { id: '4', text: 'Four' }]);
  await expect(rowsOf(page)).toHaveCount(3);
  const m = await box.evaluate((el) => ({ sh: el.scrollHeight, ch: el.clientHeight }));
  expect(m.sh > m.ch, `scroll ${m.sh} client ${m.ch}`).toBe(false);
  expect(await box.getAttribute('tabindex')).toBe('0');
  await expect(box).toBeFocused();
  // Focus has left: the next caption takes the tab stop away.
  await page.getByRole('button', { name: /^Microphone/ }).focus();
  await caption(page, [{ id: '5', text: 'Five' }]);
  await expect(rowsOf(page).last().locator('.call-caption__text')).toHaveText('Five');
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

/** Into the call in `lang`; Dutch is walked by data attributes, as the helpers' names are English. */
async function inCallIn(page: Page, lang: 'en' | 'nl'): Promise<void> {
  if (lang === 'en') return inCall(page);
  await stubToken(page, granted());
  await stubDisplay(page);
  await spy(page, { devices: 'made-up' });
  await page.addInitScript((p) => ((window as unknown as { __sdkPlan: Plan }).__sdkPlan = p), { captions: true });
  await page.goto(`${fixture('call_page')}?lang=nl`);
  await page.locator('[data-call-begin]').click();
  await expect(page.locator('[data-call-state="check"] [data-call-toggle="camera"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-call-state="check"] [data-call-toggle="microphone"]')).toHaveAttribute('aria-pressed', 'true');
  await page.locator('[data-call-join]').click();
  await expect(page.locator('[data-call-state="call"]')).toBeVisible();
}

const SIZES = [
  { width: 1280, height: 400, name: 'a wide window 400 px high', langs: ['en'] },
  { width: 1152, height: 600, name: '1152 px wide (72 rem) and 600 px high', langs: ['en', 'nl'] },
  { width: 1056, height: 600, name: '1056 px wide (66 rem) and 600 px high', langs: ['en'] },
  { width: 768, height: 600, name: '768 px wide and 600 px high', langs: ['en'] },
  { width: 736, height: 600, name: '736 px wide (46 rem) and 600 px high', langs: ['en', 'nl'] },
  { width: 641, height: 600, name: '641 px wide (just over 40 rem) and 600 px high', langs: ['en'] },
  { width: 320, height: 640, name: '320 px wide and 640 px high', langs: ['en'] },
  { width: 320, height: 400, name: '320 px wide and 400 px high', langs: ['en'] },
  { width: 320, height: 256, name: '400 % zoom (a 1280 by 1024 window)', langs: ['en'] },
] as const;

for (const size of SIZES) {
  for (const lang of size.langs) {
    test(`the one bar holds in the call with sharing, captions, every notice and the panel shown, ${size.name}, ${lang}`, async ({ page }) => {
      await page.setViewportSize({ width: size.width, height: size.height });
      await inCallIn(page, lang);
      const switchOf = (kind: 'captions' | 'share') => page.locator(`[data-call-switch="${kind}"]`);
      await switchOf('captions').click();
      await pressed(switchOf('captions'), true);
      await caption(page, [1, 2, 3, 4].map((n) => ({ id: String(n), text: `A line of the captions, number ${n}`, own: n % 2 === 0 })), [{ text: 'an interim line' }]);
      await recording(page, true);
      await reconnection(page, 'start');
      await switchOf('share').click();
      await pressed(switchOf('share'), true);
      await expect(notice(page, 'sharing')).toBeVisible();
      await expect(rowsOf(page)).toHaveCount(3);
      expect((await rowOf(page)).count).toBe(6); // microphone, camera, captions, share, devices and the way out
      await barHolds(page, size.height, 'sharing');
      // The captions are capped: they never take the picture's place or push the bar.
      const box = await captionBox(page).boundingBox();
      expect(box?.height ?? 0).toBeLessThanOrEqual(size.height / 2);

      // The panel of devices above the row: inside the window, its foot at the row's top.
      await page.locator('[data-call-devices-button]').click();
      const panel = await page.locator('[data-call-devices-panel]').boundingBox();
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
}

// ---- the picture gives way to what is really shown above and below it -----------------------------------
// The picture grows into what is left, up to 16 by 9 of the column's width, never below 8 rem. In three
// states (captions only; the participant's own share; the own share and the recording notice): at scroll 0
// the whole captions box is above the bar, the picture is at least 8 rem high and the captions follow it.
// The numbers are in the message of each assertion.

/** The boxes of the call at scroll 0, rounded; the tiles are those inside the stage. */
async function layoutOf(page: Page) {
  await page.evaluate(() => window.scrollTo(0, 0));
  return page.evaluate(() => {
    const box = (selector: string) => (document.querySelector(selector) as HTMLElement).getBoundingClientRect();
    const stage = box('[data-call-stage]');
    const captions = box('[data-call-captions]');
    const bar = box('[data-call-controls]');
    const minimum = Number.parseFloat(getComputedStyle(document.querySelector('[data-call-stage]') as HTMLElement).minHeight);
    const rect = (r: DOMRect) => ({ x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) });
    return {
      scrollY: window.scrollY,
      stage: rect(stage),
      stageMinimum: Math.round(minimum),
      captionsTop: Math.round(captions.top),
      captionsFoot: Math.round(captions.bottom),
      barTop: Math.round(bar.top),
      gap: Math.round(captions.top - stage.bottom),
      self: rect(box('.call-self')),
      remote: rect(box('[data-call-remote]')),
      share: rect(box('[data-call-share-view]')),
    };
  });
}
type Layout = Awaited<ReturnType<typeof layoutOf>>;

function expectAboveTheBar(m: Layout, label: string): void {
  const said_ = `${label}: ${JSON.stringify(m)}`;
  expect(m.scrollY, said_).toBe(0);
  expect(m.captionsFoot, `captions foot below the bar's top, ${said_}`).toBeLessThanOrEqual(m.barTop);
  expect(m.stage.h, `picture lower than its minimum, ${said_}`).toBeGreaterThanOrEqual(m.stageMinimum);
  expect(m.gap, `picture and captions not adjacent, ${said_}`).toBeGreaterThanOrEqual(0);
  expect(m.gap, `picture and captions not adjacent, ${said_}`).toBeLessThanOrEqual(16);
}

for (const size of [
  { width: 1280, height: 800 },
  { width: 1366, height: 650 },
  { width: 1280, height: 720 },
  { width: 1024, height: 700 },
  { width: 1920, height: 950 },
]) {
  for (const state_ of ['captions only', 'own share', 'own share and recording'] as const) {
    test(`the captions stay above the bar and next to the picture, ${state_}, ${size.width} by ${size.height}`, async ({ page }) => {
      await page.setViewportSize(size);
      await inCall(page);
      await captionsToggle(page).click();
      await pressed(captionsToggle(page), true);
      await caption(page, [{ id: '1', text: 'A first line of the captions', own: false }, { id: '2', text: 'A second line', own: true }]);
      if (state_ !== 'captions only') await shareNow(page);
      if (state_ === 'own share and recording') {
        await recording(page, true);
        await expect(notice(page, 'recording')).toBeVisible();
      }
      await expect(rowsOf(page)).toHaveCount(2);
      const m = await layoutOf(page);
      expectAboveTheBar(m, `${size.width}x${size.height} ${state_}`);
      // The own tile lies whole inside a stage that may be low.
      expect(m.self.h, JSON.stringify(m)).toBeLessThanOrEqual(m.stage.h);
      expect(m.self.y, JSON.stringify(m)).toBeGreaterThanOrEqual(m.stage.y);
      if (size.width === 1920 && state_ === 'captions only') {
        // The cap holds: 16 by 9 of the column's width, within a pixel.
        expect(Math.abs(m.stage.h - (m.stage.w * 9) / 16), JSON.stringify(m)).toBeLessThanOrEqual(1);
      }
    });
  }
}

test('the staff member\'s screen and both tiles lie inside the stage, with the captions above the bar, 1366 by 650', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 650 });
  await inCall(page);
  await captionsToggle(page).click();
  await pressed(captionsToggle(page), true);
  await caption(page, [{ id: '1', text: 'A first line of the captions', own: false }, { id: '2', text: 'A second line', own: true }]);
  await remoteShare(page, true);
  await expect(notice(page, 'theirs')).toBeVisible();
  await expect(rowsOf(page)).toHaveCount(2);
  const m = await layoutOf(page);
  const label = JSON.stringify(m);
  expectAboveTheBar(m, '1366x650 their screen');
  const inside = (r: { x: number; y: number; w: number; h: number }) =>
    r.x >= m.stage.x - 1 && r.y >= m.stage.y - 1 && r.x + r.w <= m.stage.x + m.stage.w + 1 && r.y + r.h <= m.stage.y + m.stage.h + 1;
  expect(inside(m.share), `the shared screen is not inside the stage: ${label}`).toBe(true);
  expect(inside(m.remote), `the staff member's tile is not inside the stage: ${label}`).toBe(true);
  expect(inside(m.self), `the own tile is not inside the stage: ${label}`).toBe(true);
  expect(m.remote.x + m.remote.w <= m.self.x, `the tiles overlap: ${label}`).toBe(true);
});

test('the sharing notice is one row on a wide window: the words on the left and the stop button on their right', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await inCall(page);
  await shareNow(page);
  const words = await notice(page, 'sharing').locator('span').first().boundingBox();
  const button = await stopButton(page).boundingBox();
  const whole = await notice(page, 'sharing').boundingBox();
  const m = JSON.stringify({ words, button, whole });
  expect(words && button && button.y < words.y + words.height, `the button is below the words: ${m}`).toBe(true);
  expect(words && button && button.x >= words.x + words.width - 1, `the button is not on the right of the words: ${m}`).toBe(true);
  expect(whole?.height ?? 999, m).toBeLessThan(100);
});

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
