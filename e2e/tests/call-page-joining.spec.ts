/**
 * The call page from the click that joins (ADR 0123 step 4c), walked in a browser: the SDK loaded
 * with its checksum, the token asked on the click, connecting with retries, the lobby, the first
 * moment of the call, leaving, the meeting's end, the failure state after the last attempt and each
 * refusal of the token endpoint, the keyboard alone, what is announced and where focus goes, and that
 * the token never reaches the page, an address or a storage.
 *
 * The stack places a test double of the video SDK over the vendored file (e2e/vendor/webex/, see
 * .github/actions/e2e-stack); what it does is chosen per test with `window.__sdkPlan` before the
 * page loads and read back from `window.__sdk`. A stack without it makes these tests FAIL, with the
 * sentence that says how to place it: a green run must mean the joining was walked. The token
 * endpoint is answered by the tests (the example booking is two hours away, outside the join
 * window), except in the one test that asks the real endpoint.
 *
 * The devices are the made-up ones of `../call-page` (two cameras, two microphones, a canvas
 * picture and an oscillator sound, in every browser). Nothing here waits for a fixed time to prove
 * that something did not happen: every wait is for a state of the page or of the stand-in.
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

import { expect, test, type Page } from '@playwright/test';

import { begin, live, openCallPage, READY, said, spy, statuses } from '../call-page';
import {
  APP,
  CONNECTING,
  FAILED,
  granted,
  heading,
  IN_CALL,
  join,
  joinButton,
  LINK,
  control,
  NO_STAND_IN,
  open,
  RETRY,
  remoteCamera,
  saidLike,
  sdk,
  state,
  stubToken,
  timesSaid,
  TOKEN,
  WAITING,
  type Plan,
} from '../call-sdk';
import { expectNoAxeViolations, tabTo } from '../helpers';

test.use({ locale: 'en-US' });

/**
 * The page's contract is what it does with the answer of `play()` on the other side's sound, not
 * what an engine does with a made-up stream: the stub decides for that element, in both phases
 * (`window.__blockPlay`: refuse with NotAllowedError; else resolve; `__hangPlay`: never settle), and
 * counts the calls (`__plays`). The engines' own autoplay rules are seen only in a real meeting.
 */
async function stubRemoteSound(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __blockPlay: boolean; __hangPlay: boolean; __plays: number };
    w.__blockPlay = true;
    w.__hangPlay = false;
    w.__plays = 0;
    const original = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function (this: HTMLMediaElement) {
      if (!this.hasAttribute('data-call-remote-audio')) return original.call(this);
      w.__plays += 1;
      if (w.__hangPlay) return new Promise<void>(() => {});
      return w.__blockPlay ? Promise.reject(new DOMException('blocked', 'NotAllowedError')) : Promise.resolve();
    };
  });
}
const plays = (page: Page): Promise<number> => page.evaluate(() => (window as unknown as { __plays: number }).__plays);


test('before the click nothing leaves the origin; the click loads the file, asks for the token once and starts the SDK', async ({ page }) => {
  const asked: string[] = [];
  page.on('request', (request) => asked.push(`${request.method()} ${request.url()}`));
  const tokens = await stubToken(page, granted());
  await open(page);
  await begin(page).click();
  await said(page, READY);
  await page.getByRole('button', { name: 'Play a test sound' }).click();
  await expect(page.getByText('You now hear two tones.')).toBeVisible();

  // The page, the check, a test sound: no token, no SDK file, nobody else.
  expect(tokens).toHaveLength(0);
  expect(asked.filter((line) => /call\/token|vendor\/webex/.test(line))).toEqual([]);
  const strangers = (): string[] => asked.map((line) => new URL(line.split(' ')[1]).origin).filter((o) => !['null', APP].includes(o));
  expect(strangers()).toEqual([]);

  await joinButton(page).click();
  await expect(heading(page, IN_CALL)).toBeVisible();
  expect(tokens).toHaveLength(1);
  expect(tokens[0].method()).toBe('POST');
  expect(tokens[0].postData()).toBeNull();
  expect(asked.filter((line) => /vendor\/webex/.test(line))).toHaveLength(1);
  // Everything the page and the SDK stand-in asked stays on the page's own origin.
  expect(strangers()).toEqual([]);
});

