# 0066. Bookings per email address

Status: accepted (2026-10-30), requested by the product owner. Extends ADR 0027 and ADR 0056. Partly built; see `docs/adr-status.md` for what is built.

## Context

The capacity of a slot is safe: slot creation, the capacity check and the booking run in one
transaction under `BEGIN IMMEDIATE`, with a unique index on (group, start) as backstop. What is
not limited is how many places one person takes. A citizen can book the same slot twice (a
double click, a second tab) or book many slots, and every booking holds an agent. The only
brake is the rate limit of 10 bookings per IP address per hour.

A hard rule of one booking per citizen does not fit either: citizens often book on behalf of
someone else (a parent for a child, a carer for a relative), and many of those people have no
email address of their own. And a refusal that says "this address already has an appointment"
tells anyone who types someone else's address that this person has an appointment with this
team, for example debt counselling. That is personal data through an error message, an abuse
case in `docs/threat-model.md`.

## Decision

1. **The same email address twice in one slot is always refused** (`booking_limit_reached`).
   It is a double submit, never a booking on behalf of someone else. No setting.
2. **An email address may hold at most `max_bookings_per_email` future bookings per group**
   (default 3, 1 to 10). This leaves room to book for others but stops one person or bot from
   taking the agenda. It is a group setting on the group's *Settings* tab, next to *Book
   ahead*, because the right number depends on the team (a youth team sees more parents).
3. **What counts:** scheduled bookings of the same group whose status holds a place
   (`HOLDS_PLACE`: confirmed, suspended, and pending confirmation once ADR 0056 adds it) and
   that end after now. Cancelled, completed, awaiting rebook and direct calls do not count.
   Other groups do not count.
4. **Comparison:** the address as stored, compared without regard to ASCII case on both sides
   (`lower()` in SQLite). No normalisation of `+tags` or dots: that differs per provider and
   anyone who wants to bypass the limit can use another address anyway. The limit is a brake,
   not an identity check.
5. **Order:** the check runs after the capacity check in `BookingService._place`, inside the
   same locked transaction. A full slot is reported as full to everyone; two parallel
   bookings with the same address cannot both pass.
6. **Moves:** rescheduling cancels the old booking before the new one is placed, so a move
   never counts the booking it moves. A planner or a closure move into a slot where the same
   address already has a booking is refused with the same code.
7. **What the citizen sees:** one code for both rules, and a message that does not say an
   appointment exists: "We cannot make this appointment. Do you already have an appointment
   with this team? …". The adapter returns start times only; the address never leaves the
   query, and no log or audit event carries it.
8. **Later step, on top of ADR 0056 step 2:** once a booking first becomes *pending
   confirmation*, the page says "check your email" for every submission, and the refusal is
   explained in an email to that address only. Then the page reveals nothing at all.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `max_bookings_per_email` | D organisation (per group) | 3 | 1–10 | `group.edit` | Group column, like `queue_visible_from_minutes` (ADR 0043) |

No off switch: the limit is a protection (ADR 0042), so the bound is 10, not "unlimited".

**Fixed on purpose:** the same address twice in one slot is always refused; that has no
legitimate use. The case folding rule is fixed (ASCII, both sides).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1–7 | Per-email limit: same slot refused, maximum per group, setting, migration 0054 | #128 | merged |
| 8 | A refused booking is explained by email only (prerequisite ADR 0056 step 2 is merged) |  | planned |

(State as checked against the code on 2026-10-30; the overview of every ADR is `docs/adr-status.md`.)

## Consequences

- Migration 0054 adds `groups.max_bookings_per_email` (default 3, check 1–10); reversible.
- Residual risk until step 8: the difference between "booked" and "refused" still shows that
  an address has bookings in the group. The rate limit (10 per IP per hour) and the honeypot
  keep probing slow; step 8 removes the signal.
- The machine API (ADR 0023) and the widget (ADR 0035) use the same domain path and return
  `booking_limit_reached` with HTTP 409.
- Manual: *Appointments per email address* on the group settings topic (nl, en).
- Threat model: slot exhaustion and PII through error messages updated.
- Not solved here (see the investigation of 2026-10-30): direct calls and scheduled bookings
  sharing agents (ADR 0027), and agents in more than one group (O4).
