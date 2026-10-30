# 0069. The citizen cancels until the start and moves until a lead per group

Status: accepted (2026-10-30), requested by the product owner. Amends ADR 0043 (change
deadline per kind) and the read-only rule of ADR 0017 and 0021. Settings per ADR 0042.

## Context

A citizen of a group on the own queue could not change or cancel a Monday 09:30 appointment
from the Friday before, and saw "the call starts soon". The cause was ADR 0043: one working
day before the start, at the same local time. The text was written for the contact-center
case, where the lock is the dispatch moment five minutes before the start, and was wrong for
the own queue. The product owner found that rule too strict: it is not logical to lose the
control to cancel or move days ahead of the call, and a no-show costs the team more than a
late cancellation.

What the code already supports:

- Cancelling after dispatch works: the work item gets a "cancelled" message and an end
  request, the meeting is deleted (ADR 0021), and the team's calendar invite is withdrawn
  with a `CANCEL` (ADR 0043).
- Moving is a cancel plus a new booking in one transaction (`BookingService.relocate`); the
  new booking needs time to be dispatched again, so a move cannot be last-minute.
- A planner already has their own lead (ADR 0044, `planner_change_min_lead_minutes`).

## Decision

1. **Cancel until the start.** A citizen can cancel a confirmed booking from its management
   page until its start, for both dispatch kinds. From the start on `booking_locked` applies.
2. **Move until a lead.** A citizen can move a booking until `citizen_change_lead_minutes`
   before its start, for both dispatch kinds. The lead is a setting per group, default 60
   minutes, 0 to 1440, never shorter than `dispatch_lead_minutes` (a moved booking must be
   dispatched again).
3. **`queue_change_working_days` is removed.** Own-queue groups use the same rule as
   contact-center groups. `Group.change_deadline` returns the move deadline and the new
   `Group.cancel_deadline` the cancel deadline. `working_days_before` goes.
4. **The management page says what is still possible.** Before the move deadline: move and
   cancel, with both deadlines in the text. After it and before the start: a notice that
   moving is closed and a cancel link. After the start: a notice that the call has begun. The
   page shows no deadline text that does not match the rule.
5. **A booking on hold** (emergency closure, ADR 0024, or awaiting a new time, ADR 0044) keeps
   having no deadline for the citizen. Reinstating a suspended booking by an admin is refused
   from the dispatch moment on (not from the new move lead), as before.
6. **Dispatch does not change.** The WxCC task is still created `dispatch_lead_minutes`
   before the start. A cancellation after it relies on ADR 0021; a booking cancelled in the
   last minutes may leave a task that the end request closes asynchronously. The agent sees
   "cancelled" on the task.
7. **Migration 0062** adds `citizen_change_lead_minutes` (default 60; the dispatch lead for
   groups whose dispatch lead is longer than 60) and drops `queue_change_working_days`. The
   downgrade restores the working days at 1.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `citizen_change_lead_minutes` | D organisation (per group) | 60 | `dispatch_lead_minutes`–1440 | `group.edit` | How long before the start the citizen can still move a booking. Replaces `queue_change_working_days` (class D, 1–5). |

**Fixed on purpose:** the citizen can cancel until the start. It is a rule of the service, not
a tunable value: shortening it brings back the problem this ADR solves. If a group needs to
stop late cancellations, that is a policy for the organisation to decide with a new ADR.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | The citizen cancels until the start and moves until a lead (migration 0062) | #151 | merged |

(State as checked against the code on 2026-10-30; the overview of every ADR is `docs/adr-status.md`.)

## Consequences

- A team can have a task or calendar entry withdrawn shortly before the start. The cancel
  path was already built for planner changes and closures; the runbook says what the agent
  sees.
- Fewer no-shows are expected, since a citizen who cannot come has a way to say so.
- Mails that name a deadline for a change (`{deadline}` of the rebooking mails excepted) now
  name the move deadline.
- Manual topic `group-settings` (nl, en), architecture text, runbook and threat model row are
  updated in the same PR; ADR 0043's *Change deadline per kind* is superseded by this ADR.
- Threat model: the abuse case "cancelling after the task is in the WxCC queue" no longer
  blocks; the dead-appointment risk is covered by the cancellation job (ADR 0021) and the
  test `test_booking_can_be_cancelled_after_the_change_lead_and_dispatch`.
