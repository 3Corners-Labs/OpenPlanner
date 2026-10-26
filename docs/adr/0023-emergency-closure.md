# 0023. Emergency closure: appointments on hold, citizens informed

Status: accepted (2026-10-25), decided by the product owner. The retention exception needs
confirmation by the privacy officer in the DPIA (open point O7).

## Context

A team can become unavailable at short notice: an outage, illness of the whole team, a closed
building, a strike. Closing a date with an exception (`schedule_overrides`) stops new bookings,
but leaves confirmed appointments in place: reminders go out, WxCC tasks are created and nobody
answers. Citizens must know what is going on, and staff must be able to handle the affected
appointments quickly. The product owner asked that, during an emergency, neither appointments
nor personal data are deleted, so citizens do not have to book and fill in everything again.

## Decision

1. **Closure per group.** A `Closure` has a start (now or later), an optional end (open-ended
   until lifted) and a notice for citizens in Dutch and English (plain text, at most 1000
   characters, no templates or HTML; ADR 0009). At most one active or upcoming closure per
   group. While it is visible, the group page shows the notice; from its start no slot inside
   it is offered or booked (`group_closed`) and no direct call starts.
2. **Per organisation.** An organisation admin can close every group of the organisation at
   once (`organisation.closure`); groups that are already closed are left as they are. Each
   group gets its own closure, so it can be handled and lifted separately afterwards.
3. **Suspend, never delete.** Confirmed bookings inside the closure get status `suspended`:
   reminders and the WxCC task are removed (an already created work item is ended, as for a
   cancellation), the meeting stays (so a reinstated booking keeps
   its link), the place in the slot stays taken, and the citizen gets an email with the notice
   and the management link. A suspended booking has no change deadline: the citizen can choose
   a new time or cancel it, without entering their details again.
4. **Staff handle bookings in bulk or one by one** (`closure.manage`, also for group admins on
   their own groups): update the notice and the end and mail the update to everyone on hold;
   lift the closure, optionally reinstating what can still go ahead (reinstatement mail with
   the calendar entry); move one booking to a free slot (change mail, new link); reinstate or
   cancel one or all.
5. **Retention.** Instead of slot end + 24 h, the personal data of a booking that is still
   suspended is purged 30 days after the closure ends. While a closure is open-ended there is
   no purge job; it is planned as soon as the end is known (set or lifted). Once a suspended
   booking is moved, reinstated or cancelled, the normal rules apply again.
6. **Audit.** Every change is audited with machine keys and counts
   (`closure.start,bookings:3`), never notice texts or contact data.

## Consequences

- The retention exception is visible in `docs/dataflow.md` and must be confirmed in the DPIA.
  An open-ended closure keeps personal data until it is lifted; the closure page says so.
- A booking within its dispatch lead when the closure starts may already have a WxCC work
  item; it is ended like the work item of a cancelled booking (`end_wxcc_task`).
- Closure messages go by email; SMS (ADR 0022) is used for reminders only for now. The notice on the
  group page and the management page reaches those who do not read their mail.
- Downgrading migration 0014 turns suspended bookings into cancelled ones and purges them at
  once, so no personal data outlives the closure that kept it.
