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

## Results

None yet: the call page is not enabled on a production installation until this file records the
passes (ADR 0123 point 18).
