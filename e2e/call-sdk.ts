/**
 * What the call page's browser specs share (ADR 0123 steps 4c and 5a): the test double's plan and
 * log, the token endpoint's stub, the page opened with the made-up devices and a plan, the click
 * that joins, the names of the states and the sentences the live region has said.
 */
import { expect, type Page, type Request } from '@playwright/test';

import { begin, openCallPage, READY, said, spy, statuses } from './call-page';

export const TOKEN = 'guest-token-0123456789abcdef';
export const LINK = 'https://meeting.example.test/guest/abcdef';
export const APP = new URL(process.env.BASE_URL ?? 'http://127.0.0.1:8000').origin;
export const NO_STAND_IN =
  'this stack offers no SDK file, so the joining cannot be walked: the CI stack (.github/actions/e2e-stack) ' +
  'mounts e2e/vendor/webex/meetings.min.js and manifest.json read-only over ' +
  '/srv/app/web/static/vendor/webex/ of the app container and sets EMBEDDED_CALL_CONNECT_SECONDS=10; ' +
  'a local stack needs the same two -v options and that variable';

export type Plan = {
  failures?: number;
  hang?: number;
  always?: boolean;
  delay?: number;
  late?: number;
  authFailures?: number;
  slowLeave?: number;
  unpublishFails?: boolean;
  slowUnpublish?: number;
  hangUnpublish?: boolean;
  joinDelay?: number;
  publishFails?: boolean;
  authFailAt?: 'create' | 'join';
  mediaFailures?: number;
  lobby?: boolean;
  remote?: boolean;
  deviceFails?: string[];
  captions?: boolean;
  captionsFail?: boolean;
  canShare?: boolean;
  shareSendFails?: boolean;
  holdPublish?: boolean;
};
export type Log = {
  calls: string[];
  inits: { token: string; autoUploadLogs: boolean; ephemeral: boolean; automaticLLM: boolean }[];
  devices: { kind: string; deviceId: string }[];
  published: string[];
  unpublished: number[];
  added?: string[];
  link?: string;
  left: boolean;
};
export type Answer = { status: number; body?: unknown; abort?: boolean; hang?: boolean };

export const granted = (): Answer => ({
  status: 200,
  body: { token: TOKEN, expires_at: new Date(Date.now() + 6 * 3600 * 1000).toISOString(), link: LINK },
});