test('the SDK file comes first and the token second: a file the browser refuses costs no token and ends the joining at once', async ({ page }) => {
  const tokens = await stubToken(page, granted());
  // The server names the file with a checksum; a swapped file in the browser's hands is refused.
  await page.route('**/static/vendor/webex/meetings.min.js*', (route) =>
    route.fulfill({ status: 200, contentType: 'text/javascript', body: 'window.Webex = {};' }),
  );
  await open(page);
  await join(page);

  await expect(heading(page, 'The call is not available in this page yet')).toBeFocused();
  await expect(page.getByText('We tried 3 times').first()).toBeHidden();
  expect(tokens).toHaveLength(0);
});

test('the script tag carries the manifest checksum and the SDK is started with the token, the link and its logs off', async ({ page }) => {
  await stubToken(page, granted());
  await open(page);
  const expected = `sha256-${createHash('sha256').update(readFileSync(new URL('../vendor/webex/meetings.min.js', import.meta.url))).digest('base64')}`;
  await expect(page.locator('[data-call]')).toHaveAttribute('data-call-sdk-integrity', expected);
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();

  // The page made the tag the browser checked, with the same checksum, and no other script tag.
  await expect(page.locator('script[src*="vendor/webex"]')).toHaveAttribute('integrity', expected);
  expect(await page.locator('script[src*="vendor/webex"]').count()).toBe(1);

  const log = await sdk(page);
  expect(log.inits).toEqual([{ token: TOKEN, autoUploadLogs: false, ephemeral: true }]);
  expect(log.link).toBe(LINK);
  expect(log.calls).toEqual(['init', 'register', 'create', 'join', 'createCameraStream', 'createMicrophoneStream', 'addMedia']);
  expect(log.added).toEqual(['microphone', 'camera']);
});

test('the token and the link are nowhere in the page, an address, a storage or a cookie', async ({ page }) => {
  await stubToken(page, granted());
  await open(page);
  await join(page);
  await expect(heading(page, IN_CALL)).toBeVisible();
  const seen = await page.evaluate(() => ({
    html: document.documentElement.outerHTML,
    url: location.href,
    local: JSON.stringify({ ...localStorage }),
    session: JSON.stringify({ ...sessionStorage }),
    cookie: document.cookie,
    title: document.title,
  }));
  for (const [where, text] of Object.entries(seen)) {
    expect(text, where).not.toContain(TOKEN);
    expect(text, where).not.toContain('meeting.example.test');
  }
});

test('connecting is visible and says so, once, when it tries again by itself; one token serves the attempts of a click', async ({ page }) => {
  const tokens = await stubToken(page, granted());
  // Two attempts fail at once; the third takes a moment, so the page can be looked at while it runs.
  await open(page, { failures: 2, delay: 3000 });
  await join(page);

  await expect(heading(page, CONNECTING)).toBeVisible();
  await expect(page.getByText('Attempt 3 of 3')).toBeVisible();
  await expect(state(page, 'connecting').getByText(RETRY)).toBeVisible();
  await saidLike(page, /^Setting up your call\.$/);
  await saidLike(page, new RegExp(`^${RETRY.replace(/\./g, '\\.')}$`));
  await expect(heading(page, IN_CALL)).toBeFocused();

  const log = await sdk(page);
  expect(log.calls.filter((c) => c === 'register')).toHaveLength(3);
  // The count stays on the page for every retry, the sentence is said once per joining.
  expect(await timesSaid(page, RETRY)).toBe(1);
  // Forgetting a token in the page does not withdraw it: the three attempts of one click use one.
  expect(tokens).toHaveLength(1);
  // No countdown and no clock on the page.
  await expect(state(page, 'connecting').getByText(/\b\d+ ?(s|sec|seconds)\b/)).toHaveCount(0);
});

test('a registration that never answers is given up on and tried again', async ({ page }) => {
  test.setTimeout(90_000);
  await stubToken(page, granted());
  await open(page, { hang: 1, delay: 3000 });
  const seconds = Number(await page.locator('[data-call]').getAttribute('data-call-seconds'));
  expect(seconds, NO_STAND_IN).toBeLessThanOrEqual(15);
  await join(page);
  await expect(heading(page, CONNECTING)).toBeVisible();
  await expect(page.getByText('Attempt 2 of 3')).toBeVisible({ timeout: (seconds + 10) * 1000 });
  await expect(heading(page, IN_CALL)).toBeVisible({ timeout: 20_000 });
});

