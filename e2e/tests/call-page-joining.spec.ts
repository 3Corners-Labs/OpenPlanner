/**
 * The call page from the click that joins (ADR 0123 step 4c), walked in a browser: the token asked
 * only on the click, the SDK loaded with its checksum, connecting with retries, the lobby, the first
 * moment of the call, leaving, the meeting's end, the failure state after the last attempt and each
 * refusal of the token endpoint, the keyboard alone, what is announced and where focus goes, and
 * that the token never reaches the page, an address or a storage.
 *
 * The stack places a test double of the video SDK over the vendored file (e2e/vendor/webex/, see
 * .github/actions/e2e-stack); what it does is chosen per test with `window.__sdkPlan` before the
 * page loads and read back from `window.__sdk`. A stack without it offers no SDK at all and these
 * tests skip themselves. The token endpoint is answered by the tests (the example booking is two
 * hours away, outside the join window), except in the one test that asks the real endpoint.
 *
 * The devices are the made-up ones of `../call-page` ("made-up": two cameras, two microphones, a
 * canvas picture and an oscillator sound, in every browser).
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

import { expect, test, type Page, type Request } from '@playwright/test';

import { begin, live, openCallPage, READY, said, spy, statuses } from '../call-page';
import { expectNoAxeViolations, tabTo } from '../helpers';

test.use({ locale: 'en-US' });

const TOKEN = 'guest-token-0123456789abcdef';
const LINK = 'https://meeting.example.test/guest/abcdef';
const APP = new URL(process.env.BASE_URL ?? 'http://127.0.0.1:8000').origin;

type Plan = { failures?: number; hang?: number; always?: boolean; delay?: number; lobby?: boolean; remote?: boolean };
type Log = {
  calls: string[];
  inits: { token: string; autoUploadLogs: boolean; ephemeral: boolean }[];
  devices: { kind: string; deviceId: string }[];
  published: string[];
  added?: string[];
  link?: string;
  left: boolean;
};
type Answer = { status: number; body?: unknown; abort?: boolean };

const granted = (): Answer => ({
  status: 200,
  body: { token: TOKEN, expires_at: new Date(Date.now() + 6 * 3600 * 1000).toISOString(), link: LINK },
});

/** Answer the token endpoint with `answers` in turn (the last one repeats); returns the requests it saw. */
async function stubToken(page: Page, ...answers: Answer[]): Promise<Request[]> {
  const seen: Request[] = [];
  await page.route('**/call/token', async (route) => {
    const answer = answers[Math.min(seen.length, answers.length - 1)];
    seen.push(route.request());
    if (answer.abort) return route.abort();
    return route.fulfill({
      status: answer.status,
      contentType: 'application/json',
      headers: { 'cache-control': 'no-store' },
      body: JSON.stringify(answer.body ?? { error: 'refused' }),
    });
  });
  return seen;
}

/** The stand-in's own log: what the page asked of the SDK, in order. */
const sdk = (page: Page): Promise<Log> =>
  page.evaluate(() => {
    const s = (window as unknown as { __sdk: Log }).__sdk;
    return { calls: s.calls, inits: s.inits, devices: s.devices, published: s.published, added: s.added, link: s.link, left: s.left };
  });
const control = (page: Page, action: 'admit' | 'end'): Promise<void> =>
  page.evaluate((what) => (window as unknown as { __sdk: Record<string, () => void> }).__sdk[what](), action);

/** Open the call page with the made-up devices and the stand-in's plan; skip where the stack offers no SDK. */
async function open(page: Page, plan: Plan = {}): Promise<void> {
  await spy(page, { devices: 'made-up' });
  await page.addInitScript((p) => ((window as unknown as { __sdkPlan: Plan }).__sdkPlan = p), plan);
  await openCallPage(page);
  const offered = await page.locator('[data-call]').getAttribute('data-call-sdk');
  test.skip(offered === null, 'this stack has no SDK file: the stand-in of e2e/vendor/webex is not placed');
}

const joinButton = (page: Page) => page.getByRole('button', { name: 'Join', exact: true });
const heading = (page: Page, name: string | RegExp) => page.getByRole('heading', { level: 1, name });
const CONNECTING = 'We are connecting you';
const WAITING = /You are here\. The .+ will let you in shortly/;
const IN_CALL = 'You are in the call';
const FAILED = 'We cannot connect';

/** Through the check to the click that joins. */
async function join(page: Page): Promise<void> {
  await begin(page).click();
  await said(page, READY);
  await joinButton(page).click();
}

