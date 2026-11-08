# OpenPlanner verification map

The maintained list of what the verify-openplanner skill proves about OpenPlanner as a
participant or staff member meets it. Read this page before driving the stack, then take the
feature file of the journey you need as its recipe. `../SKILL.md` says how to start, check and
remove the stack; this map says what to do on it and what counts as proof.

## Baseline preconditions

- The stack is up through `stack.sh up`: web on http://127.0.0.1:8010, Keycloak on
  http://127.0.0.1:8090, Mailpit on http://127.0.0.1:8035 (or the `VERIFY_*_PORT` values the run
  recorded in `stack.env`).
- It started from an empty database: web ran the migrations, `seed-demo` added organisation
  `Demo-acme` with group `demo` ("Demo: videogesprek", weekday opening hours, slots of 30
  minutes, two optional form fields) and organisation `andere` with group `andere-team`.
- `stack.sh doctor` ends with `doctor: usable`; above all `build` (the image is this checkout)
  and `instance` (the port answers this run's installation) are PASS.
- The e2e tooling is installed (`cd e2e && npm ci`) and Google Chrome is present, or
  `PLAYWRIGHT_CHROMIUM_CHANNEL` is set empty after `cd e2e && npx playwright install chromium`.
- Staff are the development users of `dev/keycloak/README.md`; this map uses `groupadmin`,
  `outsider` and `norole`. The participant is the run's made-up one in `stack.env`.
- Never drive a stack that `stack.sh up` of this checkout did not start.

## Driving conventions

- The harness is `scripts/journey.mjs`, one journey per feature; each journey starts from the
  state its *Preconditions* name, and `all` runs them in the order they depend on each other.
- The browser is Dutch (`nl-NL`), like the organisation default; the texts below are the Dutch
  catalogue texts, and `stack-client.mjs` names the catalogue key of each in its `NL` table.
- Handles, in this order of preference: a role with its accessible name (`button "Afspraak
  maken"`), a level 1 heading, a `role=status` notice, a route. CSS only where the markup has no
  role: `a.calendar__day[data-day]`, `#times .time-button[value]`, `#agenda-heading`, the profile
  menu `details.admin-profile > summary`, and Keycloak's `#username`, `#password`, `#kc-logout`.
- Email is read from the Mailpit API by recipient (`/api/v1/search?query=to:"<participant
  email>"`) and recognised by subject (`SUBJECT` in `stack-client.mjs`); the journey follows the link
  in the plain-text part, as the participant would.
- Changes stay in the run's database, which `stack.sh down` deletes; there is nothing to restore
  by hand between runs, so a new proof starts with a new `up`.

## Proof and skip reporting

- Every step leaves a screenshot and an ARIA snapshot in the journey's folder and a line in
  `journey.jsonl` with what it showed; the PR quotes the summary table and names the run folder.
- An email is proved by a JSON note: subject, recipient, links with the token blanked and the
  attachments, never the body.
- A change is proved from a second view as well: the email that follows it, and the admin agenda
  for the booking.
- Console errors are listed per journey; an expected one (an answer of 422 or 403 that the step
  asks for) is marked with its reason, any other is a finding.
- A journey whose precondition is not met fails with that reason. Report it as not verified;
  never report a feature as verified through a path other than the one its file names.
- Not driven by these journeys: the English texts (`?lang=en`), the widget, the keyboard-only
  flow and axe (those are `make e2e`), and everything that needs Webex or a contact center.

## Feature entry contract

A feature file opens with a level 1 title and one paragraph on what the user sees. Four level 2
sections follow, always in this order:

1. `Sub-features`: short ids, one line each, for the behaviours the feature consists of.
2. `How to get to it (user POV)`: every way a user reaches it.
3. `Driving it with journey.mjs`: `Preconditions:` first, then one bold-labelled bullet per user
   action with the handle the journey uses and the result it waits for, and the command.
4. `Gotchas`: what can waste a run or make its proof wrong.

The map names user paths, handles, state and proof; how the code does it belongs elsewhere.

## Features

- [Participant booking](./participant-booking.md): book on `/g/demo`, the invalid-email error,
  the confirmation request by email, confirming by its link, the confirmation email.
- [Manage a booking by the emailed link](./manage-booking.md): the management page, moving the
  appointment, cancelling it, and the email after each change.
- [Admin login through Keycloak](./admin-login.md): sign in, the dashboard of a group admin, log
  out at the identity provider, a user without a role.
- [Group agenda](./admin-agenda.md): the participant's booking as group staff see it, its
  cancelled state, and the refusal of an admin of another organisation.
