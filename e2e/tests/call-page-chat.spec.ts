/**
 * The chat beside the call (ADR 0123 point 8, step 7c), walked in a browser against the test double
 * of the video SDK, on the seeded booking whose task is a conversation (`{call_chat_page}`).
 *
 * The stack has no contact center, so the page's two requests are answered by a model of the
 * server in this file (`chat`): `GET .../call/chat?since=&waiting=` returns what is newer than
 * `since`, the state of the waiting ids, the staff member's name and whether the chat is closed,
 * as `app/api/call_chat_pages.py` does; `POST` takes `{id, text}` and answers the stored message or
 * the refusal a test asks for. What these tests prove is the page's behaviour with those answers:
 * the side bar and when it is there, sending (Enter, the button, Shift and Enter), the states of a
 * message in words, a refused or failed send that keeps the text, the characters left in text, the
 * closed state, a log that only gains nodes (a reader's place is kept), markup as text, the poll's
 * turns, and axe and the keyboard on the states that are new. The poll's interval is the stack's
 * (`embedded_call_chat_poll_seconds`, 3 seconds by default): a test that waits for the next turn
 * waits for a state within the expect timeout, and nothing here sleeps for a fixed time.
 *
 * The server's own rules (the order of refusals, the rate, the length, the replay, the abuse cases)
 * are walked over HTTP in `tests/api/test_call_chat_pages.py`.
 */
import { expect, test, type Page, type Request, type Route } from '@playwright/test';

import { said, statuses } from '../call-page';
import {
  control,
  FAILED,
  granted,
  heading,
  IN_CALL,
  join,
  open,
  state,
  stubToken,
  WAITING,
  type Plan,
} from '../call-sdk';
import { expectNoAxeViolations, tabTo } from '../helpers';

test.use({ locale: 'en-US' });

const NEXT = { timeout: 20_000 };

type Line = { id: number; author: 'participant' | 'staff'; text: string; state: string; time: string; at: string };
const AT = '2026-11-15T09:30:00+00:00';
type Model = {
  lines: Line[];
  staff: string;
  closed: null | 'ended' | 'staff' | 'lost';
  /** What the next send is answered with, in turn; the last repeats. A number is a status. */
  sends: (number | 'ok')[];
  reads: Request[];
  posts: { id: string; text: string }[];
  /** A status for every read (a refusal), or 0 for the model's own answer. */
  readStatus: number;
  /** The ids that are confirmed at the next read. */
  confirm: boolean;
};

const field = (page: Page) => page.getByRole('textbox', { name: 'Your message' });
const sendButton = (page: Page) => page.getByRole('button', { name: 'Send', exact: true });
const side = (page: Page) => page.locator('[data-call-chat]');
const log = (page: Page) => page.getByRole('log', { name: 'Chat messages' });
const messages = (page: Page) => page.locator('[data-call-chat-log] .call-chat__message');
const problem = (page: Page) => page.locator('[data-call-chat-problem]');
const closedBox = (page: Page) => page.locator('[data-call-chat-closed]');
const leave = (page: Page) => page.getByRole('button', { name: /^Leave the (call|waiting room)$/ });

function model(): Model {
  return { lines: [], staff: '', closed: null, sends: ['ok'], reads: [], posts: [], readStatus: 0, confirm: false };
}

let nextId = 100;
const ERRORS: Record<number, string> = { 400: 'refused', 403: 'refused', 409: 'closed', 429: 'rate_limited', 503: 'unavailable' };

