# 0057. Rebooking assistant: propose a new time and hold it until the citizen confirms

Status: proposed (2026-10-30), requested by the product owner, with the choices recorded under *Choices
made on acceptance*. Extends ADR 0044 (planner agenda, shortfalls, actions on bookings) and
reuses the pending-confirmation mechanism of ADR 0056.

## Context

When agents fall ill or something urgent takes them away, the planner lowers the staffing
(ADR 0027) and the agenda shows slots with a shortfall (ADR 0044 §2). Today the planner then
handles each booking by hand: move it (the planner picks a slot), ask the citizen to rebook
(the citizen picks a slot) or cancel it. With many bookings that is slow, and "ask to rebook"
pushes the whole search onto the citizen.

The product owner wants an assistant that thinks along: an algorithm that proposes a new time
for each booking that has to go, preferably the same time on another day; the citizen gets a
mail with its own subject and text saying the planning changed and a new slot was chosen, and
either confirms it or picks another slot. The proposed slot is held only for a limited time;
after that it is released. Holding several alternative slots per booking is out of scope.

## Decision

1. **A fifth action: propose a new time.** Next to move, ask to rebook, cancel and leave as is
   (ADR 0044 §4), under the same permission `bookings.manage`, the same limits (§6:
   `planner_change_min_lead_minutes`, not dispatched, not claimed) and the same fixed reasons.

2. **Which bookings move.** Per slot only the excess (confirmed and pending bookings minus
   agents), taking the **last-made** bookings first, as in the bulk action of ADR 0044 §5.
   Bookings the planner may not change are skipped and stay visible in the shortfall.

3. **Candidate slots.** A candidate is a slot start of the **same group** that is free after
   counting confirmed bookings, pending confirmations (ADR 0056) and proposals already made in
   this run; that lies outside the change deadline (`Group.change_deadline`) and within
   `reschedule_search_days` of the original start; that has the same duration; and that is not
   in a closure, holiday or zero-agent block. Earlier and later days are both allowed.

4. **Ranking**, a pure, deterministic function in `app/domain/rebooking.py` (no I/O, fully
   unit-tested and mutation-tested):
   1. same local time of day on another day before any other time;
   2. then the smallest difference in time of day, up to `reschedule_time_tolerance_minutes`;
   3. then the nearest day (absolute distance, earlier or later);
   4. then the earlier moment, so the result never depends on input order.
   Bookings are assigned one by one in the order of rule 2; each assignment takes a place from
   the candidate's remaining capacity, so two proposals never share the last place. A booking
   without a candidate falls back to "ask to rebook" (ADR 0044 §4.2).

5. **Review by the planner (default).** The assistant shows a confirmation page per shortfall
   slot, day or week: each booking with its reference, first name, old time and proposed time,
   or "no slot found → ask to rebook". The planner may change a proposed time to another free
   slot or untick a booking. Sending applies all rows in one transaction (write lock,
   `BEGIN IMMEDIATE`) and re-checks every capacity; rows that no longer fit are recomputed and
   shown again, never sent blind. Bulk sending needs a fresh login (ADR 0015) and counts toward
   `planner_changes_alert_per_hour`.

