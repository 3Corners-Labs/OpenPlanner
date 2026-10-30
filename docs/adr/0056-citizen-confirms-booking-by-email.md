# 0056. The citizen confirms a booking by email link

Status: accepted (2026-10-29) by the product owner, with the choices recorded under *Choices
made on acceptance*. Requested by the product owner. Builds on ADR 0011 (management tokens),
ADR 0022 (channels and consent), ADR 0042 (settings classes), ADR 0044 (planner, lapse of an
unanswered request) and ADR 0055 (booking form fields).

## Context

Today a booking is `confirmed` the moment the form is submitted. The first email carries the
calendar attachment, the join link and the management link, although nobody has checked that
the address belongs to the person who booked. A typo sends a stranger the appointment and its
join link, a fake address holds a place that nobody will use, and the contact center prepares
for a call that never happens.

The product owner wants the citizen to confirm the booking by clicking a link in the first
email. Confirming also verifies the email address. Only then the citizen gets the full
confirmation: the calendar file, the details they entered (so they can check them) and links
to cancel, reschedule or correct their details (everything except the email address). An
unconfirmed booking gets a reminder, and lapses after a period or a number of reminders.
Planners see that a booking is not confirmed yet.

## Decision

1. **New status `pending_confirmation`** (NL: *niet bevestigd*, EN: *not confirmed*). A
   scheduled booking made by a citizen starts in this status; `confirm(now)` turns it into
   `confirmed` and records `confirmed_at` (also the moment the email address was verified).
   `expire(now)` turns it into `cancelled` with `cancelled_at = confirm_by`, so a lapse is
   recognisable without a reason field (`lapsed_unconfirmed`: deadline set, never confirmed,
   cancelled at the deadline). A pending
   booking **holds its place** in the slot (it is in `HOLDS_PLACE`) but is **not active**: no
   video meeting, no reminders, no WxCC task or own-queue entry until it is confirmed.

2. **Emergency closure.** A closure (ADR 0024) does not suspend a pending booking; the booking
   keeps its deadline, so it either lapses or is confirmed and then suspended like any other.
   Settled in delivery step 2, where the closure service gets its test.

3. **Not for:** direct calls (ADR 0019, they start now), and bookings a planner moves or a
   citizen reschedules after confirming (the address is already verified; the new booking
   carries `confirmed_at` over). A reschedule of a pending booking is not offered.

4. **Confirmation window, depending on the start.** At booking, `confirm_by` is the earlier of
   `created_at + booking_confirm_window_hours` and `start − booking_confirm_cutoff_hours`.
   If that lies less than `booking_confirm_min_minutes` ahead (the appointment is soon), the
   booking still needs confirming, with `confirm_by = start − dispatch lead` (the change
   deadline, `Group.change_deadline`) and no reminder. If even that has passed, the booking cannot be made
   (`slot_unavailable`), as today for a slot past its deadline. `confirm_by` is stored on the
   booking and never recalculated.

5. **Reminders spread over the window.** `booking_confirm_reminders` reminders (0–3) go at
   evenly spaced moments between `created_at` and `confirm_by`: with one reminder, halfway.
   A reminder that would fall within `booking_confirm_min_minutes` of `confirm_by` is
   dropped. Email only: the point is to verify the address.

6. **Lapse.** At `confirm_by` the job `expire_unconfirmed` re-reads the booking and, if it is
   still pending, cancels it in one transaction: the place is freed, pending jobs are
   replaced, and the citizen gets a short email that the booking lapsed, with a link to the
   group page to book again. Personal data is purged `PURGE_AFTER` (24 h) later, as for any
   cancellation. Lapses are counted in the system log (no PII, ADR 0040).

7. **First email: ask to confirm, nothing more.** It shows group, date and time, a button
   *Confirm appointment* and a link *This was not me / cancel*. No calendar file, no join
   link, no name, phone or form answers: the address is not verified yet, so no personal data
   beyond the address itself goes to it.

8. **Confirming is a POST, never a GET.** The link opens `/b/{token}/confirm`, a page with the
   appointment and one button; only submitting it (POST, CSRF-protected like the other
   citizen forms) confirms. Mail scanners (Safe Links and similar) open links in advance; a
   GET that confirmed would verify every address automatically. The management token of
   ADR 0011 is the confirmation token: possession of the email is the proof. Confirming an
   already confirmed booking shows the booking page (idempotent); a lapsed or cancelled
   booking shows that it lapsed, with the link to book again.

