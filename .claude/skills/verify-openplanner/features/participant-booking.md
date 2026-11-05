# Participant booking

A participant opens a group's booking page, picks a free day and time, fills in first name, last
name and email address, is asked to confirm by email, and confirms the appointment through the
link in that email (ADR 0056). The link trades its token for a cookie, so the address the
participant ends on holds no token (ADR 0093).

## Sub-features

- `book-page`: `/g/demo` shows the group's name and a month calendar with the free days.
- `book-day`: a free day shows its free times; with JavaScript they load in place and the address
  gets `?day=<day>`.
- `book-time`: a time opens the contact form "Uw gegevens" with the chosen moment beside it.
- `book-invalid`: an invalid email address brings the error summary "Er is een probleem" (role
  alert), marks the field `aria-invalid="true"` and keeps the typed names.
- `book-submit`: a valid form ends on "Bevestig uw afspraak" with the status "We hebben u een
  e-mail gestuurd"; the booking waits for confirmation and holds its place.
- `book-email`: the worker sends "Bevestig uw afspraak met Demo: videogesprek" with a
  `/b/<token>/confirm` link.
- `book-confirm`: that link opens `/b/m/<reference>/confirm`; the button "Afspraak bevestigen"
  confirms, the page says "Uw afspraak is bevestigd", and the worker sends "Bevestiging van uw
  videogesprek op …" with the management link and the calendar file `afspraak.ics`.

## How to get to it (user POV)

- The organisation's website links to the group page, here http://127.0.0.1:8010/g/demo.
- The same calendar, embedded on an allowed site as the Web Component (`docs/widget.md`; not
  driven here).
- The confirm link in the email "Bevestig uw afspraak met …".

## Driving it with journey.mjs

Preconditions:

- `stack.sh doctor` says `doctor: usable`.
- `/g/demo` shows at least one free day in the current month (weekday opening hours of the seed).
- The run's participant has made no booking yet on this stack (a fresh `up`).

- **Open the group page.** `page.goto('/g/demo')`. Heading level 1 `Demo: videogesprek` and the
  month calendar. Evidence `booking/01-group-page`.
- **Choose a day.** Click the first `a.calendar__day`; its `data-day` is the day. The address
  gets `?day=<that day>`, `#times` is no longer `aria-busy` and `#times .time-button` lists the
  times. `02-day-times`.
- **Choose a time.** Click the first `#times .time-button`; its `value` is the start in UTC, its
  text the local start (`09:00`). Heading `Uw gegevens`; "Gekozen moment" shows the slot
  (`09:00–09:30`). `03-form`.
- **Send an invalid email.** Fill label `Voornaam` and `Achternaam` with the run's participant
  and `E-mailadres` with `not-an-email`, click button `Afspraak maken`. A `role=alert` summary
  "Er is een probleem" appears and `E-mailadres` has `aria-invalid="true"`; the answer is a 422,
  recorded as an expected console error. `04-invalid-email`.
- **Book.** Fill `E-mailadres` with the participant's address, click `Afspraak maken`. Heading
  `Bevestig uw afspraak`, status contains `We hebben u een e-mail gestuurd`.
  `05-check-your-email`.
- **Get the confirmation request.** Mailpit, by the participant's address, until a new message
  with subject `^Bevestig uw afspraak met ` (within 90 seconds; the worker polls every 10). Its
  plain-text part holds `http://127.0.0.1:8010/b/<token>/confirm`.
  `06-confirm-request-email.json`.
- **Open the link.** `page.goto(<that link>)`. The address becomes `/b/m/<reference>/confirm`,
  without the token; heading `Afspraak bevestigen`. `07-confirm-page`.
- **Confirm.** Click button `Afspraak bevestigen`. Status `Uw afspraak is bevestigd` on
  `/b/m/<reference>`. `08-confirmed`.
- **Get the confirmation.** A new message with subject `^Bevestiging van uw videogesprek`, a
  `/b/<token>` link and the attachment `afspraak.ics` (`text/calendar`).
  `09-confirmation-email.json`. `state.json` now holds the booking: reference, day, start,
  time, status `confirmed`.
- **Proof.** The `booking/` folder and its lines in `journey.jsonl`; the `agenda` journey then
  finds the same booking from the staff side.

Command: `node .claude/skills/verify-openplanner/scripts/journey.mjs booking`

## Gotchas

- The emails come from the worker, not from web. When doctor shows the worker unhealthy, the
  journey times out at *Get the confirmation request*.
- Opening the confirm link does not confirm: only the button's POST does (ADR 0056 §8). A proof
  that stops at the confirm page has not confirmed anything.
- Use the link of this run's email. A token from `python -m app.cli seed-closures` belongs to a
  seeded booking and proves nothing about the booking just made; its output also holds tokens,
  so it never goes into the evidence.
- The journey takes the first free day of the month on screen and does not page to the next
  month. Late in a month, or with the opening hours changed, it may find none: it fails, and the
  reason belongs here, not in a weaker journey.
- An address may hold only a few bookings per group (ADR 0066) and bookings are rate-limited per
  address of the client; every run has a new participant address and a new stack process, so
  repeat journeys on one stack after a `down` and `up`, not by raising a limit.
- Without Webex the confirmation email has no join link and the agenda shows "Geen meeting";
  that is this stack, not a fault (`../SKILL.md`, *Stand-in versus a real installation*).