6. **Automatic per group (opt-in).** A group may set `reschedule_auto_send`. A staffing change
   that creates a shortfall then schedules a job `reschedule_proposals` after
   `reschedule_auto_delay_minutes`, so a typo in the staffing can be undone first. The job
   stores the staffing version it was scheduled for and exits silently if the staffing changed
   since or the shortfall is gone (the `pinned_start` pattern). When more than
   `reschedule_auto_max_bookings` bookings would be affected, it sends nothing and instead
   notifies the planners that a review is waiting (system event plus the agenda's summary).
   The actor in the audit is the system, with the staffing change that caused it as context.

7. **Holding the proposed slot.** In one transaction:
   - the original booking frees its place and gets status `awaiting_rebook` (ADR 0044), so the
     shortfall is gone at once;
   - a proposal row links it to the proposed slot and holds a place there, counted like a
     pending confirmation (ADR 0056), with a deadline of
     `min(now + reschedule_proposal_confirm_hours, change_deadline(proposed start))`;
   - the booking's jobs are replaced: one reminder `reschedule_proposal_reminder_hours`
     before the deadline (none if that moment has passed or the setting is 0) and an expiry
     job at the deadline, both with the pinned proposal id and no PII.

8. **The citizen's message.** A new message type `reschedule_proposal` with its own subject
   and text blocks (catalogue → organisation override → `group_text`, ADR 0009), the reason
   from the three fixed reasons, the old and the proposed time in the group's timezone, and
   three actions via the management token (ADR 0011):
   - **Confirm the new time**: a GET page with a POST button (mail scanners prefetch links,
     ADR 0056). Confirming moves the booking into the proposed slot through the same domain
     service as a reschedule: status `confirmed`, jobs, meeting and dispatch hooks redone, the
     usual confirmation with ICS.
   - **Choose another time**: the existing reschedule page, details pre-filled (WCAG 3.3.7),
     with the next two or three candidates of the ranking marked as *suggested*. They are not
     held. Choosing any slot releases the held proposal in the same transaction.
   - **Cancel**: releases the proposal and cancels the booking.
   Email always; SMS as well when the citizen consented (ADR 0022), with a short text and the
   link. A reminder uses the same message type with a reminder subject.

9. **After the deadline.** The expiry job releases the held place and the booking stays
   `awaiting_rebook`: the citizen can still choose a time via their link until the lapse of
   ADR 0044 (`planner_rebook_days` after the original start), with its one reminder and its
   lapse message. The citizen gets a short message that the proposed time was released and
   that they can still choose. Silence is never taken as consent.

10. **Planner overview.** The agenda's list *Awaiting a new time* (ADR 0044) shows proposals
    with the proposed time and the deadline. The planner can confirm on the citizen's behalf
    (audited, as in ADR 0056 after a phone call), withdraw the proposal (place released,
    booking stays `awaiting_rebook`) or cancel.

11. **Code home.** The algorithm in `app/domain/rebooking.py` (pure); the actions in
    `app/domain/booking_changes.py` next to move and ask-to-rebook; the proposal hold reuses
    the pending-confirmation counting of ADR 0056 instead of a second capacity rule (ADR 0005).
    The screen lives under organisation → group → *Agenda* tab (ADR 0047); no new tab.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `reschedule_proposal_confirm_hours` | D organisation | 48 | 4–168 | `settings.organisation.manage` | Never beyond the proposed slot's change deadline. |
| `reschedule_proposal_reminder_hours` | D organisation | 12 | 0–72, below the confirm hours | `settings.organisation.manage` | 0 = no reminder. |
| `reschedule_search_days` | D organisation | 14 | 1–60 | `settings.organisation.manage` | Distance from the original start, both directions. |
| `reschedule_time_tolerance_minutes` | D organisation | 60 | 0–240 | `settings.organisation.manage` | 0 = only the exact same time of day. |
| `reschedule_auto_send` | D organisation (per group) | off | on/off | `group.edit` | Send proposals without review. Off keeps the planner's review. |
| `reschedule_auto_delay_minutes` | D organisation (per group) | 15 | 5–120 | `group.edit` | Time to undo a staffing change before proposals go out. |
| `reschedule_auto_max_bookings` | D organisation (per group) | 10 | 1–100 | `group.edit` | Above this, automatic mode waits for a planner's review. |

**Fixed on purpose:** the citizen is always notified and must always confirm (silence is not
consent); the citizen can always choose another time; one held slot per booking (holding
several is out of scope); only the same group; no free-text reason (PII rule); the ranking
rules themselves (they must stay explainable to the citizen and the planner).

## Choices made on acceptance

The product owner chose on 2026-10-30, out of the options put to them:

- Proposals are reviewed by the planner by default; a group may switch to automatic sending
  (not: always automatic, not: review only).
- After the deadline the booking returns to "choose a time yourself" until the ADR 0044 lapse
  (not: cancel at once, not: silent acceptance).
- The last-made bookings move (not: the planner picks who moves).
- Proposals may fall on earlier and later days, nearest first (not: later only).

## Delivery

1. Domain: `rebooking.py` ranking and assignment, with property tests (no slot over capacity,
   deterministic, same group only).
2. Proposal hold, confirm/choose/cancel/expiry, message type and texts (nl/en), migration for
   the proposal table (reversible). Depends on ADR 0056 step 1.
3. Planner review page in the agenda, bulk send, overview in *Awaiting a new time*.
4. Automatic mode per group with delay, staffing-version pin and the booking ceiling.

## Consequences

- New i18n keys (nl and en) for the review page, the proposal message, its reminder and its
  expiry message; manual topics under `docs/manual/{nl,en}/` for the assistant.
- Threat model: mass proposals by a compromised planner account (fresh login, alert per
  hour); automatic mode triggered by a malicious staffing change (delay, ceiling, audit);
  proposals into another group's slot (refused, tested); token reuse after a proposal was
  released (confirm checks the proposal is still open).
- Data flow: no new PII; the proposal row holds booking id, slot, deadline and status only.
- `docs/architecture.md` gets `rebooking.py` and the proposal flow.
- Not in scope: holding several alternatives, planning per agent, moving to another group.
