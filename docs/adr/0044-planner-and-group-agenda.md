# 0044. Planner: group agenda with shortfalls and changes to existing bookings

Status: accepted (2026-10-28) by the product owner, with the choices recorded under
*Choices made on acceptance*. Requested by the product owner. Builds on ADR 0024 (emergency
closure: suspend, move, cancel), ADR 0027 (staffing as a count of agents), ADR 0039 (adjustable
roles) and ADR 0043 (dispatch kinds, own queue).

## Context

Staffing is a count of agents per block (ADR 0027), and a booking is never tied to an agent
(B5). When agents fall ill or are absent, the planner lowers the staffing. Bookings already
made stay as they are, so some slots now hold more bookings than there are agents. Nothing in
OpenPlanner shows that, and only the emergency closure (ADR 0024) can change existing bookings,
and only for a whole group or organisation at once.

The product owner wants a planner to open the group's agenda in OpenPlanner and change
existing bookings. OpenPlanner has no insight into agents' own calendars; that stays manual
(ADR 0043, open point O12). This works the same for both dispatch kinds of ADR 0043.

## Decision

1. **A permission, not a fixed role.** A new permission **`bookings.manage`** at group level,
   marked `pii`, requiring `bookings.view` and `group.view`. `group_admin` and `org_admin` hold
   it. The auditor never does (ADR 0039). A "Planner" (NL: *planner*) is a custom role per
   ADR 0039, for example `group.view`, `bookings.view`, `bookings.manage` and optionally
   `schedule.manage`. No new built-in role: needs differ per organisation.

2. **Group agenda.** A day view and a week view per group (`/admin/groups/{slug}/agenda`,
   `bookings.view`). Per slot: start, number of confirmed bookings, and the current agent count
   from the *effective* staffing (blocks, staffing exceptions, holidays and closures). A slot
   with more bookings than agents is a **shortfall**, shown with text and an icon, never colour
   alone. The day view lists the bookings per slot: reference, first name, status and, for the
   own queue (ADR 0043), claimed or not. Email and phone show only on the booking's detail
   page, as today with `bookings.view`. Server-rendered, no JavaScript (ADR 0016); the week
   view is a table with row and column headers.

3. **Shortfall summary.** The group's overview and the dashboard show the number of slots with
   a shortfall in the next `shortfall_horizon_days` days, linking to the first one. After a
   staffing change that causes a shortfall, the confirmation page says so and links to the
   agenda.

4. **Actions on one booking** (`bookings.manage`), each in one transaction, each audited with
   actor, group, booking reference and action, without PII:
   1. **Move** to another slot of the same group with room. It uses the same domain service as
      the citizen's reschedule (new booking in the new slot, old one cancelled, jobs and
      dispatch hooks redone, ADR 0043). The citizen gets the change mail with the new time and
      can still change it via their own link.
   2. **Ask the citizen to rebook.** The booking gets its own status `awaiting_rebook` (not
      the closure's `suspended`): reminders, dispatch and the meeting lapse and, unlike a
      closure (ADR 0024), **its place in the slot is freed**, so the shortfall is gone at once. The citizen gets a message with a reason from
      a fixed list and a link to choose a new time without entering their details again. If
      the citizen chooses nothing, the booking lapses (is cancelled)
      `planner_rebook_days` after its original start, the citizen gets a short message that
      it lapsed, and its personal data is purged 24 h later. One reminder goes
      `planner_rebook_reminder_days` before the lapse. Such bookings appear in the agenda's
      own list *Awaiting a new time* (`bookings.manage`), with move and cancel; reinstating is
      not offered, because the place is no longer reserved.
   3. **Cancel** with a reason from the same fixed list. The citizen gets the cancellation
      message with that reason.
   4. **Leave as is.** The planner marks the shortfall as accepted for that slot (for example
      when a colleague from another team helps out). The mark is audited and disappears when
      the staffing or the bookings of that slot change.

   The reasons are three i18n text blocks with organisation overrides (ADR 0009, 0004), never
   free text, so no PII ends up in a message through a planner's typing: *not enough agents
   available*, *technical failure* and *unforeseen circumstances*. The citizen is always told,
   through the existing channels: email always, SMS as well when the citizen consented
   (ADR 0022). That is a protection and not a setting.

5. **Bulk for one slot.** In a slot with a shortfall the planner can apply "ask to rebook" to
   the *last-made* bookings until the shortfall is gone, in one action, after a confirmation
   page that lists the bookings concerned.

6. **Limits.** A planner cannot change a booking that starts within
   `planner_change_min_lead_minutes`, nor one already dispatched to the contact center
   (`dispatch_lead_minutes`, as for the citizen) or claimed in the own queue; those need a
   phone call, not a mail. Moving into another group's slot is refused (abuse case of HANDOVER
   §12). A move may not create a new shortfall unless the planner confirms it explicitly.

