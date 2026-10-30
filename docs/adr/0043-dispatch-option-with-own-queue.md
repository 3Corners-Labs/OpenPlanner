# 0043. Dispatch option per group: contact center or OpenPlanner's own queue

Status: accepted (2026-10-28), decided by the product owner with the amendments under
*Acceptance* below; built without ADR 0032. Extends ADR 0032 (provider-neutral
contact center) and ADR 0039 (adjustable roles). Builds on ADR 0010 and 0017 (meeting status
from Webex webhooks), ADR 0019 and 0026 (direct calls), and ADR 0042 (settings).

## Context

Today every booking reaches staff in one way: at T-5 (`dispatch_lead_minutes`) OpenPlanner
creates a task in the Webex Contact Center queue of the group, and the first free agent picks it
up (ADR 0007, 0018). Not every team works from a contact-center queue. Some teams handle video
appointments from a calendar, the way they handle a regular meeting. The product owner wants a
second way for those teams, with three constraints:

- **The group keeps its staffing as a count** of agents per block (ADR 0027). There is no
  schedule per agent and no link to agents' own calendars (Exchange, Google, HCL Notes): that
  is out of reach for now (see Consequences).
- **The citizen always gets the first available agent**, never a chosen one.
- **The citizen never sees an agent's email address.**

A calendar invite on its own does not answer "who takes which appointment?". With three agents
on a block and three bookings in the same slot, a shared calendar shows three equal entries and
the team has to agree among themselves. The contact-center queue solves exactly that. So the
second way needs a minimal queue of its own: agents sign in to OpenPlanner and pick up a waiting
appointment.

More contact-center platforms may follow (ADR 0032). The answer must not be a WxCC special case.

## Decision

### Dispatch kinds

1. **Every group has one dispatch kind.** The domain defines `DispatchKind`:
   - `contact_center`: the organisation's contact-center connection of ADR 0032 (`webex_cc`
     today, more platforms later). Behaviour unchanged. This is the default, so existing groups
     do not change.
   - `openplanner_queue`: OpenPlanner's own queue, described below.

   ADR 0032's `ContactCenterKind` stays the choice *within* `contact_center`. A group with
   `contact_center` needs an organisation connection; validation refuses the choice otherwise.

2. **One dispatcher per kind, with lifecycle hooks.** A new port `BookingDispatcher` in
   `app/domain/ports.py` has four hooks, each a no-op by default:
   `on_booked`, `on_rescheduled`, `on_cancelled` and `at_lead_time`. The application holds
   `dict[DispatchKind, BookingDispatcher]`, as it does per `ChannelKind` (ADR 0009) and per
   `ContactCenterKind` (ADR 0032). The contact-center dispatcher implements `at_lead_time`
   (create the task) and `on_cancelled` (end it, ADR 0021) by delegating to ADR 0032's
   `ContactCenterDispatcher`. The queue dispatcher implements the hooks for the calendar
   invite (point 9). Jobs, booking services and admin templates contain no
   `if kind == ...`.

3. **Changing the kind** of a group applies to bookings whose lead time has not passed yet:
   their pending `dispatch_contact_center_task` jobs are removed in the same transaction (or
   created, for the other direction). A booking already dispatched keeps its task. The change
   is audited. This also gives a fallback when Webex Connect or WxCC is down: the group admin
   switches the group to `openplanner_queue` (runbook).

### The own queue

