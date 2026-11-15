# Accessibility audit: the manual passes

The automated gates (axe with zero violations, Lighthouse accessibility, the keyboard-only runs,
320 px and 400 % zoom, forced colours, reduced motion) are necessary and not sufficient
(`CLAUDE.md`, accessibility rules). Before go-live a person runs the passes below with NVDA and
Firefox on Windows and with VoiceOver and Safari on macOS and iOS, and records the result here.
This file starts with what each pass must check; results are added under *Results* with the date,
the tester, the versions and what was found.

## The call page: the chat beside the call (ADR 0123 point 8, step 7c; run in step 8)

Only a booking whose task is a conversation has the side bar. Run the pass on such a booking and
once on a booking without a chat (the page must be as before: no side bar, no chat sentences).

| # | Check | Expected |
|---|---|---|
| 1 | Reach the side bar by keyboard from the start of the page | Tab order: the page's content, then the side bar (heading, the log, the field, the send button), then the control bar; no trap; the log can be focused (it scrolls) and shows a visible focus ring |
| 2 | The heading and the landmark | The side bar is a complementary region named by its heading ("Chat with ..."); the heading is a level two |
| 3 | A message arrives while the focus is in the field | The reader says the author's label and the text once, politely, without moving focus or interrupting typing; the line the participant sent is read once, not twice |
| 4 | Open the page again (a reload) with a thread of ten messages | The history is not read out; the reader's place in the log is where the participant left it after scrolling up, and new messages do not pull the log down when the participant scrolled up |
| 5 | The field | It has a visible label; its description says Enter sends and Shift and Enter start a new line; the remaining characters are said in text and change as the participant types, without colour as the only signal, and the count is not announced on every key |
| 6 | Send with Enter, with the button, with Shift and Enter | Enter sends; Shift and Enter break the line; the button sends; focus stays in the field after a send |
| 7 | A send that fails (switch the network off) | The text stays in the field; the sentence that says so is linked to the field (`aria-describedby`), is read once and is visible as text; focus returns to the field |
| 8 | The state of each own message | "Sent", "Delivered" and "Not delivered" are words next to the message, read with it; a refusal is announced once |
| 9 | A closed chat | The field and the button are gone; a sentence says why (the call ended, the participant left, the staff member closed the chat) and is read once; if the field had focus, focus moves to the sentence; the thread stays readable |
| 10 | The refused-permission and failure states | The sentence that the chat stays available is read with the explanation; the side bar is reachable and usable in those states |
| 11 | 320 px and 400 % zoom | The side bar is below the content, nothing scrolls sideways, every target is at least 24 by 24 px |
| 12 | Forced colours, dark appearance, reduced motion | The messages and the closed sentence keep a border; the own messages are told apart by their label and not by colour; nothing moves |
| 13 | Markup typed in a message | It is shown as text, by the participant's reader too |
| 14 | A window under about 650 px high (a landscape phone, a browser with a large toolbar) at 64 rem wide or more | The side bar is cut to the window less the control bar and scrolls inside itself, so Send is never under the fixed bar (2.4.11); 320 px and 400 % zoom still stack the bar below the content |
| 15 | `prefers-contrast: more` | Borders and the muted time and state text stay readable (the side bar has no rule of its own for it: look) |
| 16 | The contrast of the muted time and state text in dark appearance | At least 4.5:1 against the message's background (`--booking-color-text-muted` on the surface and the accent tint) |
| 17 | The log as a scrolling region | It is a tab stop only while it scrolls, has a visible focus ring and a name, and its scroll target is at least 24 by 24 px; with a few messages it is no tab stop |
| 18 | A touch keyboard (iOS and Android) | The hint says Enter starts a new line and the button sends, there is no Enter-sends behaviour, and the screen reader reads the hint that is true |
| 19 | A staff name that arrives after messages were read | Lines already in the log are not read again; only the lines after it carry the name |
| 20 | The first read of a thread (a reload) and the moment the live region is switched back to polite (after half a second) | The history is not read out by NVDA or VoiceOver, and a message that arrives in that half second is still read |
| 21 | A session that ended or a chat that is gone (403, 404) | The sentence says so, the field is gone and focus is not lost; a retry is not offered |
| 22 | A closed chat for each reason (appointment over, the staff closed it, did not go ahead, participant left) | Each has its own sentence, read once |
| 23 | The date of a message that is not from today | The `time` element reads the date and the time in the page's language |

## Results

None yet: the call page is not enabled on a production installation until this file records the
passes (ADR 0123 point 18).