test('a token request that never answers is given up on with the attempt and asked again', async ({ page }) => {
  test.setTimeout(90_000);
  const tokens = await stubToken(page, { status: 200, hang: true }, granted());
  await open(page);
  const seconds = Number(await page.locator('[data-call]').getAttribute('data-call-seconds'));
  expect(seconds, NO_STAND_IN).toBeLessThanOrEqual(15);
  await join(page);
  await expect(heading(page, IN_CALL)).toBeVisible({ timeout: (seconds + 20) * 1000 });
  expect(tokens).toHaveLength(2);
  expect((await sdk(page)).inits).toHaveLength(1);
});

test('a failed attempt leaves the meeting and unregisters before the next one joins', async ({ page }) => {
  await stubToken(page, granted());
  await open(page, { mediaFailures: 1 });
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();

  const { calls } = await sdk(page);
  const first = calls.indexOf('join');
  const second = calls.indexOf('init', calls.indexOf('addMedia'));
  expect(calls.indexOf('addMedia')).toBeGreaterThan(first);
  // Between the refused media and the second initialisation: left, then unregistered.
  const between = calls.slice(calls.indexOf('addMedia') + 1, second);
  expect(between).toEqual(['leave', 'unregister']);
});

test('a registration that answers after its attempt was given up is unregistered when it arrives', async ({ page }) => {
  test.setTimeout(90_000);
  await stubToken(page, granted());
  await open(page, { late: 12_000 });
  const seconds = Number(await page.locator('[data-call]').getAttribute('data-call-seconds'));
  expect(seconds, NO_STAND_IN).toBeLessThanOrEqual(10);
  await join(page);
  await expect(heading(page, IN_CALL)).toBeVisible({ timeout: 40_000 });
  // The first registration answers at 12 s, after its attempt ended at 10 s: it is undone.
  await expect
    .poll(async () => (await sdk(page)).calls.filter((c) => c === 'unregister').length, { timeout: 30_000 })
    .toBeGreaterThanOrEqual(1);
});

test('after the last attempt the failure state says so, without the other way, and trying again works', async ({ page }) => {
  const tokens = await stubToken(page, granted());
  await open(page, { always: true });
  await join(page);

  await expect(heading(page, FAILED)).toBeFocused({ timeout: 30_000 });
  await expect(page.getByText('We tried 3 times').first()).toBeVisible();
  await saidLike(page, /We tried 3 times/);
  expect((await sdk(page)).calls.filter((c) => c === 'register')).toHaveLength(3);
  // The option is `hidden` here: no way into the platform's own app, and no fourth attempt.
  await expect(page.getByRole('link', { name: 'Join this call another way' })).toHaveCount(0);
  await expect(page.getByText(/Attempt 4/)).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
  await expectNoAxeViolations(page);

  // The failure state forgot the token: "try again" asks for a new one.
  const before = tokens.length;
  await page.evaluate(() => ((window as unknown as { __sdkPlan: Plan }).__sdkPlan.always = false));
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(heading(page, IN_CALL)).toBeFocused();
  expect(tokens).toHaveLength(before + 1);
});

/** The server renders the link out only when the organisation allows it; the page's own rule is what these tests need. */
async function withOtherWay(page: Page): Promise<void> {
  await page.addInitScript(() => {
    document.addEventListener('readystatechange', () => {
      if (document.readyState !== 'interactive') return;
      const place = document.querySelector('[data-call-state="failed"] .actions');
      if (!place || place.querySelector('[data-call-failed-other-way]')) return;
      const wrap = document.createElement('span');
      wrap.setAttribute('data-call-failed-other-way', '');
      const link = document.createElement('a');
      link.href = '/b/m/example/join/go';
      link.textContent = 'Join this call another way';
      wrap.append(link);
      place.append(wrap);
    });
  });
}