4. **Who works the queue.** A new permission **`queue.work`** at group level, marked `pii`
   (agents see the citizen's first name, ADR 0039), requiring `group.view`. Agents sign in with
   the existing OIDC login (ADR 0013); their groups come from the group claim as for group
   admins. There is no user table (B8). A built-in role **`agent`** (NL: *medewerker*) holds
   `dashboard.view`, `rights.view`, `matrix.view`, `group.view` and `queue.work`, with reach
   `groups`. `group_admin` and `org_admin` hold `queue.work` too. The IdP role value for
   `agent` is part of open point O3.

5. **What the queue shows.** Per group the agent may work: the confirmed bookings of today
   that start within `queue_visible_from_minutes` from now, plus earlier ones not yet ended.
   Per row: start time, first name, status, and, from the Webex webhooks (ADR 0010, 0017),
   **"waiting in the lobby"** when the citizen has joined. Never the email address, phone
   number or note. Direct calls of a group with `openplanner_queue` (ADR 0019) appear in the
   same list, marked as such. The page is server-rendered and refreshes with HTMX polling
   (ADR 0016) every `queue_refresh_seconds`. New and waiting rows are announced in an
   `aria-live="polite"` region; the list also works without the refresh (reload).

6. **Picking up is a claim, not routing.** The default action is one button,
   **"Volgende oppakken" / "Pick up next"**: it claims the unclaimed booking whose citizen has
   waited longest in the lobby, else the earliest start. Claiming a specific row is allowed
   only when the group's `queue_free_pick_allowed` is on, to limit cherry picking. A claim:
   - runs in one transaction with a write lock (`BEGIN IMMEDIATE`); the second agent gets
     "Al opgepakt door een collega" / "Already picked up by a colleague" and the next row;
   - stores `claimed_at` and `claimed_by` on the booking. `claimed_by` is a pseudonym, the
     HMAC-SHA256 of the OIDC `sub` under a key derived from `MANAGEMENT_TOKEN_KEY` with its own
     label (`openplanner/agent-pseudonym/v1`, HKDF-SHA256 in `app/keys.py`), so the token key
     itself is never used for a second purpose. It is not a name or email address and is not a
     PII field of the citizen. It is removed by `purge_pii` with the citizen's data;
   - shows the agent the meeting's host link only now. Before the claim no agent sees it;
   - writes an audit event (actor, group, booking reference, `queue.claimed`).

   B5 still holds: no agent is recorded at booking time. The claim records who picked up, as
   the contact center does at T-5.

7. **Release.** The agent who claimed a booking can release it (for example after a technical
   failure). It returns to the top of the queue, `claimed_*` is cleared and the release is
   audited. A group admin can release any claim of the group.

8. **Not picked up.** A booking with `openplanner_queue` that is still unclaimed
   `not_picked_up_minutes` after its start (the existing per-group setting, ADR 0019) creates a
   system event (ADR 0031) and shows as "not picked up" on the dashboard and in the planner's
   agenda (ADR 0044). What the citizen is told stays open point O2, as for the contact center.

   Out of scope on purpose: agent availability states, skills, routing rules, wrap-up codes,
   statistics per agent. Those belong to a contact center. If a team needs them, the group
   uses `contact_center`.

### Optional calendar invite

9. **An invite to a group address.** A group with `openplanner_queue` may set a
   `calendar_invite_address`: a shared mailbox or distribution list of the team, such as
   `team-aangifte@organisatie.nl`. The queue dispatcher then sends an iCalendar invite by email
   (ADR 0009, the email `MessageSender`):
   - `on_booked`: `METHOD:REQUEST`; `on_rescheduled`: `METHOD:CANCEL` for the old booking and
     `REQUEST` for the new one (a reschedule is a new booking, HANDOVER §3); `on_cancelled` and
     suspension (ADR 0024): `METHOD:CANCEL`. The UID derives from the booking id; `SEQUENCE`
     rises with each change.
   - `ORGANIZER` is the organisation's sender address, `ATTENDEE` the group address with
     `RSVP=FALSE`. The citizen's mail is unchanged and never contains the group address.
   - Content: group name, start and end, the citizen's first name, the booking reference and a
     link to the group's queue in OpenPlanner. **Not** the host link: an agent gets it by
     claiming, so two agents cannot both start the same meeting unannounced.
   - Subject and description are fixed layouts with i18n text blocks and allow-listed
     placeholders (ADR 0009), in the organisation's default locale.

   We send our own invite instead of Webex's meeting invitations: no extra address goes to
   Webex, and update and cancel stay in our hands.

10. **The meeting stays as it is.** The Meetings API schedules the meeting on the group's
    licensed host with `allowAnyUserToBeCoHost` (ADR 0017). Every signed-in user of the
    organisation becomes cohost on joining, so no licence per agent is needed. The citizen still
    joins through `/b/{token}/join`.

### Data

11. **Schema.** `groups.dispatch_kind` (default `contact_center`),
    `groups.calendar_invite_address` (nullable), `bookings.claimed_at` and
    `bookings.claimed_by` (both nullable). One reversible Alembic migration.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `dispatch_kind` | D organisation (per group) | `contact_center` | `contact_center`, `openplanner_queue` | `group.edit` | `contact_center` requires an organisation connection (ADR 0032). |
| `calendar_invite_address` | D organisation (per group) | empty (no invite) | valid email address, max. 254 | `group.edit` | Only with `openplanner_queue`. An external address, so audited like other group fields. |
| `queue_visible_from_minutes` | D organisation (per group) | 15 | 0–120 | `group.edit` | How long before the start a booking appears in the queue. |
| `queue_free_pick_allowed` | D organisation (per group) | off | on/off | `group.edit` | Off: only "pick up next". Not a protection, a working agreement. |
| `queue_change_working_days` | D organisation (per group) | 1 | 1–5 | `group.edit` | Own queue only: the citizen can change or cancel until this many working days (Mon–Fri) before the start, same local time. Added at acceptance. |
| `queue_refresh_seconds` | C platform | 10 | 5–60 | `settings.platform.manage` | HTMX polling interval; bounded to protect the server. |

Unchanged and reused: `not_picked_up_minutes` (per group, 1–120, default 10) and
`dispatch_lead_minutes` (per group, 0–60, default 5; for `contact_center` only).

**Fixed on purpose:** the claim is atomic and the host link is shown only after a claim
(protections, never switchable); the pseudonym of `claimed_by` uses HMAC-SHA256 with an
HKDF-derived key (crypto parameters); the queue never shows email, phone or note (PII rule).

## Acceptance (2026-10-28)

The product owner accepted this ADR with these choices, so it can be built before ADR 0032:

- **On today's dispatcher.** `contact_center` means the current WxCC integration: a group
  dispatches when its WxCC fields are filled in (`Group.dispatch_enabled`), and that property is
  false for `openplanner_queue`. There is no organisation connection yet, so point 1's
  validation ("needs an organisation connection") waits for ADR 0032; until then a
  `contact_center` group without WxCC fields dispatches nothing, as today. Job types keep their
  names (`dispatch_wxcc_task`, `end_wxcc_task`); ADR 0032 renames them.
- **Changing the kind (point 3) needs no job juggling.** The dispatch job stays planned for
  every booking and does nothing for an own-queue group, because the dispatcher checks the
  group's kind when the job runs. Switching either way therefore applies to every booking
  whose job has not run yet, and a booking already dispatched keeps its task. The change is
  audited as a `group.edit` with `dispatch_kind` in the detail.
- **The `BookingDispatcher` port (point 2)** arrives with the calendar invite (point 9), its
  first hooks that do something beyond today's WxCC calls (ADR 0005: extract at the second
  use).