/** Answer the chat's address as the server does, from `m`; a test changes `m` while the page runs. */
async function serve(page: Page, m: Model): Promise<void> {
  await page.route('**/call/chat**', async (route: Route) => {
    const request = route.request();
    const answer = (status: number, body: unknown) =>
      route
        .fulfill({ status, contentType: 'application/json', headers: { 'cache-control': 'no-store' }, body: JSON.stringify(body) })
        .catch(() => undefined);
    if (request.method() === 'POST') {
      const body = request.postDataJSON() as { id: string; text: string };
      m.posts.push(body);
      const outcome = m.sends[Math.min(m.posts.length - 1, m.sends.length - 1)];
      if (outcome !== 'ok') return answer(outcome, { error: ERRORS[outcome] ?? 'refused' });
      const line: Line = { id: (nextId += 1), author: 'participant', text: body.text, state: 'sent', time: '09:30', at: AT };
      m.lines.push(line);
      return answer(200, { message: line });
    }
    m.reads.push(request);
    if (m.readStatus) return answer(m.readStatus, { error: 'rate_limited' });
    const url = new URL(request.url());
    const since = Number(url.searchParams.get('since') ?? 0);
    const waiting = (url.searchParams.get('waiting') ?? '').split(',').filter(Boolean).map(Number);
    const states: Record<string, string> = {};
    for (const line of m.lines) {
      if (!waiting.includes(line.id)) continue;
      if (m.confirm && line.state === 'sent') line.state = 'delivered';
      if (line.state !== 'sent') states[String(line.id)] = line.state; // only what the page named as waiting
    }
    // At most 100 a read, with `more`, as the server answers.
    const newer = m.lines.filter((l) => l.id > since);
    return answer(200, { messages: newer.slice(0, 100), states, staff: m.staff, closed: m.closed, more: newer.length > 100 });
  });
}

/** The staff member writes. */
const staffSays = (m: Model, text: string): void => {
  m.lines.push({ id: (nextId += 1), author: 'staff', text, state: 'delivered', time: '09:31', at: AT });
};

const probed = new WeakSet<Page>();

/** Two requests of our own through the page, so that what a timer started has reached the stubs. */
async function quiet(page: Page): Promise<void> {
  if (!probed.has(page)) {
    probed.add(page);
    await page.route('**/__probe', (route) => route.fulfill({ status: 204 }));
  }
  for (let i = 0; i < 2; i += 1) await page.evaluate(() => fetch('/__probe', { cache: 'no-store' }).then(() => 0));
}

/** The page opened on the booking that has a chat, with the stubs, up to the check or further. */
async function start(page: Page, m: Model, plan: Plan = {}): Promise<void> {
  await serve(page, m);
  await stubToken(page, granted());
  await open(page, plan, 'call_chat_page');
}

/** Into the call with the chat. */
async function inCall(page: Page, m: Model, plan: Plan = {}): Promise<void> {
  await start(page, m, plan);
  await join(page);
  await expect(heading(page, IN_CALL)).toBeFocused();
}

// ---- where the side bar is -----------------------------------------------------------------------

test('the side bar is not on the start state and is there from the check on, with its warning', async ({ page }) => {
  const m = model();
  await start(page, m);
  await expect(side(page)).toBeHidden();
  expect(m.reads).toHaveLength(0); // nothing is asked before the participant begins

  await page.getByRole('button', { name: 'Test camera and microphone' }).click();

  await expect(side(page)).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: /^Chat with our / })).toBeVisible();
  await expect(side(page)).toContainText('Please do not share identity numbers in the chat');
  await expect(log(page)).toHaveAttribute('aria-live', 'polite');
  await expect.poll(() => m.reads.length, NEXT).toBeGreaterThan(0);
});

test('a booking without a chat has no side bar, loads no chat script and asks nothing of a chat', async ({ page }) => {
  const asked: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/call/chat')) asked.push(request.url());
  });
  await stubToken(page, granted());
  await open(page, {});
  await page.getByRole('button', { name: 'Test camera and microphone' }).click();

  await expect(page.locator('[data-call-chat]')).toHaveCount(0);
  expect(await page.locator('script[src*="call-chat.js"]').count()).toBe(0);
  expect(asked).toEqual([]);
});

// ---- sending -------------------------------------------------------------------------------------