test('the other way is offered when the page cannot connect, and not for a call that is not open', async ({ page }) => {
  await withOtherWay(page);
  await stubToken(page, granted());
  await open(page, { always: true });
  await join(page);
  await expect(heading(page, FAILED)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('link', { name: 'Join this call another way' })).toBeVisible();
  await expect(page.locator('[data-call-failed-contacts]')).toBeHidden();

  // A call that is not open would not open through the link either, and cannot be tried again:
  // the organisation's contact details, or the one plain sentence when it has none (the example
  // organisation has none).
  const closed = await page.context().newPage();
  await withOtherWay(closed);
  await stubToken(closed, { status: 409, body: { error: 'not_open' } });
  await open(closed);
  await join(closed);
  await expect(heading(closed, FAILED)).toBeVisible();
  await expect(closed.getByRole('link', { name: 'Join this call another way' })).toBeHidden();
  await expect(closed.getByRole('button', { name: 'Try again' })).toBeHidden();
  await expect(closed.locator('[data-call-failed-contacts]')).toContainText('Get in touch with the organisation.');
});

for (const [status, body, sentence, retry] of [
  [409, { error: 'not_open' }, 'The call is not open yet, or it is no longer open.', false],
  [429, { error: 'rate_limited' }, 'You tried too many times in a short while.', true],
  [503, { error: 'unavailable' }, 'The call is not available right now.', true],
  [500, undefined, 'The video call could not be set up. You can try again.', true],
] as const) {
  test(`the token endpoint answering ${status} ends in its own sentence, and the SDK is never started`, async ({ page }) => {
    const tokens = await stubToken(page, { status, body });
    await open(page);
    await join(page);

    await expect(heading(page, FAILED)).toBeFocused();
    await expect(state(page, 'failed').getByText(sentence)).toBeVisible();
    await saidLike(page, new RegExp(`^${sentence.replace(/[.]/g, '\\.')}`));
    expect(tokens).toHaveLength(1);
    // The file was loaded (it comes first), but nothing of the video platform was started or asked.
    expect((await sdk(page)).calls).toEqual([]);
    // Trying again is offered where it can work: not for a call that is not open.
    await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible({ visible: retry });
    await expectNoAxeViolations(page);
  });
}

test('a token request that does not arrive is retried like any failed step, and spends the attempts', async ({ page }) => {
  const tokens = await stubToken(page, { status: 0, abort: true });
  await open(page);
  await join(page);
  await expect(heading(page, FAILED)).toBeFocused({ timeout: 30_000 });
  await expect(state(page, 'failed').getByText('We tried 3 times').first()).toBeVisible();
  expect(tokens).toHaveLength(3);
});

test('a token the platform refuses at registration is forgotten and a new one is asked for', async ({ page }) => {
  const tokens = await stubToken(page, granted());
  await open(page, { authFailures: 1 });
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();
  expect(tokens).toHaveLength(2);
  expect((await sdk(page)).inits).toHaveLength(2);
});

for (const [event, restored] of [['pagehide', false], ['pageshow', true]] as const) {
  test(`after a failed attempt, a ${event} during the wait starts nothing again`, async ({ page }) => {
    const tokens = await stubToken(page, granted());
    await open(page, { mediaFailures: 1, slowLeave: 1500 });
    await join(page);
    // The first attempt failed after joining: it is being undone (the leaving takes a moment).
    await expect.poll(async () => (await sdk(page)).calls.includes('leave')).toBe(true);
    await page.evaluate(
      (name) =>
        window.dispatchEvent(
          name === 'pageshow' ? new PageTransitionEvent('pageshow', { persisted: true }) : new Event('pagehide'),
        ),
      event,
    );
    // The undoing finishes (unregistered); then nothing may start: no second registration, no token.
    await expect.poll(async () => (await sdk(page)).calls.includes('unregister'), { timeout: 15_000 }).toBe(true);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const { calls } = await sdk(page);
    expect(calls.filter((c) => c === 'init')).toHaveLength(1);
    expect(calls.filter((c) => c === 'register')).toHaveLength(1);
    expect(tokens).toHaveLength(1);
    if (restored) await expect(heading(page, 'Your video call')).toBeVisible();
  });
}

