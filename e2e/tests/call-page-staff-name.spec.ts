/**
 * The call page in the lobby and the call, third part (ADR 0123 step 5c), walked in a browser
 * against the test double of the video SDK: who the participant talks to. The name comes from our
 * own server (`GET .../call/staff`), never from the video platform, and is shown on the other side's
 * picture ("name · term", or the term alone), as the picture's accessible name, and in the captions;
 * the sentences that name the staff member keep the term. One poll with one timer asks for it.
 *
 * Every test but the first stubs the endpoint with `page.route`, as the other call-page specs stub the
 * token; the first uses the real endpoint on the seeded booking, which nobody has accepted, so
 * the label is the term alone. The poll's interval is the stack's setting (`embedded_call_chat_poll_seconds`,
 * 3 seconds by default): a test that waits for the next turn waits for a state, within the expect
 * timeout; a test that proves a timer is stopped, or measures the wait between two turns, moves the
 * page's own clock (`page.clock`, installed before the page loads) and waits for nothing.
 */
import { expect, test, type Page, type Request } from '@playwright/test';

import { said, statuses } from '../call-page';
import {
  caption,
  control,
  granted,
  heading,
  IN_CALL,
  join,
  open,
  reconnection,
  remoteShare,
  stubToken,
  timesSaid,
  WAITING,
  type Plan,
} from '../call-sdk';
import { expectNoAxeViolations } from '../helpers';

test.use({ locale: 'en-US' });

const label = (page: Page) => page.locator('[data-call-remote-label]');
const remote = (page: Page) => page.locator('[data-call-remote]');
const leave = (page: Page) => page.getByRole('button', { name: /^Leave the (call|waiting room)$/ });
const NEXT = { timeout: 20_000 };

/** The organisation's word for staff, as the server renders it for the page. */
const term = async (page: Page): Promise<string> => ((await page.locator('[data-text="staff_term"]').textContent()) ?? '').trim();

type Staff = { status: number; body: unknown; hold?: Promise<void> };
type Stub = { answer: Staff; seen: Request[] };

/** Answer the endpoint as `stub.answer` says now; every request is kept in `stub.seen`. */
async function stubStaff(page: Page, body: unknown = {}): Promise<Stub> {
  const stub: Stub = { answer: { status: 200, body }, seen: [] };
  await page.route('**/call/staff', async (route) => {
    const answer = stub.answer;
    stub.seen.push(route.request());
    if (answer.hold) await answer.hold;
    // A request the page gave up (the call ended while it was held) cannot be answered any more.
    return route
      .fulfill({
        status: answer.status,
        contentType: 'application/json',
        headers: { 'cache-control': 'no-store' },
        body: JSON.stringify(answer.body),
      })
      .catch(() => undefined);
  });
  return stub;
}

/** Into the call with the endpoint stubbed (or not, with `stub` false). */
async function inCall(page: Page, stub: false | unknown = {}, plan: Plan = {}): Promise<Stub | null> {
  await page.clock.install(); // before the page loads: its timers are the test's to move
  const staff = stub === false ? null : await stubStaff(page, stub);
  await stubToken(page, granted());
  await open(page, { captions: true, ...plan });
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();
  return staff;
}

/** How many times the endpoint was asked, as the page's own network log has it. */
const asked = (stub: Stub): number => stub.seen.length;

/** Let `ms` of the page's own time pass at once: a timer that is still running asks in it. */
const pass = async (page: Page, ms: number): Promise<void> => {
  await page.clock.runFor(ms);
  await page.evaluate(() => 0); // a round trip, so that a request the timers made has reached the stub
};

/** How much of the page's time goes by, in steps of 100 ms, until the endpoint is asked once more. */
async function gap(page: Page, stub: Stub, limit = 60_000): Promise<number> {
  const before = asked(stub);
  for (let elapsed = 100; elapsed <= limit; elapsed += 100) {
    await pass(page, 100);
    if (asked(stub) > before) return elapsed;
  }
  throw new Error(`the endpoint was not asked again within ${limit} ms of the page's time`);
}

/** Stop the page's time where it is: from now on only `pass` moves it. */
const freeze = (page: Page): Promise<void> => page.clock.pauseAt(new Date(Date.now() + 2_000));