test('Enter sends, Shift and Enter break the line, the button sends, and focus stays in the field', async ({ page }) => {
  const m = model();
  await inCall(page, m);

  await field(page).fill('Hello');
  await field(page).press('Enter');
  await expect(messages(page)).toHaveCount(1);
  await expect(field(page)).toHaveValue('');
  await expect(field(page)).toBeFocused();

  await field(page).pressSequentially('a');
  await field(page).press('Shift+Enter');
  await field(page).pressSequentially('b');
  await expect(field(page)).toHaveValue('a\nb'); // the line broke, nothing was sent
  expect(m.posts).toHaveLength(1);
  await sendButton(page).click();

  await expect(messages(page)).toHaveCount(2);
  expect(m.posts.map((p) => p.text)).toEqual(['Hello', 'a\nb']);
  expect(m.posts[0].id).toMatch(/^[0-9a-f]{32}$/);
  expect(m.posts[0].id).not.toBe(m.posts[1].id);
  // The own messages say who wrote them, when, and where they stand, in words.
  await expect(messages(page).first()).toContainText('You');
  await expect(messages(page).first()).toContainText('09:30');
  await expect(messages(page).first()).toContainText('Sent');
  // Line breaks of a message are kept.
  await expect(messages(page).nth(1).locator('.call-chat__text')).toHaveCSS('white-space', 'pre-wrap');
});

test('the way of using Enter and Shift and Enter is said in text next to the field', async ({ page }) => {
  await inCall(page, model());

  await expect(page.getByText('Enter sends the message. Shift and Enter together start a new line.')).toBeVisible();
  await expect(field(page)).toHaveAccessibleDescription(/Enter sends the message/);
});

test('an empty message is refused by the page without a request, with a reason linked to the field', async ({ page }) => {
  const m = model();
  await inCall(page, m);

  await field(page).fill('   ');
  await sendButton(page).click();

  await expect(problem(page)).toHaveText('Write a message first.');
  await expect(field(page)).toBeFocused();
  await expect(field(page)).toHaveAccessibleDescription(/Write a message first\./);
  await expect(field(page)).toHaveAttribute('aria-invalid', 'true');
  expect(m.posts).toEqual([]);
  await said(page, 'Write a message first.');
  // Typing again takes the sentence away.
  await field(page).pressSequentially('x');
  await expect(problem(page)).toBeHidden();
  await expect(field(page)).not.toHaveAttribute('aria-invalid', 'true');
});

test('the characters left are said in text and a message that is too long is refused by the page', async ({ page }) => {
  const m = model();
  await inCall(page, m);
  const max = Number(await page.locator('[data-call]').getAttribute('data-call-chat-max'));
  await expect(page.locator('[data-call-chat-left]')).toHaveText(`${max} characters left`);

  await field(page).fill('x'.repeat(10));
  await expect(page.locator('[data-call-chat-left]')).toHaveText(`${max - 10} characters left`);
  await field(page).fill('x'.repeat(max + 1));
  await expect(page.locator('[data-call-chat-left]')).toHaveText('1 characters too many');
  await sendButton(page).click();

  await expect(problem(page)).toHaveText('Your message is too long. Make it shorter.');
  expect(m.posts).toEqual([]);
  await expect(field(page)).toHaveValue('x'.repeat(max + 1)); // the text stays
});