- **Change deadline per kind** (superseded by ADR 0068: both kinds cancel until the start and
  move until `citizen_change_lead_minutes`). Contact center: unchanged, the dispatch moment. Own queue:
  `queue_change_working_days` working days before the start, at the same local time (product
  owner: "1 werkdag van te voren"). Working days are Monday to Friday; public holidays are not
  skipped (an assumption, recorded in the PR). A booking made later than that is read-only
  from the start; the citizen can still phone the team.
- **`claimed_by` stays the HMAC pseudonym** of point 6, not a reference to the staff
  directory of ADR 0046. Agents of the own queue are portal users, which ADR 0046's
  assumption "contact-center agents live in WxCC" does not cover.
- **Three PRs:** dispatch kind and change deadline; the own queue (role `agent`, claim,
  release, not picked up); the calendar invite.

Built in step 2 with these limits, each a follow-up:

- **No automatic refresh yet.** HTMX (point 5) needs a vendored script and a CSP change with
  its own review (ADR 0016); until then the queue has a **Refresh** link and announces its
  counts in a polite live region on load. `queue_refresh_seconds` is therefore not added yet.
- **"Waited longest" is approximated.** The lobby join time is not stored, so among citizens
  in the lobby the earliest start goes first.
- **"Not picked up" (point 8)** shows in the queue; the system event and the flag on the
  dashboard and the agenda follow with ADR 0044's agenda.
- **Releasing others' claims** is allowed to whoever holds `bookings.view` on the group (group
  and organisation admins), since group admins do not hold `group.edit`.

Built in step 3 (the calendar invite, point 9) with these choices:

- **Message jobs instead of the `BookingDispatcher` port.** The invite and its withdrawal are
  two email jobs (`send_team_invite`, `send_team_cancel`) planned with the booking's other
  jobs, like every message (ADR 0009). The WxCC task already has its jobs, so no hook is left
  for a port to carry; ADR 0032 can still introduce one when a second contact center arrives.
- **Organizer and attendee are the team's address.** The mail is sent from the organisation's
  sender (`From`), but the sender port does not expose that address, so the `REQUEST` names
  the team mailbox as organizer and attendee: the event lands in the team's calendar without
  answers. `SEQUENCE` is the minute of sending, so it always rises.
- **No invite for direct calls**: they start at once.

## Consequences

- New port `BookingDispatcher` and adapters `app/adapters/dispatch_contact_center.py` and
  `app/adapters/dispatch_queue.py`; `app/jobs/` calls hooks instead of WxCC directly. ADR 0032's
  rename of `dispatch_wxcc_task` lands first or in the same PR.
- `app/jobs/calendar.py` gains `REQUEST` with `ORGANIZER`/`ATTENDEE`, beside `PUBLISH` and
  `CANCEL`.
- Authorisation: `queue.work` and the `agent` role in the matrix, the expectation table, the
  generated `docs/authorization.md`, and tests for every role × queue route × own/other group.
- Abuse cases in `docs/threat-model.md`: claiming in another group's queue; two agents
  claiming at once; reading the host link without a claim; guessing booking ids in claim
  URLs; an invite address that is not the organisation's (an admin typo sends first names
  outside, so the address is shown in the audit and in the group overview).
- Privacy (`docs/dataflow.md`, DPIA): the invite puts the citizen's first name and the booking
  reference in the team's mailbox, **outside our purge**. That is why it carries no more. The
  queue shows first names to agents of the group only.
- `docs/architecture.md` (dispatch diagram), `docs/runbook.md` (switch to the own queue when
  Connect is down), `docs/manual/{nl,en}/` topics for the group's dispatch fields and the queue
  screen, and the a11y pages in `e2e/pages.json` for the queue in both locales.
- Later, not planned: availability per agent from their own calendars (Exchange, Google, HCL
  Notes) behind a `CalendarProvider` port. That would revisit B1 and B5, need rights on staff
  calendars and a DPIA update. Recorded as open point O12 in HANDOVER §11.