/** The sentence of the polite region that names the staff member, as often as it was said. */
const told = async (page: Page, name?: string): Promise<number> =>
  (await statuses(page)).filter((s) => (name ? s === `You are talking to ${name}.` : /^You are talking to /.test(s))).length;

// ---- the label ----------------------------------------------------------------------------------

test('with the real endpoint nobody has accepted the booking: the label is the team\'s word alone, also the picture\'s name', async ({ page }) => {
  await inCall(page, false);
  const word = await term(page);

  await expect(label(page)).toBeVisible();
  await expect(label(page)).toHaveText(word);
  await expect(remote(page)).toHaveAccessibleName(word);
  expect(await label(page).innerText()).not.toContain('·');
});

test('with a name the label reads "name · term", as text in the page and as the picture\'s accessible name', async ({ page }) => {
  await inCall(page, { name: 'Sanne' });
  const word = await term(page);

  await expect(label(page)).toHaveText(`Sanne · ${word}`, NEXT);
  await expect(remote(page)).toHaveAccessibleName(`Sanne · ${word}`);
  // Text in the page, not a picture or a pseudo-element, and not a control.
  expect(await label(page).evaluate((el) => el.tagName)).toBe('P');
  expect(await label(page).getAttribute('tabindex')).toBeNull();
  // The picture carries the words as its name: the visible label is hidden from assistive technology.
  expect(await label(page).getAttribute('aria-hidden')).toBe('true');
});

test('the label is not shown in the lobby, where the staff member\'s picture is not either', async ({ page }) => {
  await page.clock.install();
  await stubStaff(page, { name: 'Sanne' });
  await stubToken(page, granted());
  await open(page, { lobby: true });
  await join(page);
  await expect(heading(page, WAITING)).toBeFocused();

  await expect(label(page)).toBeHidden();
  await control(page, 'admit');
  await expect(heading(page, IN_CALL)).toBeFocused();
  await expect(label(page)).toBeVisible();
  await expect(label(page)).toContainText('Sanne', NEXT);
});

test('the name is also the other side in the captions; the participant\'s own lines stay "You"', async ({ page }) => {
  await inCall(page, { name: 'Sanne' });
  await expect(label(page)).toContainText('Sanne', NEXT);
  await page.getByRole('button', { name: /^Captions/ }).click();
  await said(page, 'Captions are on.');

  await caption(page, [{ id: '1', text: 'Can you hear me?', own: false }, { id: '2', text: 'Yes', own: true }]);

  const rows = page.locator('[data-call-captions] .call-caption');
  await expect(rows).toHaveCount(2);
  // The approved design (mock-up 04): the other side is named as on the picture, "name · term".
  await expect(rows.nth(0).locator('.call-caption__who')).toHaveText(`Sanne · ${await term(page)}`);
  await expect(rows.nth(1).locator('.call-caption__who')).toHaveText('You');
});

test('without a name the captions name the other side with the team\'s word', async ({ page }) => {
  await inCall(page, {});
  const word = await term(page);
  await page.getByRole('button', { name: /^Captions/ }).click();
  await said(page, 'Captions are on.');

  await caption(page, [{ id: '1', text: 'Hello', own: false }]);

  await expect(page.locator('[data-call-captions] .call-caption__who')).toHaveText(word);
});

test('the sentences that name the staff member keep the team\'s word, never the name', async ({ page }) => {
  await inCall(page, { name: 'Sanne' });
  const word = await term(page);
  await expect(label(page)).toContainText('Sanne', NEXT);

  await remoteShare(page, true);

  await said(page, `The ${word} is sharing their screen.`);
  expect((await page.locator('[data-call-notice="theirs"]').innerText())).toContain(word);
  expect((await page.locator('[data-call-notice="theirs"]').innerText())).not.toContain('Sanne');
  await expect(page.locator('[data-call-remote-off]')).not.toContainText('Sanne');
});

// ---- when the name arrives or changes ------------------------------------------------------------