test('a send that fails keeps the text, says so in words, and the retry is the same message', async ({ page }) => {
  const m = model();
  m.sends = [503, 'ok'];
  await inCall(page, m);

  await field(page).fill('Can you hear me?');
  await sendButton(page).click();

  await expect(problem(page)).toHaveText('The message was not sent. Your text is still in the field. Choose Send to try again.');
  await expect(field(page)).toHaveValue('Can you hear me?');
  await expect(field(page)).toBeFocused();
  await expect(field(page)).toHaveAccessibleDescription(/The message was not sent/);
  await expect(messages(page)).toHaveCount(0); // nothing is shown as sent that was not
  await said(page, 'The message was not sent. Your text is still in the field. Choose Send to try again.');

  await sendButton(page).click();

  await expect(messages(page)).toHaveCount(1);
  await expect(field(page)).toHaveValue('');
  await expect(problem(page)).toBeHidden();
  expect(m.posts).toHaveLength(2);
  expect(m.posts[1].id).toBe(m.posts[0].id); // the same identifier: one message at the other side
});

test('a change of the text after a failure is a new message', async ({ page }) => {
  const m = model();
  m.sends = [503, 'ok'];
  await inCall(page, m);
  await field(page).fill('one');
  await sendButton(page).click();
  await expect(problem(page)).toBeVisible();

  await field(page).fill('two');
  await sendButton(page).click();

  await expect(messages(page)).toHaveCount(1);
  expect(m.posts[1].id).not.toBe(m.posts[0].id);
});

test('the answers 429 and 400 of the server are told in their own words, and the text stays', async ({ page }) => {
  const m = model();
  m.sends = [429, 400, 'ok'];
  await inCall(page, m);

  await field(page).fill('too fast');
  await sendButton(page).click();
  await expect(problem(page)).toHaveText('You are sending too fast. Wait a moment and try again.');
  await expect(field(page)).toHaveValue('too fast');

  await sendButton(page).click(); // the second answer has no reason the page knows: the general sentence
  await expect(problem(page)).toContainText('The message was not sent');
  await expect(field(page)).toHaveValue('too fast');
});

// ---- the thread ----------------------------------------------------------------------------------

test('the state of an own message goes from Sent to Delivered in the same line', async ({ page }) => {
  const m = model();
  await inCall(page, m);
  await field(page).fill('Hello');
  await sendButton(page).click();
  const own = messages(page).first();
  await expect(own).toContainText('Sent');
  await own.evaluate((el) => ((el as HTMLElement & { kept?: boolean }).kept = true));

  m.confirm = true;

  await expect(own).toContainText('Delivered', NEXT);
  await expect(own).not.toContainText('Sent');
  expect(await own.evaluate((el) => (el as HTMLElement & { kept?: boolean }).kept)).toBe(true); // not drawn again
  await expect(messages(page)).toHaveCount(1); // and not twice, though the read also returns it
});

test('a staff member\'s message arrives with their name, politely, without moving focus', async ({ page }) => {
  const m = model();
  m.staff = 'Sanne';
  await inCall(page, m);
  await field(page).focus();

  staffSays(m, 'Good morning, I am reading along.');

  await expect(messages(page)).toHaveCount(1, NEXT);
  const line = messages(page).first();
  await expect(line).toContainText('Good morning, I am reading along.');
  await expect(line.locator('.call-chat__who')).toHaveText(/^Sanne · .+/);
  await expect(line).toContainText('09:31');
  await expect(field(page)).toBeFocused();
  await expect(log(page)).toHaveAttribute('aria-live', 'polite');
  expect(await log(page).evaluate((el) => el.getAttribute('role'))).toBe('log');
});

test('without a name the staff member is the team\'s word; a name that comes in after is on the later lines and the read line is left as it was', async ({ page }) => {
  const m = model();
  staffSays(m, 'Hello');
  await inCall(page, m);
  const first = messages(page).first();
  await expect(first.locator('.call-chat__who')).toHaveText(/^[^·]+$/, NEXT);
  await first.evaluate((el) => ((el as HTMLElement & { kept?: boolean }).kept = true));

  m.staff = 'Sanne';
  staffSays(m, 'Second');

  await expect(messages(page)).toHaveCount(2, NEXT);
  await expect(messages(page).nth(1).locator('.call-chat__who')).toHaveText(/^Sanne · /);
  await expect(first.locator('.call-chat__who')).toHaveText(/^[^·]+$/); // a line that was read is not rewritten
  expect(await first.evaluate((el) => (el as HTMLElement & { kept?: boolean }).kept)).toBe(true);
});