7. **Code home.** The suspend, move and cancel steps of `app/domain/closure_service.py` move to
   a shared `app/domain/booking_changes.py` used by both the closure and the planner (ADR 0005:
   extract at the second use). The closure keeps its own entry points.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `planner_change_min_lead_minutes` | D organisation (per group) | 60 | 0–1440 | `group.edit` | Not below `dispatch_lead_minutes`; validation enforces it. |
| `shortfall_horizon_days` | D organisation | 14 | 1–60 | `settings.organisation.manage` | How far ahead the shortfall summary looks. |
| `planner_rebook_days` | D organisation | 14 | 1–30 | `settings.organisation.manage` | Days after the original start after which an unanswered request to rebook lapses. |
| `planner_rebook_reminder_days` | D organisation | 3 | 1–29, below `planner_rebook_days` | `settings.organisation.manage` | Days before the lapse at which the one reminder goes. |
| `planner_changes_alert_per_hour` | C platform | 50 | 5–1000 | `settings.platform.manage` | Changes by one actor per hour above which a system event warns (abuse case). Raises an alert only; never blocks. |

Change reasons are texts, edited under `texts.manage` (ADR 0009), not settings.

**Fixed on purpose:** the citizen is always notified of a planner's change (protection); no
free-text reason (PII rule); shortfall is computed, not configured.

## Choices made on acceptance

The product owner chose on 2026-10-28, out of the options put to them:

- `bookings.manage` is held by `group_admin` and `org_admin` by default (not only the
  organisation admin, not only custom roles).
- "Ask to rebook" frees the place in the slot; an unanswered request lapses after
  `planner_rebook_days` (the alternative, keeping the place as in a closure, would leave the
  shortfall in place until the citizen acts).
- Three fixed reasons (above), not one general reason.
- The citizen is informed through the existing channels (email, and SMS with consent), not by
  email only.

For step 3 the product owner chose on 2026-10-29:

- A citizen whose request lapses gets a short message (not a silent lapse).
- One reminder before the lapse (`planner_rebook_reminder_days`), not none.
- Planners see the bookings awaiting a new time in a list in the agenda (`bookings.manage`),
  not in the closure's list of bookings on hold (which needs `closure.manage`).

## Delivery

1. Agenda with agents and shortfalls, read only: day view per slot, week view, the shortfall
   summary on the group pages and the dashboard (`shortfall_horizon_days`).
2. `bookings.manage` and the actions on one booking (move, cancel, leave as is), with the
   shared `booking_changes` module.
3. Ask to rebook, one at a time and in bulk per slot.

## Consequences

- New i18n keys (nl and en) for the agenda, the actions, the reasons and the citizen mails for
  "ask to rebook" and cancellation with a reason.
- Authorisation: `bookings.manage` in the matrix, the expectation table and
  `docs/authorization.md`; tests for every role × agenda/action route × own/other group.
- Abuse cases in `docs/threat-model.md`: moving into another group's slot; mass cancellation
  by a compromised planner account (fresh login for bulk actions, ADR 0015, and a system event
  above `planner_changes_alert_per_hour`); booking ids guessed in action URLs.
- `docs/manual/{nl,en}/` topics for the agenda and each action; the agenda pages get a
  Playwright test with axe in both locales (`e2e/tests/admin-agenda.spec.ts`;
  `e2e/pages.json` lists public pages only, admin pages need a login).
- `docs/architecture.md` gets the shared `booking_changes` module.
- Not in scope: planning per agent, or availability from agents' own calendars (O12).