test('a name that arrives after the call started is shown at the next turn and said once', async ({ page }) => {
  const stub = (await inCall(page, {})) as Stub;
  const word = await term(page);
  await expect(label(page)).toHaveText(word);
  expect(await timesSaid(page, 'You are talking to Sanne.')).toBe(0);

  stub.answer = { status: 200, body: { name: 'Sanne' } };

  await expect(label(page)).toHaveText(`Sanne · ${word}`, NEXT);
  await said(page, 'You are talking to Sanne.');
  // The next turns bring the same name: it is not said again.
  const before = asked(stub);
  await expect.poll(() => asked(stub), NEXT).toBeGreaterThan(before);
  expect(await timesSaid(page, 'You are talking to Sanne.')).toBe(1);
});

test('a name that changes during the call (another staff member takes over) is shown and said once', async ({ page }) => {
  const stub = (await inCall(page, { name: 'Sanne' })) as Stub;
  const word = await term(page);
  await expect(label(page)).toHaveText(`Sanne · ${word}`, NEXT);

  stub.answer = { status: 200, body: { name: 'Tom' } };

  await expect(label(page)).toHaveText(`Tom · ${word}`, NEXT);
  await said(page, 'You are talking to Tom.');
  expect(await timesSaid(page, 'You are talking to Tom.')).toBe(1);
  await expect(remote(page)).toHaveAccessibleName(`Tom · ${word}`);
  // Back to no name: the word alone, and nothing is said.
  stub.answer = { status: 200, body: {} };
  await expect(label(page)).toHaveText(word, NEXT);
});

test('a failed or unreadable answer changes nothing and the next turn asks again', async ({ page }) => {
  const stub = (await inCall(page, { name: 'Sanne' })) as Stub;
  const word = await term(page);
  await expect(label(page)).toHaveText(`Sanne · ${word}`, NEXT);

  stub.answer = { status: 503, body: { error: 'unavailable' } };
  const before = asked(stub);
  await expect.poll(() => asked(stub), NEXT).toBeGreaterThan(before);
  await expect(label(page)).toHaveText(`Sanne · ${word}`);

  stub.answer = { status: 200, body: ['not', 'an', 'object'] };
  const again = asked(stub);
  await expect.poll(() => asked(stub), NEXT).toBeGreaterThan(again);
  await expect(label(page)).toHaveText(word); // an answer without a name is "no name"
});

test('a name known in the lobby is said once on entering the call', async ({ page }) => {
  await page.clock.install();
  const stub = await stubStaff(page, { name: 'Sanne' });
  await stubToken(page, granted());
  await open(page, { lobby: true });
  await join(page);
  await expect(heading(page, WAITING)).toBeFocused();
  await expect.poll(() => asked(stub), NEXT).toBeGreaterThanOrEqual(1);
  expect(await told(page)).toBe(0); // the lobby shows no picture: nothing is said yet

  await control(page, 'admit');
  await expect(heading(page, IN_CALL)).toBeFocused();

  await said(page, 'You are talking to Sanne.');
  await expect(label(page)).toContainText('Sanne');
  await pass(page, 30_000); // more turns, the same answer
  expect(await told(page, 'Sanne')).toBe(1);
});

test('the same answer asked many times is said once, a change once more, and a cleared name not at all', async ({ page }) => {
  const stub = (await inCall(page, { name: 'Sanne' })) as Stub;
  const word = await term(page);
  await said(page, 'You are talking to Sanne.');
  await freeze(page);

  const before = asked(stub);
  await pass(page, 60_000);
  expect(asked(stub)).toBeGreaterThanOrEqual(before + 10);
  expect(await told(page, 'Sanne')).toBe(1);

  stub.answer = { status: 200, body: { name: 'Tom' } };
  await pass(page, 10_000);
  await said(page, 'You are talking to Tom.');
  await pass(page, 30_000);
  expect(await told(page, 'Tom')).toBe(1);

  stub.answer = { status: 200, body: {} };
  await pass(page, 10_000);
  await expect(label(page)).toHaveText(word);
  await pass(page, 30_000);
  expect(await told(page)).toBe(2); // nothing was said for the cleared name
});

test('an answer that says the feature is off (404) brings the label back to the team\'s word', async ({ page }) => {
  const stub = (await inCall(page, { name: 'Sanne' })) as Stub;
  const word = await term(page);
  await expect(label(page)).toHaveText(`Sanne · ${word}`, NEXT);
  await said(page, 'You are talking to Sanne.');

  stub.answer = { status: 404, body: { error: 'gone' } };

  await expect(label(page)).toHaveText(word, NEXT);
  expect(await told(page)).toBe(1);
});