test('the time of a message is a time element with its full value, and a thread of more than a hundred is read in pieces', async ({ page }) => {
  const m = model();
  for (let n = 1; n <= 105; n += 1) staffSays(m, `Message ${n}`);
  m.lines[0].time = '12 Nov 2026 09:31'; // not from today: the server puts the date in front
  await inCall(page, m);

  await expect(messages(page)).toHaveCount(105, NEXT);

  const when = messages(page).first().locator('time.call-chat__time');
  await expect(when).toHaveText('12 Nov 2026 09:31');
  await expect(when).toHaveAttribute('datetime', AT);
  expect(m.reads.length).toBeGreaterThanOrEqual(2); // the first read said "more" and the next came at once
});

test('the log is a tab stop only while it scrolls', async ({ page }) => {
  const m = model();
  staffSays(m, 'Hello');
  await inCall(page, m);
  await expect(messages(page)).toHaveCount(1, NEXT);
  await expect(log(page)).not.toHaveAttribute('tabindex', /.*/);

  for (let n = 1; n <= 30; n += 1) staffSays(m, `Message number ${n} with some words to fill a line or two of the log.`);

  await expect(log(page)).toHaveAttribute('tabindex', '0', NEXT);
});

test('a reload shows the thread and a message that arrives twice is shown once', async ({ page }) => {
  const m = model();
  staffSays(m, 'First');
  staffSays(m, 'Second');
  await inCall(page, m);
  await expect(messages(page)).toHaveCount(2, NEXT);
  // The history of a reload is drawn with the live region switched off: it is not read out.
  const lines = await page.evaluate(() => (window as unknown as { spied: { statuses: string[] } }).spied.statuses);
  expect(lines.some((s) => /First|Second/.test(s))).toBe(false);

  m.lines.push({ ...m.lines[1] }); // the same id again
  await expect.poll(() => m.reads.length, NEXT).toBeGreaterThan(2);

  await expect(messages(page)).toHaveCount(2);
});

test('lines that did not change are not drawn again, and the reader\'s place in the log is kept', async ({ page }) => {
  const m = model();
  for (let n = 1; n <= 30; n += 1) staffSays(m, `Message number ${n} with some words to fill a line or two of the log.`);
  await inCall(page, m);
  await expect(messages(page)).toHaveCount(30, NEXT);
  await messages(page).first().evaluate((el) => ((el as HTMLElement & { kept?: boolean }).kept = true));
  await log(page).evaluate((el) => (el.scrollTop = 0)); // the reader went back to the start

  staffSays(m, 'A new one.');
  await expect(messages(page)).toHaveCount(31, NEXT);

  expect(await messages(page).first().evaluate((el) => (el as HTMLElement & { kept?: boolean }).kept)).toBe(true);
  expect(await log(page).evaluate((el) => el.scrollTop)).toBe(0); // the new message did not pull the reader down
});

test('markup in a message, the participant\'s or the staff member\'s, is shown as text', async ({ page }) => {
  const m = model();
  const markup = '<img src=x onerror="document.title=\'pwned\'"><b>bold</b>';
  staffSays(m, markup);
  await inCall(page, m);
  await expect(messages(page)).toHaveCount(1, NEXT);

  await field(page).fill(markup);
  await sendButton(page).click();

  await expect(messages(page)).toHaveCount(2);
  await expect(messages(page).nth(0).locator('.call-chat__text')).toHaveText(markup);
  await expect(messages(page).nth(1).locator('.call-chat__text')).toHaveText(markup);
  await expect(page.locator('[data-call-chat-log] img, [data-call-chat-log] b')).toHaveCount(0);
  expect(await page.title()).not.toBe('pwned');
});