9. **After confirming: the full confirmation.** The page and the email (`send_confirmation`,
   as today) carry the calendar file, an overview of the details entered (name, phone and
   the ADR 0055 form answers, for transparency and checking), and links to cancel,
   reschedule and **correct the details**. Only now the meeting is created and the reminders,
   WxCC task and purge are planned (`plan_for_booking`, unchanged).

10. **Correcting details.** On the management page the citizen can change name, phone and the
   group's form answers, with the same validation as the booking form (ADR 0055); the email
   address is shown but cannot be changed (a different address means a new booking). Allowed
   until the change deadline, like rescheduling. The change is audited without values, and
   when the WxCC task or own-queue entry already exists it is not updated (only the first name
   goes there, CLAUDE.md, and it was sent at dispatch). Built after ADR 0055 step 2 (name
   split, form answers stored).

11. **Planners.** Agenda, day view and booking detail show the status *Not confirmed* with
    `confirm_by` (text and icon, never colour alone); the agenda has a filter on it. Pending
    bookings count towards a slot's occupancy and its shortfall (ADR 0044), marked as such.
    With `bookings.manage` a planner can **confirm on the citizen's behalf** (for example when
    the citizen calls); this is audited (`booking.confirmed_by_staff`) and sends the same full
    confirmation email. Moving or cancelling a pending booking works as for a confirmed one;
    a moved pending booking stays pending with its own `confirm_by`.

12. **Abuse.** Pending bookings hold places, so a bot with fake addresses could block slots
    until they lapse. Mitigations: the rate limits of the booking form stay as they are, the
    window is bounded (at most 72 h), and a new abuse case in `docs/threat-model.md` ("slot
    exhaustion with unconfirmed bookings") gets a test. A confirmation token has the strength
    and storage of ADR 0011; no PII in the confirm URL.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `booking_confirm_window_hours` | D organisation | 24 | 1–72 | `settings.organisation.manage` | Latest confirmation after booking |
| `booking_confirm_cutoff_hours` | D organisation | 2 | 0–24 | `settings.organisation.manage` | Latest confirmation before the start |
| `booking_confirm_reminders` | D organisation | 1 | 0–3 | `settings.organisation.manage` | Evenly spread over the window |
| `booking_confirm_min_minutes` | C platform | 15 | 5–120 | `settings.platform.manage` | Below this the short-notice rule of point 4 applies; no reminder closer than this to `confirm_by` |

**No on/off switch.** Confirming verifies the address before personal data and a join link go
to it, and it frees places held with fake addresses: a protection, which per ADR 0042 is never
switched off from the portal. If an organisation needs it off, that is a new ADR (class B
kill switch at most).

**Fixed on purpose:** confirmation by email only (it verifies that address); confirmation
by POST only (point 8).

## Choices made on acceptance

- A pending booking holds its place until it lapses (not only after confirming).
- Defaults 24 h after booking / 2 h before the start / 1 reminder, class D per organisation.
- Short notice: still confirm, until the change deadline, without a reminder.
- A planner with `bookings.manage` may confirm on the citizen's behalf, audited.

## Delivery

1. **Domain:** status, `confirm`/`expire`, `confirm_by` and reminder moments, job plan
   (`send_confirm_request`, `send_confirm_reminder`, `expire_unconfirmed`,
   `send_unconfirmed_lapsed`), settings in the registry.
2. **Persistence and jobs:** migration (`confirm_by`, `confirmed_at`), adapter, worker
   handlers, emails (nl/en text blocks), confirm page and POST.
3. **Planner:** status in agenda, detail and filter; confirm on behalf; audit event.
4. **Correct details:** management page form for name, phone and form answers (after
   ADR 0055 step 2).

## Consequences

- Every new citizen booking costs one more click; a citizen who never reads the email loses
  the booking. The lapse email and the link to book again soften that.
- `docs/dataflow.md`: the first email no longer carries name or join link; the confirmed email
  carries the entered details. `docs/threat-model.md`: new abuse case (point 12), mail-scanner
  prefetch (point 8).
- Manual topics for the agenda (status, confirm on behalf) and the organisation settings.
- Reporting distinguishes bookings that lapsed unconfirmed from cancellations.
- Migration is reversible: down maps `pending_confirmation` to `cancelled` and drops the two
  columns.
