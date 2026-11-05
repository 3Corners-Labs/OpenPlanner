# Group agenda

Group staff read the day's appointments of their group in the admin portal: per time slot the
staffing and the bookings, each with the participant's first name and the state of its video
meeting and contact-center task (ADR 0044). A booking a participant just made shows up here, a
cancelled one carries the label "Geannuleerd", and staff of another organisation are refused
(ADR 0014).

## Sub-features

- `agenda-day`: `/admin/groups/demo/agenda?day=<day>` shows the day as heading and one row group
  per time slot ("09:00–09:30 Medewerkers: 2, afspraken: 1"), with a row per booking.
- `agenda-confirmed`: a confirmed booking of this run is one row, whose header starts with its
  start time, with the participant's first name and without "Geannuleerd".
- `agenda-cancelled`: after cancelling, that row carries the label "Geannuleerd".
- `agenda-refused`: `outsider`, admin of organisation `andere`, gets a 403 "Geen toegang" on
  the demo agenda.

## How to get to it (user POV)

- Admin portal, Groepen, "Demo: videogesprek", tab `Agenda`; then the links `Vorige dag`,
  `Vandaag`, `Volgende dag` and `Weekoverzicht`.
- The address `/admin/groups/demo/agenda?day=YYYY-MM-DD`.

## Driving it with journey.mjs

Preconditions:

- A booking of this run in `state.json`: after `booking` the confirmed one, after `manage` the
  moved one, cancelled.
- The development users `groupadmin` and `outsider` (`dev/keycloak/README.md`).

- **Sign in as group admin.** Sign in as `groupadmin` with the agenda address of the booking's
  day, then open that address again, since the login keeps only the path.
- **Find the booking.** `#agenda-heading` shows the day. Exactly one `row` contains the
  participant's first name and has a `th` that starts with `<start>–` (`09:00–`).
- **Check its state.** The label `Geannuleerd` is in that row exactly when `state.json` says
  `cancelled`. Evidence `agenda/01-agenda-confirmed`, or `agenda-2/01-agenda-cancelled` for the
  second run in `all`.
- **Try it as another organisation.** In a new browser context sign in as `outsider` and open
  the same address. Status 403, heading `Geen toegang`. `02-outsider-refused`.
- **Proof.** The ARIA snapshot holds the row with `<first_name>` in place of the name and the
  screenshot masks it; `journey.jsonl` names the time and the state found.

Command: `node .claude/skills/verify-openplanner/scripts/journey.mjs agenda`

## Gotchas

- The row header is a range (`09:30–10:00`). Match the start with `^<start>–`: after a move, the
  old booking's row may end at the new start time, and it is "Geannuleerd" too.
- The day is the group's own (Europe/Amsterdam). The journey takes it from the calendar's
  `data-day`, which is already local; a day computed from the UTC start can be the wrong one
  around midnight.
- Rows show "Geen meeting" and "Nog geen taak" because this stack has no Webex and no contact
  center; on a real installation those cells change during the call.
- The agenda refreshes itself through `live-region.js`; the journey reads the table as it was
  served and does not wait for a refresh.
- The first name is personal data on a real booking. Screenshot only this stack's agenda, where
  every booking is the run's made-up participant; never a test host's or production's.