test('a refused read keeps what is shown and announces nothing; the wait grows as the name poll\'s does', async ({ page }) => {
  // The back-off itself is the poll's (`call-poll.js`) and is measured in call-page-staff-name.spec.ts
  // (`refusals and failures keep the name and make the turns wait longer ...`); here: the chat's part.
  const m = model();
  staffSays(m, 'Still here');
  await inCall(page, m);
  await expect(messages(page)).toHaveCount(1, NEXT);
  const before = m.reads.length;
  const said = (await statuses(page)).length;

  m.readStatus = 429;
  await expect.poll(() => m.reads.length, NEXT).toBeGreaterThan(before);

  await expect(messages(page)).toHaveCount(1); // what was shown stays
  // The polite line says one sentence every 1.5 seconds and may still be saying what the join
  // queued (the camera, the microphone, being let in), so the count of lines is not the test: no
  // line that came after the refusal is about the chat or about a failure.
  const later = (await statuses(page)).slice(said);
  expect(later.filter((line) => /chat|message|deliver|sent|try again|too fast|not able|refus/i.test(line))).toEqual([]);
  m.readStatus = 0;
  staffSays(m, 'Back again');
  await expect(messages(page)).toHaveCount(2, { timeout: 40_000 }); // a success brings the turns back
});

// ---- the closed state ----------------------------------------------------------------------------

test('a chat that the staff member closed shows the thread and a sentence instead of the field, while the call goes on', async ({ page }) => {
  const m = model();
  staffSays(m, 'Thank you, I am reading along.');
  await inCall(page, m);
  await expect(messages(page)).toHaveCount(1, NEXT);
  await field(page).focus();

  m.closed = 'staff';

  await expect(closedBox(page)).toBeVisible(NEXT);
  await expect(closedBox(page)).toContainText('Chat closed');
  await expect(closedBox(page)).toContainText('has closed the chat. You can still read the messages. The call goes on.');
  await expect(field(page)).toBeHidden();
  await expect(sendButton(page)).toBeHidden();
  await expect(messages(page)).toHaveCount(1); // the thread stays readable
  await expect(closedBox(page)).toBeFocused(); // the field that had focus went away: focus goes to the sentence
  await said(page, 'The chat is closed.');
  await expect(heading(page, IN_CALL)).toBeVisible(); // the call itself goes on
});

test('a send that the server says is closed does not decide the reason: one more read brings the last message and the server\'s reason', async ({ page }) => {
  const m = model();
  m.sends = [409];
  await inCall(page, m);
  await field(page).fill('Are you still there?');
  m.closed = 'staff'; // the conversation ended while the participant was typing
  staffSays(m, 'Goodbye, the chat is ending.');

  await sendButton(page).click();

  await expect(closedBox(page)).toBeVisible(NEXT);
  await expect(messages(page).filter({ hasText: 'Goodbye, the chat is ending.' })).toHaveCount(1); // read after the 409
  await expect(closedBox(page)).toContainText('has closed the chat');
});

test('a conversation that did not go ahead has its own sentence, not the staff member\'s', async ({ page }) => {
  const m = model();
  await inCall(page, m);

  m.closed = 'lost';

  await expect(closedBox(page)).toContainText('the conversation did not go ahead', NEXT);
  await expect(closedBox(page)).not.toContainText('has closed the chat');
});

test('a session that is gone (403) or a chat that is gone (404) says so, takes the form away and offers no retry', async ({ page }) => {
  const m = model();
  m.sends = [403];
  await inCall(page, m);
  await field(page).fill('Hello');

  await sendButton(page).click();

  await expect(closedBox(page)).toContainText('The chat is no longer available.');
  await expect(closedBox(page)).toContainText('open your appointment again');
  await expect(field(page)).toBeHidden();
  await expect(sendButton(page)).toBeHidden();
  await expect(problem(page)).toBeHidden(); // no "choose Send to try again"
});