test('refusals and failures keep the name and make the turns wait longer, a success brings the interval back', async ({ page }) => {
  const stub = (await inCall(page, { name: 'Sanne' })) as Stub;
  const word = await term(page);
  await expect(label(page)).toHaveText(`Sanne · ${word}`, NEXT);
  const seconds = Number(await page.locator('[data-call]').getAttribute('data-call-poll-seconds'));
  const turn = seconds * 1000;
  await said(page, 'You are talking to Sanne.');
  await freeze(page);

  stub.answer = { status: 429, body: { error: 'rate_limited' } };
  await gap(page, stub); // the first refusal: it starts the backing off
  const gaps: number[] = [];
  for (let i = 0; i < 5; i += 1) gaps.push(await gap(page, stub));

  // Twice as long after each refusal, up to 30 seconds (resolution: one step of 100 ms).
  const expected = [2, 4, 8, 16, 16].map((n) => Math.min(turn * n, 30_000));
  gaps.forEach((got, i) => expect(Math.abs(got - expected[i]), `gap ${i}: ${got} against ${expected[i]}`).toBeLessThanOrEqual(300));
  // Nothing is announced and the name stays.
  await expect(label(page)).toHaveText(`Sanne · ${word}`);
  expect(await told(page)).toBe(1);

  stub.answer = { status: 200, body: { name: 'Sanne' } };
  await gap(page, stub); // the request that is answered well
  const back = await gap(page, stub);
  expect(Math.abs(back - turn)).toBeLessThanOrEqual(300);
});

test('a network error is a failure like a refusal: the name stays and the next turn waits longer', async ({ page }) => {
  await page.clock.install();
  let failing = false;
  const seen: Request[] = [];
  await page.route('**/call/staff', (route) => {
    seen.push(route.request());
    return failing
      ? route.abort('connectionreset')
      : route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ name: 'Sanne' }) });
  });
  await stubToken(page, granted());
  await open(page, { captions: true });
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();
  const word = await term(page);
  await expect(label(page)).toHaveText(`Sanne · ${word}`, NEXT);
  const stub: Stub = { answer: { status: 200, body: {} }, seen };
  await freeze(page);

  failing = true;
  await gap(page, stub);
  const second = await gap(page, stub);

  expect(second).toBeGreaterThan(Number(await page.locator('[data-call]').getAttribute('data-call-poll-seconds')) * 1000);
  await expect(label(page)).toHaveText(`Sanne · ${word}`);
});

// ---- the one poll -------------------------------------------------------------------------------

test('the page asks on entering the lobby and again on entering the call, then at each turn', async ({ page }) => {
  const stub = await stubStaff(page, {});
  await stubToken(page, granted());
  await open(page, { lobby: true });
  await join(page);
  await expect(heading(page, WAITING)).toBeFocused();
  await expect.poll(() => asked(stub), NEXT).toBeGreaterThanOrEqual(1);
  const inLobby = asked(stub);

  await control(page, 'admit');
  await expect(heading(page, IN_CALL)).toBeFocused();

  await expect.poll(() => asked(stub), NEXT).toBeGreaterThan(inLobby);
  const inTheCall = asked(stub);
  await expect.poll(() => asked(stub), NEXT).toBeGreaterThan(inTheCall); // and then again, by itself
});

test('the request goes to our own origin only, with no body, and nothing is kept in the browser', async ({ page }) => {
  const stub = (await inCall(page, { name: 'Sanne' })) as Stub;
  await expect(label(page)).toContainText('Sanne', NEXT);

  const origin = new URL(page.url()).origin;
  for (const request of stub.seen) {
    expect(new URL(request.url()).origin).toBe(origin);
    expect(request.method()).toBe('GET');
    expect(request.postData()).toBeNull();
  }
  const kept = await page.evaluate(() => ({
    local: window.localStorage.length,
    session: window.sessionStorage.length,
    cookie: document.cookie.includes('Sanne'),
  }));
  expect(kept).toEqual({ local: 0, session: 0, cookie: false });
});

