/**
 * Shared steps of the call page's browser specs (ADR 0123 steps 4b and 4c): opening the page
 * through its emailed link, and the made-up devices. `spy` stands in for getUserMedia and
 * enumerateDevices before the page's own script runs, in every browser, with two cameras, two
 * microphones and two speakers, a canvas picture and an oscillator sound; it also records every request for a
 * device, every stream handed out and every sentence the page's live region gets, and plays a
 * refusal of one kind or of both while the other stays healthy.
 */
import { expect, type Page } from '@playwright/test';

import { fixture } from './pages';

export type Plan = { devices: 'real' | 'made-up' | 'none'; refuse?: 'camera' | 'microphone' | 'both'; delay?: number };
export type Spied = { deviceRequests: number; streams: MediaStream[]; statuses: string[]; delay: number };

/** Open the call page in English through its emailed link (the cookie is set on the way). */
export async function openCallPage(page: Page): Promise<void> {
  await page.goto(`${fixture('call_page')}?lang=en`);
  await expect(page.getByRole('heading', { level: 1, name: 'Your video call' })).toBeVisible();
}

/**
 * Before the page's own script runs: record every request for a device, every stream it returns
 * and every sentence the live region gets, and answer requests as `plan` says.
 */
export async function spy(page: Page, plan: Plan = { devices: 'real' }): Promise<void> {
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
      { deviceId: 'spk-2', label: 'Speaker two', kind: 'audiooutput', groupId: 'g' },
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

export const requests = (page: Page): Promise<number> => page.evaluate(() => (window as unknown as { spied: Spied }).spied.deviceRequests);
export const statuses = (page: Page): Promise<string[]> => page.evaluate(() => (window as unknown as { spied: Spied }).spied.statuses);
/** How many of the tracks the page was given are still live: [video, audio]. */
export const live = (page: Page): Promise<[number, number]> =>
  page.evaluate(() => {
    const tracks = (window as unknown as { spied: Spied }).spied.streams.flatMap((s) => s.getTracks());
    const open = (kind: string): number => tracks.filter((t) => t.kind === kind && t.readyState === 'live').length;
    return [open('video'), open('audio')];
  });
export const level = (page: Page): Promise<number> => page.locator('[data-call-level]').evaluate((el) => (el as HTMLMeterElement).value);

/** The live region has said `text` (it keeps no history of its own, so the spy does). The queue gives each sentence its turn. */
export const said = (page: Page, text: string): Promise<void> =>
  expect.poll(() => statuses(page), { timeout: 20_000 }).toContain(text);

export const begin = (page: Page) => page.getByRole('button', { name: 'Test camera and microphone' });
export const checkHeading = (page: Page) => page.getByRole('heading', { level: 1, name: 'Check picture and sound' });
export const READY = 'Your camera and microphone are ready.';

/** The list of cameras or microphones of the device check (the call's panel has lists of its own). */
export const checkList = (page: Page, label: 'Camera' | 'Microphone') =>
  page.locator('[data-call-state="check"]').getByLabel(label, { exact: true });