test('a message the platform refused shows as not delivered and the chat is closed', async ({ page }) => {
  const m = model();
  await inCall(page, m);
  await field(page).fill('Hello');
  await sendButton(page).click();
  await expect(messages(page).first()).toContainText('Sent');

  // The page names the message it still shows as waiting; the model answers its new state.
  m.lines[0].state = 'refused';
  m.closed = 'staff';

  await expect(closedBox(page)).toBeVisible(NEXT);
  await expect(messages(page).first()).toContainText('Not delivered', NEXT);
  await said(page, 'A message was not delivered.');
});

test('when the call ends the thread stays readable and the field makes way for the sentence that says why', async ({ page }) => {
  const m = model();
  staffSays(m, 'Bye');
  await inCall(page, m);
  await expect(messages(page)).toHaveCount(1, NEXT);

  await control(page, 'end');

  await expect(heading(page, 'The call has ended')).toBeFocused();
  await expect(closedBox(page)).toContainText('The chat is closed because the call has ended. You can still read the messages.');
  await expect(field(page)).toBeHidden();
  await expect(messages(page)).toHaveCount(1);
  await expect(page.getByText('Our copy of the personal data of this call, the chat included, is deleted 24 hours after the end of your appointment. The contact center keeps its own record of the chat.')).toBeVisible();
});

test('when the participant leaves the sentence says so', async ({ page }) => {
  const m = model();
  await inCall(page, m);

  await leave(page).click();

  await expect(heading(page, 'You left the call')).toBeFocused();
  await expect(closedBox(page)).toContainText('The chat is closed because you left the call.');
  await expect(field(page)).toBeHidden();
});

test('the poll stops once the chat is closed and read', async ({ page }) => {
  await page.clock.install(); // the page's timers are the test's to move
  const m = model();
  m.closed = 'ended';
  await inCall(page, m);
  await expect(closedBox(page)).toBeVisible();
  const reads = m.reads.length;
  expect(reads).toBeGreaterThan(0);

  // A closed chat is not asked about again: a minute of the page's time later nothing more was asked.
  for (let i = 0; i < 4; i += 1) {
    await page.clock.fastForward(15_000);
    await quiet(page);
  }

  expect(m.reads.length).toBe(reads);
});

// ---- the chat where the picture fails ------------------------------------------------------------

test('the chat is there, said and usable, when the browser refuses the camera', async ({ page }) => {
  const m = model();
  await serve(page, m);
  await stubToken(page, granted());
  await open(page, {}, 'call_chat_page');
  // The devices of the page are made up by `open`; a refusal is played by the spy of the device check.
  await page.evaluate(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      throw new DOMException('blocked', 'NotAllowedError');
    };
  });
  await page.getByRole('button', { name: 'Test camera and microphone' }).click();

  await expect(page.locator('[data-call-denied]:not([hidden])')).toContainText('The chat stays available.');
  await expect(side(page)).toBeVisible();
  await field(page).fill('My camera does not work');
  await sendButton(page).click();
  await expect(messages(page)).toHaveCount(1);
  await expectNoAxeViolations(page);
});

test('the chat is there when the call cannot be set up, with the sentence that says so', async ({ page }) => {
  const m = model();
  await serve(page, m);
  await stubToken(page, { status: 503, body: { error: 'unavailable' } });
  await open(page, {}, 'call_chat_page');
  await join(page);
  await expect(heading(page, FAILED)).toBeFocused({ timeout: 30_000 });

  await expect(state(page, 'failed')).toContainText('You can still reach us in the chat beside this page.');
  await expect(side(page)).toBeVisible();
  await field(page).fill('The picture does not work for me');
  await sendButton(page).click();
  await expect(messages(page)).toHaveCount(1);
  await expectNoAxeViolations(page);
});

// ---- the keyboard, the sizes, the preferences ----------------------------------------------------