for (const [name, stop] of [
  ['the call is left', async (page: Page) => leave(page).click()],
  ['the call ends', async (page: Page) => control(page, 'end')],
  ['the call fails', async (page: Page) => reconnection(page, 'fail')],
] as const) {
  test(`the poll stops when ${name}`, async ({ page }) => {
    const stub = (await inCall(page, { name: 'Sanne' })) as Stub;
    await expect(label(page)).toContainText('Sanne', NEXT);

    await stop(page);
    await expect(page.locator('[data-call-state="call"]')).toBeHidden();
    // A timer that was left running would ask at the next turn: wait one turn and a margin (a state
    // cannot be waited for when the thing to prove is that nothing happens any more).
    await pass(page, 5_000);
    const stopped = asked(stub);
    await pass(page, 120_000); // many turns of the poll, were it still running

    expect(asked(stub)).toBe(stopped);
  });
}

test('an answer that comes after the call ended changes nothing', async ({ page }) => {
  const stub = (await inCall(page, {})) as Stub;
  let release: () => void = () => {};
  const hold = new Promise<void>((resolve) => (release = resolve));
  stub.answer = { status: 200, body: { name: 'Late' }, hold };
  const held = asked(stub);
  await expect.poll(() => asked(stub), NEXT).toBeGreaterThan(held); // a request is on its way

  await control(page, 'end');
  await expect(page.locator('[data-call-state="ended"]')).toBeVisible();
  release();
  await pass(page, 60_000);

  expect(await timesSaid(page, 'You are talking to Late.')).toBe(0);
  await expect(label(page)).toBeHidden();
  await expect(page.locator('body')).not.toContainText('Late ·');
});

// ---- as text, and for everyone -------------------------------------------------------------------

test('a name with markup is shown as text and runs nothing', async ({ page }) => {
  await page.addInitScript(() => ((window as unknown as { pwned?: boolean }).pwned = undefined));
  const name = '<img src=x onerror="window.pwned=true"> <b>Sam</b>';
  await inCall(page, { name });

  await expect(label(page)).toContainText('<img src=x', NEXT);
  expect(await label(page).locator('img, b').count()).toBe(0);
  expect(await page.evaluate(() => (window as unknown as { pwned?: boolean }).pwned)).toBeUndefined();
  // The accessible name is the same text; the caption names the other side the same way, as text.
  await expect(remote(page)).toHaveAccessibleName(new RegExp('<img src=x'));
  await page.getByRole('button', { name: /^Captions/ }).click();
  await said(page, 'Captions are on.');
  await caption(page, [{ id: '1', text: 'Hi', own: false }]);
  await expect(page.locator('[data-call-captions] .call-caption__who')).toContainText('<img src=x');
  expect(await page.locator('[data-call-captions] img, [data-call-captions] b').count()).toBe(0);
});

test('a name longer than the server would send is cut, and the label stays inside the picture on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await inCall(page, { name: 'A'.repeat(300) });
  await expect(label(page)).toContainText('AAAA', NEXT);

  const text = await label(page).innerText();
  expect(text.length).toBeLessThan(100);
  const stage = await page.locator('[data-call-stage]').boundingBox();
  const box = await label(page).boundingBox();
  expect(box && stage && box.x >= stage.x - 1 && box.x + box.width <= stage.x + stage.width + 1).toBe(true);
});

test('axe finds nothing in the call with a name, and the label takes no part in the tab order', async ({ page }) => {
  await inCall(page, { name: 'Sanne' });
  await expect(label(page)).toContainText('Sanne', NEXT);
  await expectNoAxeViolations(page);

  const focusable = await page.locator('[data-call-stage] [tabindex], [data-call-stage] a, [data-call-stage] button').evaluateAll((els) => els.map((el) => el.className));
  expect(focusable.some((c) => String(c).includes('call-remote__label'))).toBe(false);
});

test('in forced colours the label has a border of its own and the name is still read', async ({ page }) => {
  await page.emulateMedia({ forcedColors: 'active' });
  await inCall(page, { name: 'Sanne' });
  await expect(label(page)).toContainText('Sanne', NEXT);

  expect(await label(page).evaluate((el) => getComputedStyle(el).borderTopWidth)).toBe('1px');
  await expectNoAxeViolations(page);
});