/** The sentences the live region got, in order, that match `re`: a sentence is read out when it is in this list. */
const saidLike = (page: Page, re: RegExp): Promise<void> =>
  expect.poll(async () => (await statuses(page)).some((s) => re.test(s)), { timeout: 20_000, message: String(re) }).toBe(true);

test('before the click nothing leaves the origin; the click asks for the token once and then loads the file', async ({ page }) => {
  const asked: string[] = [];
  page.on('request', (request) => asked.push(`${request.method()} ${request.url()}`));
  const tokens = await stubToken(page, granted());
  await open(page);
  await begin(page).click();
  await said(page, READY);
  await page.waitForTimeout(500);

  // The page, the check, a test sound: no token, no SDK file, nobody else.
  await page.getByRole('button', { name: 'Play a test sound' }).click();
  expect(tokens).toHaveLength(0);
  expect(asked.filter((line) => /call\/token|vendor\/webex/.test(line))).toEqual([]);
  expect(asked.map((line) => new URL(line.split(' ')[1]).origin).filter((o) => !['null', APP].includes(o))).toEqual([]);

  await joinButton(page).click();
  await expect(heading(page, IN_CALL)).toBeVisible();
  expect(tokens).toHaveLength(1);
  expect(tokens[0].method()).toBe('POST');
  expect(tokens[0].postData()).toBeNull();
  expect(asked.filter((line) => /vendor\/webex/.test(line))).toHaveLength(1);
  // Everything the page and the SDK stand-in asked stays on the page's own origin.
  expect(asked.map((line) => new URL(line.split(' ')[1]).origin).filter((o) => !['null', APP].includes(o))).toEqual([]);
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

test('connecting is visible and says so when it tries again by itself; the token is reused', async ({ page }) => {
  const tokens = await stubToken(page, granted());
  await open(page, { failures: 1, delay: 1500 });
  await join(page);

  await expect(heading(page, CONNECTING)).toBeVisible();
  await saidLike(page, /^Setting up your call\.$/);
  // The first attempt fails at once; the second takes a moment, and the page says what happens.
  await expect(page.getByText('The last attempt did not work. We are trying again. This happens by itself.').first()).toBeVisible();
  await expect(page.getByText('Attempt 2 of 3')).toBeVisible();
  await saidLike(page, /^The last attempt did not work\. We are trying again/);

  await expect(heading(page, IN_CALL)).toBeFocused();
  const log = await sdk(page);
  expect(log.calls.filter((c) => c === 'register')).toHaveLength(2);
  expect(log.calls.filter((c) => c === 'init')).toHaveLength(2);
  expect(tokens).toHaveLength(1);
  // No countdown and no clock on the page.
  await expect(page.getByText(/\b\d+ ?(s|sec|seconds)\b/)).toHaveCount(0);
});

test('a registration that never answers is given up on and tried again', async ({ page }) => {
  test.setTimeout(90_000);
  await stubToken(page, granted());
  await open(page, { hang: 1 });
  const seconds = Number(await page.locator('[data-call]').getAttribute('data-call-seconds'));
  test.skip(seconds > 15, 'this stack waits longer than 15 seconds for one attempt (EMBEDDED_CALL_CONNECT_SECONDS)');
  await join(page);
  await expect(heading(page, CONNECTING)).toBeVisible();
  await expect(page.getByText('Attempt 2 of 3')).toBeVisible({ timeout: (seconds + 10) * 1000 });
  await expect(heading(page, IN_CALL)).toBeVisible({ timeout: 20_000 });
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

  await page.evaluate(() => ((window as unknown as { __sdkPlan: Plan }).__sdkPlan.always = false));
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(heading(page, IN_CALL)).toBeFocused();
  expect(tokens).toHaveLength(1);
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

  // A call that is not open would not open through the link either: contact details only.
  const closed = await page.context().newPage();
  await withOtherWay(closed);
  await stubToken(closed, { status: 409, body: { error: 'not_open' } });
  await open(closed);
  await join(closed);
  await expect(heading(closed, FAILED)).toBeVisible();
  await expect(closed.getByRole('link', { name: 'Join this call another way' })).toBeHidden();
  await expect(closed.locator('[data-call-failed-contacts]')).toBeVisible();
});

for (const [status, body, sentence] of [
  [409, { error: 'not_open' }, 'The call is not open yet, or it is no longer open.'],
  [429, { error: 'rate_limited' }, 'You tried too many times in a short while.'],
  [503, { error: 'unavailable' }, 'The call is not available right now.'],
  [500, undefined, 'The video call could not be set up. You can try again.'],
] as const) {
  test(`the token endpoint answering ${status} ends in its own sentence, before the SDK is loaded`, async ({ page }) => {
    const asked: string[] = [];
    page.on('request', (request) => asked.push(request.url()));
    const tokens = await stubToken(page, { status, body });
    await open(page);
    await join(page);

    await expect(heading(page, FAILED)).toBeFocused();
    await expect(page.getByText(sentence).first()).toBeVisible();
    await saidLike(page, new RegExp(`^${sentence.replace(/[.]/g, '\\.')}`));
    expect(tokens).toHaveLength(1);
    // Nothing of the video platform was asked for, and no SDK file was fetched.
    expect(asked.filter((url) => /vendor\/webex/.test(url))).toEqual([]);
    expect((await page.evaluate(() => typeof (window as unknown as { __sdk?: unknown }).__sdk))).toBe('undefined');
    await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
    await expectNoAxeViolations(page);
  });
}

test('a token request that does not arrive ends in the plain failure sentence', async ({ page }) => {
  await stubToken(page, { status: 0, abort: true });
  await open(page);
  await join(page);
  await expect(heading(page, FAILED)).toBeFocused();
  await expect(page.getByText('The video call could not be set up. You can try again.').first()).toBeVisible();
});

test('the real endpoint answers an appointment outside the join window as not open', async ({ page }) => {
  await open(page);
  await join(page);
  await expect(heading(page, FAILED)).toBeFocused();
  await expect(page.getByText('The call is not open yet, or it is no longer open.').first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('the lobby: waiting words, own picture, working toggles, a way to leave, and admission', async ({ page }) => {
  await stubToken(page, granted());
  await open(page, { lobby: true });
  await join(page);

  await expect(heading(page, WAITING)).toBeFocused();
  await expect(page.getByText('There is nothing you need to do. The call starts by itself.')).toBeVisible();
  await saidLike(page, /^Waiting for the .+ to let you in\.$/);
  await expect(page.locator('[data-call-self="lobby"]')).toBeVisible();
  await expect(page.getByLabel('Your own picture')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Leave the call' })).toBeVisible();
  // Not in the call yet: no media was added, the other side is not shown.
  expect((await sdk(page)).calls).not.toContain('addMedia');
  await expect(page.locator('[data-call-state="call"]')).toBeHidden();

  const camera = page.getByRole('button', { name: /^Camera/ });
  await camera.click();
  await expect(camera).toHaveAttribute('aria-pressed', 'false');
  await expect(camera).toContainText('off');
  await said(page, 'Camera off.');
  await expect(page.locator('[data-call-state="lobby"]').getByText('Your camera is off')).toBeVisible();
  await camera.click();
  await expect(camera).toHaveAttribute('aria-pressed', 'true');
  await said(page, 'Camera on.');
  await expectNoAxeViolations(page);

  await control(page, 'admit');
  await expect(heading(page, IN_CALL)).toBeFocused();
  await saidLike(page, /let you in\. The call has started\.$/);
  expect((await sdk(page)).calls.at(-1)).toBe('addMedia');
});

test('in the call: the other side, the own picture, the toggles, leaving', async ({ page }) => {
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
  await expect(page.locator('[data-call-self="call"]')).toBeVisible();
  // The way back is not a link on the page while the call runs: it would end the call.
  await expect(page.getByRole('link', { name: 'Back to your appointment' })).toBeHidden();

  const microphone = page.getByRole('button', { name: /^Microphone/ });
  await microphone.click();
  await expect(microphone).toHaveAttribute('aria-pressed', 'false');
  await said(page, 'Microphone off.');
  await microphone.click();
  await said(page, 'Microphone on.');
  await expectNoAxeViolations(page);

  expect(await live(page)).toEqual([1, 1]);
  await page.getByRole('button', { name: 'Leave the call' }).click();
  await expect(heading(page, 'You left the call')).toBeFocused();
  await saidLike(page, /^You have left the call\.$/);
  await expect(page.getByRole('link', { name: 'Back to your appointment' }).first()).toBeVisible();
  const log = await sdk(page);
  expect(log.left).toBe(true);
  expect(log.calls.slice(-2)).toEqual(['leave', 'unregister']);
  expect(await live(page)).toEqual([0, 0]);
  await expectNoAxeViolations(page);
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
  await saidLike(page, /^The call is over\.$/);
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
  expect((await sdk(page)).left).toBe(true);
  expect(await live(page)).toEqual([0, 0]);
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
