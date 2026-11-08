# Contact details on the booking page

Group staff open a booking's page from the agenda (**Details**) to trace a meeting or a task.
The page shows the participant's full name, email address and phone number only when they ask
for them with **Contactgegevens tonen**, never by default, and **Contactgegevens verbergen**
takes them off the screen again. Each request is a line in the audit log, without the details
themselves (ADR 0044 §2, the product owner's choice of 2026-11-08).

## Sub-features

- `contact-hidden-by-default`: the booking page has a card "Contactgegevens" with the button
  `Contactgegevens tonen` and none of the participant's values.
- `contact-shown`: after the button, the address carries `?contact=show` and the card shows the
  full name, the email address as a `mailto:` link and the phone number as a `tel:` link, or
  "Niet opgegeven" when the participant gave none.
- `contact-hide`: the link `Contactgegevens verbergen` opens the page without `?contact=` and
  without the values.
- `contact-audited`: `/admin/audit` lists "Contactgegevens van de deelnemer getoond" once per
  request, and none of the participant's values.

## How to get to it (user POV)

- Admin portal, Groepen, "Demo: videogesprek", tab `Agenda`, the link `Details` in the row of a
  booking; then the button `Contactgegevens tonen` in the card "Contactgegevens".
- The address `/admin/groups/demo/bookings/<id>`, and `?contact=show` for the request; a link to
  that address from another site opens the page without the details.

## Driving it with journey.mjs

Preconditions:

- A booking of this run in `state.json` (after `booking`), whose personal data have not been
  deleted yet.
- The development user `groupadmin` (`dev/keycloak/README.md`), and no earlier request for this
  run's booking in the audit log (a fresh `up`).

- **Open the booking page.** Sign in as `groupadmin` on the agenda day of the booking, find its
  row as in `admin-agenda.md`, and follow the row's link `Details`. The `region`
  "Contactgegevens" shows the button `Contactgegevens tonen`, and neither the address nor the
  last name is on the page. Evidence `contact/01-booking-page`.
- **Ask for the contact details.** Press the button. The address gets `?contact=show`; the card
  has the link named with the participant's address (`mailto:`), the text `<first> <last>`, and
  "Niet opgegeven", since the booking journey gives no phone number. `02-contact-shown`.
- **Hide them again.** Follow `Contactgegevens verbergen`. The address has no `?contact=` and
  the page no address. `03-contact-hidden`.
- **Find the request in the audit log.** Open `/admin/audit`. Exactly one `row` contains
  "Contactgegevens van de deelnemer getoond", and `main` holds none of the participant's values.
  `04-audit-event`.
- **Proof.** The screenshots mask the participant's values and the ARIA snapshots replace them
  by `<first_name>`, `<last_name>` and `<email>`; `journey.jsonl` names each observation.

Command: `node .claude/skills/verify-openplanner/scripts/journey.mjs booking contact`

## Gotchas

- Run it on a fresh stack: each request is an audit line, so a second run of `contact` on the
  same stack finds two and fails on "exactly one".
- The `tel:` link is not driven: the run's made-up participant has no phone number, because no
  fictitious number range exists that is safe to type. The HTTP tests
  (`tests/admin/test_call_routes.py`) cover the link.
- The request counts only from the portal's own pages or a typed address: a browser that marks
  the request as cross-site (`Sec-Fetch-Site`) gets the page without the details. The journey
  presses the button, so it is the portal's own.
- After the purge (24 hours after the end or the cancellation of the booking) the card says the
  personal data have been deleted and has no button; this stack never gets there within a run.
- Screenshot only this stack's booking pages: the made-up participant is the only one on it.