test('the whole chat is reachable and usable by the keyboard alone, in the order of the page', async ({ page }) => {
  const m = model();
  staffSays(m, 'Hello');
  await inCall(page, m);
  await expect(messages(page)).toHaveCount(1, NEXT);

  await tabTo(page, field(page)); // the log does not scroll, so it is no tab stop
  await expect(field(page)).toBeFocused();
  await page.keyboard.type('From the keyboard');
  await page.keyboard.press('Tab');
  await expect(sendButton(page)).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(messages(page)).toHaveCount(2);
  // The bar comes after the chat in the page: the chat is not a trap.
  await page.keyboard.press('Tab');
  await expect(page.locator('[data-call-controls]').getByRole('button').first()).toBeFocused();
});

test('axe finds nothing on the side bar in the check, the call, with a closed chat and a refused message', async ({ page }) => {
  const m = model();
  staffSays(m, 'Hello');
  await start(page, m);
  await page.getByRole('button', { name: 'Test camera and microphone' }).click();
  await expect(side(page)).toBeVisible();
  await expectNoAxeViolations(page);

  await field(page).fill('x');
  await page.getByRole('button', { name: 'Join', exact: true }).click();
  await expect(heading(page, IN_CALL)).toBeFocused();
  await expect(messages(page)).toHaveCount(1, NEXT);
  await expectNoAxeViolations(page);

  m.lines.push({ id: 900, author: 'participant', text: 'Lost', state: 'refused', time: '09:40', at: AT });
  m.closed = 'staff';
  await expect(closedBox(page)).toBeVisible(NEXT);
  await expectNoAxeViolations(page);
});

test('at 320 px nothing scrolls sideways and every target is at least 24 by 24 pixels', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  const m = model();
  staffSays(m, 'A rather long message with a-very-long-word-that-must-wrap-somewhere-in-this-narrow-window-0123456789');
  await inCall(page, m);
  await expect(messages(page)).toHaveCount(1, NEXT);

  await side(page).scrollIntoViewIfNeeded();

  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  for (const target of [sendButton(page), field(page)]) {
    const box = await target.boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(24);
    expect(box!.height).toBeGreaterThanOrEqual(24);
  }
});

test('with forced colours and reduced motion the messages keep a border and nothing moves', async ({ page }) => {
  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
  const m = model();
  staffSays(m, 'Hello');
  await inCall(page, m);
  await expect(messages(page)).toHaveCount(1, NEXT);

  await expect(messages(page).first()).toHaveCSS('border-top-style', 'solid');
  await expect(messages(page).first()).not.toHaveCSS('border-top-width', '0px');
  await expectNoAxeViolations(page);
});

test.describe('a touch keyboard', () => {
  test.use({ hasTouch: true, isMobile: true });

  test('Enter starts a new line and the button sends, and the hint says so', async ({ page, browserName }) => {
    test.skip(browserName === 'firefox', 'Firefox has no mobile emulation');
    const m = model();
    await inCall(page, m);

    await expect(page.getByText('Choose Send to send the message. Enter starts a new line.')).toBeVisible();
    await expect(page.getByText('Enter sends the message. Shift and Enter together start a new line.')).toBeHidden();
    await field(page).click();
    await field(page).pressSequentially('a');
    await field(page).press('Enter');
    await field(page).pressSequentially('b');

    await expect(field(page)).toHaveValue('a\nb');
    expect(m.posts).toEqual([]);
    await sendButton(page).click();
    await expect(messages(page)).toHaveCount(1);
  });
});

test('the lobby has the chat too', async ({ page }) => {
  const m = model();
  await start(page, m, { lobby: true });
  await join(page);
  await expect(heading(page, WAITING)).toBeFocused();

  await expect(side(page)).toBeVisible();
  await field(page).fill('I am here');
  await sendButton(page).click();
  await expect(messages(page)).toHaveCount(1);
});
