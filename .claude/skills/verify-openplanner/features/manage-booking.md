# Manage a booking by the emailed link

The participant opens the personal link from an email, sees the appointment on "Uw afspraak",
moves it to another time after confirming the new moment, and cancels it. Each change ends on a
notice on the page and sends an email. No account is involved: the link's token is traded for a
cookie (ADR 0093).

## Sub-features

- `manage-open`: the plain `/b/<token>` link lands on `/b/m/<reference>` with heading "Uw
  afspraak": when, team and status.
- `manage-move`: "Afspraak verplaatsen" shows the slot picker; a time leads to "Nieuw moment
  bevestigen" (from and to); "Verplaatsen" moves it, the page says "Uw afspraak is verplaatst"
  and the booking has a new reference.
- `manage-move-email`: the worker sends "Uw videogesprek is verplaatst naar …" with a new
  management link and calendar file.
- `manage-cancel`: "Afspraak annuleren", then "Ja, annuleren"; the page says "Uw afspraak is
  geannuleerd".
- `manage-cancel-email`: the worker sends "Uw videogesprek op … is geannuleerd" with a calendar
  file (`text/calendar`).

## How to get to it (user POV)

- The link in the confirmation, change or reminder email ("Bekijken of wijzigen", "Wijzigen of
  annuleren").
- The same link in the description of the calendar invite (not driven here).
- From the management page itself: the links `Afspraak verplaatsen` and `Afspraak annuleren`.

## Driving it with journey.mjs

Preconditions:

- A confirmed booking of this run: the `booking` journey passed and `state.json` says
  `confirmed`.
- Its confirmation email is in Mailpit.
- The appointment starts more than an hour from now, so it may still be moved (ADR 0069).

- **Open the link.** Take the newest message with subject `^Bevestiging van uw videogesprek`
  and its plain `/b/<token>` link (not `/join` or `/test`); `page.goto(<link>)`. The address is
  `/b/m/<reference>` without the token; heading `Uw afspraak`. Evidence `manage/01-manage-page`.
- **Start moving.** Click link `Afspraak verplaatsen`. Heading `Afspraak verplaatsen` and the
  slot picker. `02-reschedule-page`.
- **Choose a new time.** Click the first `a.calendar__day`, wait for its times, then the first
  `#times .time-button` whose `value` is not the booked start. Heading `Nieuw moment bevestigen`
  with the old and the new moment. `03-reschedule-confirm`.
- **Move.** Click button `Verplaatsen`. Status `Uw afspraak is verplaatst`; the address is
  `/b/m/<new reference>`. `04-moved`.
- **Get the change email.** A new message with subject `^Uw videogesprek is verplaatst`.
  `05-change-email.json`.
- **Start cancelling.** Click link `Afspraak annuleren`. Heading `Afspraak annuleren`.
  `06-cancel-page`.
- **Cancel.** Click button `Ja, annuleren`. Status `Uw afspraak is geannuleerd`. `07-cancelled`.
- **Get the cancellation email.** A new message with subject ending `is geannuleerd`.
  `08-cancellation-email.json`. `state.json` now holds the moved booking with status
  `cancelled`.
- **Proof.** The `manage/` folder; then the `agenda` journey shows the cancelled booking from the
  staff side.

Command: `node .claude/skills/verify-openplanner/scripts/journey.mjs manage`

## Gotchas

- A move cancels the booking and makes a new one with its own reference: the address changes,
  the agenda shows the old time as "Geannuleerd" as well, and later checks must follow the new
  reference (the journey stores it in `state.json`).
- Moving closes 60 minutes before the start, cancelling at the start (ADR 0069), and the page is
  read-only from the dispatch moment. A booking too close to now shows no move link: the journey
  fails at *Start moving*; book a later time rather than changing the deadline.
- The cookie that the link sets belongs to one browser context. A new context, or another
  journey, needs the link again; the journey opens it every time.
- The link stays valid after use, until the booking's data is deleted after the appointment.
  Treat it as a password: never paste it in chat, a log or the evidence.
- The cancellation email and its calendar file are the only signal the participant gets; check
  both, not only the page's notice.