test('the real endpoint answers an appointment outside the join window as not open', async ({ page }) => {
  await open(page);
  await join(page);
  await expect(heading(page, FAILED)).toBeFocused();
  await expect(state(page, 'failed').getByText('The call is not open yet, or it is no longer open.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeHidden();
});

test('the lobby: waiting words, own picture, working toggles that give the device back, a way to leave, and admission', async ({ page }) => {
  await stubToken(page, granted());
  await open(page, { lobby: true });
  await join(page);

  await expect(heading(page, WAITING)).toBeFocused();
  await expect(page.getByText('There is nothing you need to do. The call starts by itself.')).toBeVisible();
  await saidLike(page, /^Waiting for the .+ to let you in\.$/);
  await expect(state(page, 'lobby').getByLabel('Your picture')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Leave the call' })).toBeVisible();
  // Not in the call yet: no media was added, the other side is not shown.
  expect((await sdk(page)).calls).not.toContain('addMedia');
  await expect(state(page, 'call')).toBeHidden();
  expect(await live(page)).toEqual([1, 1]);

  const camera = page.getByRole('button', { name: /^Camera/ });
  await camera.click();
  await expect(camera).toHaveAttribute('aria-pressed', 'false');
  await expect(camera).toContainText('off');
  await said(page, 'Camera off.');
  await expect(state(page, 'lobby').getByText('Your camera is off')).toBeVisible();
  expect(await live(page)).toEqual([0, 1]); // the camera is given back, the light goes out
  await camera.click();
  await expect(camera).toHaveAttribute('aria-pressed', 'true');
  await said(page, 'Camera on.');
  expect(await live(page)).toEqual([1, 1]);
  await expectNoAxeViolations(page);

  await control(page, 'admit');
  await expect(heading(page, IN_CALL)).toBeFocused();
  await saidLike(page, /let you in\. The call has started\.$/);
  expect((await sdk(page)).calls.at(-1)).toBe('addMedia');
});

test('in the call: the other side, the own picture, the toggles that give the device back, leaving', async ({ page }) => {
  await stubToken(page, granted());
  await open(page);
  await join(page);

  await expect(heading(page, IN_CALL)).toBeFocused();
  await saidLike(page, /let you in\. The call has started\.$/);
  const remote = page.locator('[data-call-remote]');
  await expect(remote).toBeVisible();
  await expect(page.getByLabel(/^Picture of the /)).toBeVisible();
  await expect.poll(() => remote.evaluate((v) => (v as HTMLVideoElement).srcObject !== null)).toBe(true);
  await expect.poll(() => page.locator('[data-call-remote-audio]').evaluate((a) => (a as HTMLAudioElement).srcObject !== null)).toBe(true);
  await expect(state(page, 'call').getByLabel('Your picture')).toBeVisible();
  // The way back is not a link on the page while the call runs: it would end the call.
  await expect(page.getByRole('link', { name: 'Back to your appointment' })).toBeHidden();

  // Off gives the device back (taken out of the meeting and stopped); on takes a new one and sends it.
  expect(await live(page)).toEqual([1, 1]);
  const microphone = page.getByRole('button', { name: /^Microphone/ });
  await microphone.click();
  await expect(microphone).toHaveAttribute('aria-pressed', 'false');
  await said(page, 'Microphone off.');
  expect(await live(page)).toEqual([1, 0]);
  expect((await sdk(page)).unpublished).toEqual([1]);
  await microphone.click();
  await said(page, 'Microphone on.');
  expect(await live(page)).toEqual([1, 1]);
  expect((await sdk(page)).published).toEqual(['microphone']);
  await expectNoAxeViolations(page);

  await page.getByRole('button', { name: 'Leave the call' }).click();
  await expect(heading(page, 'You left the call')).toBeFocused();
  await saidLike(page, /^You can close this window, or go back to your appointment\.$/);
  await expect(page.getByRole('link', { name: 'Back to your appointment' }).first()).toBeVisible();
  const log = await sdk(page);
  expect(log.left).toBe(true);
  expect(log.calls.slice(-2)).toEqual(['leave', 'unregister']);
  expect(await live(page)).toEqual([0, 0]);
  await expectNoAxeViolations(page);
});

test('the other side switching its camera off and on is announced once each, and shown', async ({ page }) => {
  await stubToken(page, granted());
  await open(page);
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();
  await expect(state(page, 'call').locator('[data-call-remote-off]')).toBeHidden();

  await remoteCamera(page, false);
  await expect(state(page, 'call').locator('[data-call-remote-off]')).toBeVisible();
  await saidLike(page, /^The .+ turned the camera off\.$/);
  await remoteCamera(page, true);
  await expect(state(page, 'call').locator('[data-call-remote-off]')).toBeHidden();
  await saidLike(page, /^The .+ turned the camera on\.$/);
  const said_ = await statuses(page);
  expect(said_.filter((s) => /turned the camera off/.test(s))).toHaveLength(1);
  expect(said_.filter((s) => /turned the camera on/.test(s))).toHaveLength(1);
});

test('a browser that blocks the sound gets a button and one message, and the button plays', async ({ page }) => {
  await stubRemoteSound(page);
  await stubToken(page, granted());
  await open(page);
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();

  const hear = page.getByRole('button', { name: 'Tap to hear the other person' });
  await expect(hear).toBeVisible();
  await saidLike(page, /blocked the sound/);
  expect(await timesSaid(page, 'Your browser blocked the sound. Use the button to hear the other person.')).toBe(1);
  await page.evaluate(() => ((window as unknown as { __blockPlay: boolean }).__blockPlay = false));
  await hear.click();
  await expect(hear).toBeHidden();
  // The button went: focus goes to the call's heading, not nowhere, and the message is not said again.
  await expect(heading(page, IN_CALL)).toBeFocused();
  expect(await timesSaid(page, 'Your browser blocked the sound. Use the button to hear the other person.')).toBe(1);
  await expectNoAxeViolations(page);
});

test('a press while an earlier try never settles plays at once, and the button goes when that one succeeds', async ({ page }) => {
  await stubRemoteSound(page);
  await stubToken(page, granted());
  await open(page);
  await join(page);
  const hear = page.getByRole('button', { name: 'Tap to hear the other person' });
  await expect(hear).toBeVisible();

  // A new try that never settles (a browser that keeps the promise), then the browser lets it play.
  await page.evaluate(() => ((window as unknown as { __hangPlay: boolean }).__hangPlay = true));
  const before = await plays(page);
  await control(page, 'remoteAgain');
  await expect.poll(() => plays(page)).toBe(before + 1);
  await page.evaluate(() => {
    const w = window as unknown as { __hangPlay: boolean; __blockPlay: boolean };
    w.__hangPlay = false;
    w.__blockPlay = false;
  });
  // The press calls play() inside the press itself, whatever is still pending, and its outcome counts.
  await hear.click();
  await expect.poll(() => plays(page)).toBe(before + 2);
  await expect(hear).toBeHidden();
  await expect(heading(page, IN_CALL)).toBeFocused();
});

test('when the sound starts on its own, the button goes and focus stays where it was', async ({ page }) => {
  await stubRemoteSound(page);
  await stubToken(page, granted());
  await open(page);
  await join(page);
  const hear = page.getByRole('button', { name: 'Tap to hear the other person' });
  await expect(hear).toBeVisible();
  const microphone = page.getByRole('button', { name: /^Microphone/ });
  await microphone.focus();
  await page.evaluate(() => ((window as unknown as { __blockPlay: boolean }).__blockPlay = false));
  await control(page, 'remoteAgain'); // a new try is made, and the browser lets it play
  await expect(hear).toBeHidden();
  await expect(microphone).toBeFocused();
});

test('a new stream that cannot be sent is stopped again, and the toggle and the page say it did not work', async ({ page }) => {
  await stubToken(page, granted());
  await open(page, { publishFails: true });
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();
  const microphone = page.getByRole('button', { name: /^Microphone/ });
  await microphone.click();
  await expect(microphone).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(() => live(page)).toEqual([1, 0]);
  await microphone.click();
  await saidLike(page, /^That did not work\./);
  // Not sent, so not on: the device is closed again and the toggle still reads off.
  await expect(microphone).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(() => live(page)).toEqual([1, 0]);
  // The next press tries again instead of showing "on" for a stream nobody receives.
  await microphone.click();
  await expect.poll(async () => (await sdk(page)).calls.filter((c) => c === 'publishStreams').length).toBe(2);
  await expect(microphone).toHaveAttribute('aria-pressed', 'false');
});

test('leaving while a device is being taken out of the meeting announces nothing and moves nothing', async ({ page }) => {
  await stubToken(page, granted());
  await open(page, { slowUnpublish: 2000 });
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();
  await page.getByRole('button', { name: /^Microphone/ }).click();
  await page.getByRole('button', { name: 'Leave the call' }).click();
  await expect(heading(page, 'You left the call')).toBeFocused();
  // The unpublishing finishes after the call ended; it must not draw or say anything any more.
  await expect.poll(async () => (await sdk(page)).unpublished).toEqual([1]);
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(await timesSaid(page, 'Microphone off.')).toBe(0);
  await expect(heading(page, 'You left the call')).toBeFocused();
  await expect(state(page, 'left')).toBeVisible();
});

for (const step of ['create', 'join'] as const) {
  test(`a token refused when ${step === 'create' ? 'creating the meeting' : 'joining'} is forgotten like one refused at registration`, async ({ page }) => {
    const tokens = await stubToken(page, granted());
    await open(page, { authFailAt: step });
    await join(page);
    await expect(heading(page, IN_CALL)).toBeFocused();
    expect(tokens).toHaveLength(2);
  });
}

test('a device that cannot be taken out of the meeting is only muted, and the page says so', async ({ page }) => {
  await stubToken(page, granted());
  await open(page, { unpublishFails: true });
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();
  const microphone = page.getByRole('button', { name: /^Microphone/ });
  await microphone.click();
  await expect(microphone).toHaveAttribute('aria-pressed', 'false');
  await saidLike(page, /may still show the device as in use/);
  // Kept (muted), not stopped: the browser still holds it.
  expect(await live(page)).toEqual([1, 1]);
});

test('the way out stands apart from the switches', async ({ page }) => {
  await stubToken(page, granted());
  await open(page);
  await join(page);
  await expect(heading(page, IN_CALL)).toBeVisible();
  const toggles = await page.locator('[data-call-state="call"] .call-toggles').boundingBox();
  const leave = await page.getByRole('button', { name: 'Leave the call' }).boundingBox();
  expect(toggles).not.toBeNull();
  expect(leave).not.toBeNull();
  // Side by side on a wide window: at least a button's width of room between them.
  expect((leave?.x ?? 0) - ((toggles?.x ?? 0) + (toggles?.width ?? 0))).toBeGreaterThanOrEqual(48);
});

test('what the participant left off at the check stays off, and can be switched on in the call', async ({ page }) => {
  await stubToken(page, granted());
  await open(page);
  await begin(page).click();
  await said(page, READY);
  await page.getByRole('button', { name: /^Camera/ }).click();
  await page.getByLabel('Microphone', { exact: true }).selectOption('mic-2');
  await said(page, 'Microphone on.');
  await joinButton(page).click();

  await expect(heading(page, IN_CALL)).toBeFocused();
  let log = await sdk(page);
  // No camera was asked for, and the microphone is the chosen one.
  expect(log.devices).toEqual([{ kind: 'microphone', deviceId: 'mic-2' }]);
  expect(log.added).toEqual(['microphone']);
  const camera = page.getByRole('button', { name: /^Camera/ });
  await expect(camera).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('[data-call-self="call"]')).toBeHidden();

  await camera.click();
  await expect(camera).toHaveAttribute('aria-pressed', 'true');
  await said(page, 'Camera on.');
  await expect(page.locator('[data-call-self="call"]')).toBeVisible();
  log = await sdk(page);
  expect(log.devices.map((d) => d.kind)).toEqual(['microphone', 'camera']);
  expect(log.published).toEqual(['camera']);
});

test('the meeting ended by the staff member shows the plain ended state', async ({ page }) => {
  await stubToken(page, granted());
  await open(page);
  await join(page);
  await expect(heading(page, IN_CALL)).toBeVisible();
  await control(page, 'end');

  await expect(heading(page, 'The call has ended')).toBeFocused();
  await saidLike(page, /^Thank you for the call\. You can close this window, or go back to your appointment\.$/);
  await expect(page.getByRole('link', { name: 'Back to your appointment' }).first()).toBeVisible();
  expect(await live(page)).toEqual([0, 0]);
  await expectNoAxeViolations(page);
});

test('leaving the page in the lobby leaves the meeting and gives the devices back', async ({ page }) => {
  await stubToken(page, granted());
  await open(page, { lobby: true });
  await join(page);
  await expect(heading(page, WAITING)).toBeVisible();
  expect(await live(page)).toEqual([1, 1]);
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  await expect.poll(async () => (await sdk(page)).left).toBe(true);
  await expect.poll(() => live(page)).toEqual([0, 0]);
});

test('the keyboard alone leads from the start through the lobby and the call to leaving', async ({ page }) => {
  await stubToken(page, granted());
  await open(page, { lobby: true });
  await tabTo(page, begin(page));
  await page.keyboard.press('Enter');
  await said(page, READY);
  await tabTo(page, joinButton(page));
  await page.keyboard.press('Enter');

  await expect(heading(page, WAITING)).toBeFocused();
  await tabTo(page, page.getByRole('button', { name: /^Microphone/ }));
  await page.keyboard.press('Space');
  await expect(page.getByRole('button', { name: /^Microphone/ })).toHaveAttribute('aria-pressed', 'false');
  await control(page, 'admit');
  await expect(heading(page, IN_CALL)).toBeFocused();
  await tabTo(page, page.getByRole('button', { name: 'Leave the call' }));
  await page.keyboard.press('Enter');
  await expect(heading(page, 'You left the call')).toBeFocused();
});

test('without a whole SDK file the join button leads to the state that says the call is not available', async ({ page }) => {
  const asked: string[] = [];
  page.on('request', (request) => asked.push(request.url()));
  const tokens = await stubToken(page, granted());
  // The page as a server without the file would render it: no address, no checksum.
  await page.route(/\/call(\?|$)/, async (route) => {
    if (route.request().resourceType() !== 'document') return route.fallback();
    const response = await route.fetch();
    const body = (await response.text()).replace(/ data-call-sdk(-integrity)?="[^"]*"/g, '');
    return route.fulfill({ response, body });
  });
  await spy(page, { devices: 'made-up' });
  await openCallPage(page);
  await begin(page).click();
  await said(page, READY);
  await joinButton(page).click();

  await expect(heading(page, 'The call is not available in this page yet')).toBeFocused();
  expect(tokens).toHaveLength(0);
  expect(asked.filter((url) => /call\/token|vendor\/webex/.test(url))).toEqual([]);
  expect(await live(page)).toEqual([0, 0]);
  await expectNoAxeViolations(page);
});

test('at 320 px nothing scrolls sideways in connecting, the lobby, the call and the failure', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await stubToken(page, granted());
  await open(page, { lobby: true, delay: 1500 });
  const overflow = (): Promise<number> => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  await join(page);
  await expect(heading(page, CONNECTING)).toBeVisible();
  expect(await overflow()).toBeLessThanOrEqual(0);
  await expect(heading(page, WAITING)).toBeVisible();
  expect(await overflow()).toBeLessThanOrEqual(0);
  await control(page, 'admit');
  await expect(heading(page, IN_CALL)).toBeVisible();
  expect(await overflow()).toBeLessThanOrEqual(0);
  await expectNoAxeViolations(page);

  const failing = await page.context().newPage();
  await failing.setViewportSize({ width: 320, height: 800 });
  await stubToken(failing, granted());
  await open(failing, { always: true });
  await join(failing);
  await expect(heading(failing, FAILED)).toBeVisible({ timeout: 30_000 });
  expect(await failing.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
});

test('reduced motion stops the ring while connecting; forced colours keep it visible', async ({ browser, browserName }) => {
  test.skip(browserName !== 'chromium', 'forced colours are emulated in Chromium');
  const context = await browser.newContext({ locale: 'en-US', reducedMotion: 'reduce', forcedColors: 'active' });
  const page = await context.newPage();
  await stubToken(page, granted());
  await open(page, { delay: 3000 });
  await join(page);
  await expect(heading(page, CONNECTING)).toBeVisible();
  const ring = await page.locator('.call-spinner').evaluate((el) => {
    const style = getComputedStyle(el);
    return { animation: style.animationName, border: style.borderTopStyle, width: style.borderTopWidth };
  });
  expect(ring.animation).toBe('none');
  expect(ring.border).toBe('solid');
  expect(Number.parseFloat(ring.width)).toBeGreaterThan(0);
  await context.close();
});

test('every control of the new states is at least 24 by 24 pixels', async ({ page }) => {
  await stubToken(page, granted());
  await open(page, { lobby: true });
  await join(page);
  await expect(heading(page, WAITING)).toBeVisible();
  const sized = async (): Promise<void> => {
    for (const button of await page.getByRole('button').all()) {
      if (!(await button.isVisible())) continue;
      const box = await button.boundingBox();
      expect(box?.width).toBeGreaterThanOrEqual(24);
      expect(box?.height).toBeGreaterThanOrEqual(24);
    }
  };
  await sized();
  await control(page, 'admit');
  await expect(heading(page, IN_CALL)).toBeVisible();
  await sized();
});
