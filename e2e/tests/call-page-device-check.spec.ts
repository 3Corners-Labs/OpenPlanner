/**
 * The call page before the click that joins (ADR 0123 step 4b), walked in a browser: the start
 * state, the device check, device choice, every refused permission, no device found, the keyboard
 * alone, what is announced and where focus goes, and that leaving the page or switching a device
 * off gives the tracks back.
 *
 * The page is the one of `fixtures.json` (`call_page`: the emailed link of a confirmed booking,
 * which sets the participant's cookie and redirects, like the suspended booking's link) on a
 * stack that runs with EMBEDDED_CALL_ENABLED (.github/actions/e2e-stack). Nothing here reaches
 * the video platform: the page has no SDK file yet, and the join button leads to a state that
 * says the call is not available in this page yet.
 *
 * Two kinds of device. "real": Chromium's own fake camera and microphone (the project's launch
 * flags in playwright.config.ts), for the tests that need the real path, such as the meter.
 * "made-up": a stand-in for getUserMedia and enumerateDevices that hands out a canvas picture and
 * an oscillator sound with two cameras and two microphones, in every browser; it also plays a
 * refusal of one kind or of both while the other kind stays healthy. The island gates (axe, 320
 * px, forced colours, scripts off, CSP) run on the start state through pages.json; axe also runs
 * here in the check and the refused states.
 */
import { expect, test, type Page } from '@playwright/test';

import { expectNoAxeViolations, tabTo } from '../helpers';
import { fixture } from '../pages';

test.use({ locale: 'en-US' });

type Plan = { devices: 'real' | 'made-up' | 'none'; refuse?: 'camera' | 'microphone' | 'both'; delay?: number };
type Spied = { deviceRequests: number; streams: MediaStream[]; statuses: string[]; delay: number };

/** Open the call page in English through its emailed link (the cookie is set on the way). */
async function openCallPage(page: Page): Promise<void> {
  await page.goto(`${fixture('call_page')}?lang=en`);
  await expect(page.getByRole('heading', { level: 1, name: 'Your video call' })).toBeVisible();
}

/**
 * Before the page's own script runs: record every request for a device, every stream it returns
 * and every sentence the live region gets, and answer requests as `plan` says.
 */
async function spy(page: Page, plan: Plan = { devices: 'real' }): Promise<void> {
  await page.addInitScript((what) => {
    const w = window as unknown as { spied: Spied };
    w.spied = { deviceRequests: 0, streams: [], statuses: [], delay: what.delay ?? 0 };
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    const listed = [
      { deviceId: 'cam-1', label: 'Camera one', kind: 'videoinput', groupId: 'g' },
      { deviceId: 'cam-2', label: 'Camera two', kind: 'videoinput', groupId: 'g' },
      { deviceId: 'mic-1', label: 'Microphone one', kind: 'audioinput', groupId: 'g' },
      { deviceId: 'mic-2', label: 'Microphone two', kind: 'audioinput', groupId: 'g' },
      { deviceId: 'spk-1', label: 'Speaker one', kind: 'audiooutput', groupId: 'g' },
    ];
    if (what.devices === 'made-up') {
      navigator.mediaDevices.enumerateDevices = async () => listed as unknown as MediaDeviceInfo[];
    }
    const madeUp = (kind: 'video' | 'audio', id: string): MediaStreamTrack => {
      let track: MediaStreamTrack;
      if (kind === 'video') {
        const canvas = document.createElement('canvas');
        canvas.width = 320;
        canvas.height = 180;
        const context = canvas.getContext('2d') as CanvasRenderingContext2D;
        let n = 0;
        setInterval(() => {
          context.fillStyle = `hsl(${(n += 10) % 360} 60% 40%)`;
          context.fillRect(0, 0, 320, 180);
        }, 100);
        track = canvas.captureStream(10).getVideoTracks()[0];
      } else {
        const audio = new AudioContext();
        const oscillator = audio.createOscillator();
        const destination = audio.createMediaStreamDestination();
        oscillator.connect(destination);
        oscillator.start();
        void audio.resume();
        track = destination.stream.getAudioTracks()[0];
      }
      track.getSettings = () => ({ deviceId: id });
      return track;
    };
    const wanted = (value: unknown, fallback: string): string | null => {
      if (!value) return null;
      const exact = (value as { deviceId?: { exact?: string } }).deviceId?.exact;
      return exact ?? fallback;
    };
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      w.spied.deviceRequests += 1;
      const video = wanted(constraints?.video, 'cam-1');
      const audio = wanted(constraints?.audio, 'mic-1');
      if (what.devices === 'none') throw new DOMException('none', 'NotFoundError');
      const refused =
        (video && (what.refuse === 'camera' || what.refuse === 'both')) ||
        (audio && (what.refuse === 'microphone' || what.refuse === 'both'));
      if (refused) throw new DOMException('blocked', 'NotAllowedError');
      if (w.spied.delay) await new Promise((resolve) => setTimeout(resolve, w.spied.delay));
      let stream: MediaStream;
      if (what.devices === 'made-up') {
        const tracks = [video ? madeUp('video', video) : null, audio ? madeUp('audio', audio) : null];
        stream = new MediaStream(tracks.filter((t): t is MediaStreamTrack => t !== null));
      } else {
        stream = await original(constraints);
      }
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
const level = (page: Page): Promise<number> => page.locator('[data-call-level]').evaluate((el) => (el as HTMLMeterElement).value);

/** The live region has said `text` (it keeps no history of its own, so the spy does). The queue gives each sentence its turn. */
const said = (page: Page, text: string): Promise<void> =>
  expect.poll(() => statuses(page), { timeout: 20_000 }).toContain(text);

const begin = (page: Page) => page.getByRole('button', { name: 'Test camera and microphone' });
const checkHeading = (page: Page) => page.getByRole('heading', { level: 1, name: 'Check picture and sound' });
const READY = 'Your camera and microphone are ready.';

test('the start state shows the appointment and one button, and asks for no device on load', async ({ page }) => {
  await spy(page, { devices: 'made-up' });
  await openCallPage(page);
  await expect(begin(page)).toBeVisible();
  await expect(page.getByText('a browser with JavaScript, a camera and a microphone')).toBeHidden();
  await expect(page.locator('dl.details')).toContainText('Team');
  await page.waitForTimeout(500);
  expect(await requests(page)).toBe(0);
  await expectNoAxeViolations(page);
});

test('the device check with the real fake devices: picture, meter, test sound', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'the real fake devices are Chromium flags');
  await spy(page);
  await openCallPage(page);
  await begin(page).click();

  await expect(checkHeading(page)).toBeFocused();
  await expect(page.getByLabel('Your own picture')).toBeVisible();
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
  await expect(page.getByText('Your microphone is off, so we do not hear you.')).toBeVisible();
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
  await expect(page.getByText('Your camera is off')).toBeVisible();
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
  await slow.waitForTimeout(2000);
  expect(await live(slow)).toEqual([0, 0]);
});

test('the keyboard alone leads from the start to the state that says the call is not available yet', async ({ page }) => {
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
  await tabTo(page, page.getByRole('button', { name: 'Join', exact: true }));
  await page.keyboard.press('Enter');
  const unavailable = page.getByRole('heading', { level: 1, name: 'The call is not available in this page yet' });
  await expect(unavailable).toBeFocused();
  expect(await live(page)).toEqual([0, 0]);
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
  await begin(page).click();
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