/** Answer the token endpoint with `answers` in turn (the last one repeats); returns the requests it saw. */
export async function stubToken(page: Page, ...answers: Answer[]): Promise<Request[]> {
  const seen: Request[] = [];
  await page.route('**/call/token', async (route) => {
    const answer = answers[Math.min(seen.length, answers.length - 1)];
    seen.push(route.request());
    if (answer.hang) return; // never answered: the page's own time limit ends it
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

/** The stand-in's own log: what the page asked of the SDK, in order. Empty while the SDK file has not loaded. */
export const sdk = (page: Page): Promise<Log> =>
  page.evaluate(() => {
    const s = (window as unknown as { __sdk?: Log }).__sdk;
    if (!s) return { calls: [], inits: [], devices: [], published: [], unpublished: [], left: false };
    return {
      calls: s.calls,
      inits: s.inits,
      devices: s.devices,
      published: s.published,
      unpublished: s.unpublished,
      added: s.added,
      link: s.link,
      left: s.left,
    };
  });
export const control = (page: Page, action: 'admit' | 'end' | 'remoteAgain'): Promise<void> =>
  page.evaluate((what) => (window as unknown as { __sdk: Record<string, () => void> }).__sdk[what](), action);
export const remoteCamera = (page: Page, on: boolean): Promise<void> =>
  page.evaluate((value) => (window as unknown as { __sdk: { remoteCamera: (v: boolean) => void } }).__sdk.remoteCamera(value), on);
/** The meeting's recording starts or stops, as the stand-in's meeting reports it. */
export const recording = (page: Page, on: boolean, payload?: unknown): Promise<void> =>
  page.evaluate(
    ([value, body]) => (window as unknown as { __sdk: { recording: (v: boolean, b: unknown) => void } }).__sdk.recording(value as boolean, body),
    [on, payload],
  );
/** The meeting's connection is being restored ("start"), is back ("success") or is lost for good ("fail"). */
export const reconnection = (page: Page, step: 'start' | 'success' | 'fail', payload?: unknown): Promise<void> =>
  page.evaluate(
    ([value, body]) => (window as unknown as { __sdk: { reconnection: (v: string, b: unknown) => void } }).__sdk.reconnection(value as string, body),
    [step, payload],
  );

/** Open the call page with the made-up devices and the stand-in's plan; fail where the stack offers no SDK. */
export async function open(page: Page, plan: Plan = {}): Promise<void> {
  await spy(page, { devices: 'made-up' });
  await page.addInitScript((p) => ((window as unknown as { __sdkPlan: Plan }).__sdkPlan = p), plan);
  await openCallPage(page);
  const offered = await page.locator('[data-call]').getAttribute('data-call-sdk');
  expect(offered, NO_STAND_IN).not.toBeNull();
}

export const joinButton = (page: Page) => page.getByRole('button', { name: 'Join', exact: true });
export const heading = (page: Page, name: string | RegExp) => page.getByRole('heading', { level: 1, name });
export const state = (page: Page, name: string) => page.locator(`[data-call-state="${name}"]`);
export const CONNECTING = 'We are connecting you';
export const WAITING = /You are here\. The .+ will let you in shortly/;
export const IN_CALL = 'You are in the call';
export const FAILED = 'We cannot connect';
export const RETRY = 'The last attempt did not work. We are trying again. This happens by itself.';

/** Through the check to the click that joins. */
export async function join(page: Page): Promise<void> {
  await begin(page).click();
  await said(page, READY);
  await joinButton(page).click();
}

/** The live region has said something that matches `re` (the page keeps no history, the spy does). */
export const saidLike = (page: Page, re: RegExp): Promise<void> =>
  expect.poll(async () => (await statuses(page)).some((s) => re.test(s)), { timeout: 20_000, message: String(re) }).toBe(true);
export const timesSaid = async (page: Page, text: string): Promise<number> => (await statuses(page)).filter((s) => s === text).length;

/**
 * The page's contract is what it does with the answer of `play()` on the other side's sound, not
 * what an engine does with a made-up stream: the stub decides for that element, in both phases
 * (`window.__blockPlay`: refuse with NotAllowedError; else resolve; `__hangPlay`: never settle), and
 * counts the calls (`__plays`). The engines' own autoplay rules are seen only in a real meeting.
 */
export async function stubRemoteSound(page: Page): Promise<void> {
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
export const plays = (page: Page): Promise<number> => page.evaluate(() => (window as unknown as { __plays: number }).__plays);

/** The staff member's screen starts or stops (the stand-in's meeting reports it as the SDK would). */
export const remoteShare = (page: Page, on: boolean): Promise<void> =>
  page.evaluate((value) => (window as unknown as { __sdk: { remoteShare: (v: boolean) => void } }).__sdk.remoteShare(value), on);
/** The meeting ends the participant's own share (the staff member stopped it). */
export const stopLocalShare = (page: Page): Promise<void> =>
  page.evaluate(() => (window as unknown as { __sdk: { stopLocalShare: () => void } }).__sdk.stopLocalShare());
/** What the meeting offers changes: sharing a screen, captions. */
export const rights = (page: Page, share: boolean, captions: boolean): Promise<void> =>
  page.evaluate(([a, b]) => (window as unknown as { __sdk: { rights: (a: boolean, b: boolean) => void } }).__sdk.rights(a, b), [share, captions]);
export type Spoken = { id: string; text: string; own?: boolean };
/** A caption event with final lines and interim lines of two speakers (the platform's own names for them are never to be shown). */
export const caption = (page: Page, finals: Spoken[], interim: Omit<Spoken, 'id'>[] = []): Promise<void> =>
  page.evaluate(
    ([f, i]) => (window as unknown as { __sdk: { caption: (spec: unknown) => void } }).__sdk.caption({ finals: f, interim: i }),
    [finals, interim] as const,
  );
export const captioning = (page: Page): Promise<{ on: boolean; language: string }> =>
  page.evaluate(() => {
    const s = (window as unknown as { __sdk: { captioning: boolean; captionLanguage: string } }).__sdk;
    return { on: s.captioning, language: s.captionLanguage };
  });
export const callsOf = async (page: Page, name: string): Promise<number> => (await sdk(page)).calls.filter((c) => c === name).length;
/** The screen that `holdPublish` keeps under way lands in the meeting now. */
export const releasePublish = (page: Page): Promise<void> =>
  page.evaluate(() => (window as unknown as { __sdk: { releasePublish: () => void } }).__sdk.releasePublish());
