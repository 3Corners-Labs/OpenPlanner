# 0072. Direct calls per staffing block, live status for citizen and agent, visible failures

Status: proposed (2026-10-30), requested by the product owner after testing the own queue
on docker01. Amends ADR 0019 (direct call per group) and ADR 0026 (cap and queue feedback),
extends ADR 0043 (own queue) and follows the pattern of ADR 0048 and ADR 0061 (a small own
script that polls a server-rendered fragment).

## Context

The product owner tested the direct call with a group on OpenPlanner's own queue (ADR 0043)
and found it "working only half": the call appears in the queue, but after the agent picks it
up the Webex link is missing, and the citizen never sees that an agent took the call. Three
causes, found on docker01 (booking timeline, Webex probes):

1. **Webex refused every meeting of a direct call.** A direct call starts *now*, so its start
   carries microseconds (`2026-10-30T17:25:03.473110+01:00`); Webex answers `400 format is
   wrong`. Scheduled bookings start on a whole minute and were never affected. A second
   Webex rule matters for the worker's retries: a start more than about a minute in the past
   is refused with "before current time", while a start a few seconds ago is accepted.
2. **The waiting page knew only the contact center.** It derived "an agent has accepted" from
   the WxCC task state and "no agent available" from a task that was never created for the
   own queue, so with the own queue the citizen read "we are looking for an agent" for ever:
   a claim (ADR 0043) changed nothing on their page, and a failed meeting was not shown at all.
   The agent, on their side, saw "no meeting" without a reason.
3. **Both pages waited for a click.** ADR 0019 and ADR 0043 said the citizen's page and the
   queue never reload by themselves and offered a Refresh link. The product owner expects what
   ADR 0048 and ADR 0061 already give the updates page and the overview: the page shows a
   change within seconds, without touching the browser.

The product owner also asked to decide *where* direct calls are offered differently: not one
switch per group, but per time block, so a team can take direct calls in the morning and
scheduled appointments in the afternoon, or both at once.

## Decision

### Direct calls per staffing block

1. **Every staffing block (ADR 0027) carries an offer.** `TimeWindow.offer` is one of
   `scheduled` (default: the block offers slots), `direct` (the block takes direct calls and
   offers no slots) or `both`. The same field exists on an exception with a window. The
   group switch `direct_call_enabled` (ADR 0019 point 1) goes; the migration gives every
   block of a group that had the switch on the offer `both`, so nothing changes for citizens.
2. **What the offer decides.** `availability.slots_on` builds its runs from staffed blocks
   that offer slots; a direct-only block ends a run like a block without agents. A direct call
   can start when *now* lies in a staffed block that offers direct calls, with the holidays,
   closures and the cap of ADR 0026 unchanged. The agent count of a `direct` block has no
   meaning yet (the cap of ADR 0026 stays the group's `direct_call_max_concurrent`); deriving
   the cap from the block is a possible follow-up, not part of this ADR.
3. **In the admin portal** the block forms (add, change, exception) get a field *Offer*
   with the three choices; the week view and the exception list name the offer when it is not
   the default; the group page says in how many blocks direct calls are offered. The group
   settings keep `not_picked_up_minutes` and `direct_call_max_concurrent`.

### The citizen's waiting page

4. **One rule in the domain.** `direct_queue.wait_state(booking, status, group, now)` returns
   what the citizen is told, for both dispatch kinds:

   | State | When |
   |---|---|
   | `inactive` | the booking is no longer confirmed |
   | `failed` | the meeting could not be created (`CallState.FAILED`, ADR 0065 reason on the timeline) |
   | `unavailable` | the task failed, or nobody accepted within `not_picked_up_minutes` after the start, whatever the dispatch kind |
   | `in_call` | the host joined the meeting |
   | `accepted` | the WxCC task is connected **or an agent claimed the booking** (ADR 0043) |
   | `searching` | otherwise; while the meeting is not ready the page says it is being prepared |

   `failed` is honest on both sides: the citizen reads that the video call could not be set
   up because of a technical problem and is offered to schedule instead; nobody waits for a
   button that will never come. A claimed direct call also counts as *connected* for the cap
   of ADR 0026, so an agent on their way to the meeting keeps the place.

### The agent's queue

5. **The video link's state on every row.** Each queue row says whether the meeting is
   *ready*, *being prepared* or *failed*; the page of a claimed booking shows the same, with
   the citizen's state (not yet joined, waiting in the lobby, in the call) and, when the
   meeting failed, a link to the booking's timeline for whoever holds `bookings.view` and the
   advice to release the booking. The agent never sees the provider's reason text: that is
   on the timeline (ADR 0065).

### Live, with a small own script

6. **Three pages poll a fragment**, as ADR 0048 and ADR 0061 do: the citizen's waiting page
   (`GET /b/{token}/wait/status?seen=KEY`), the queue (`GET /admin/groups/{slug}/queue/live`)
   and the claimed booking (`GET …/queue/{booking_id}/live`). The server answers `204` while
   `KEY` still describes the page, otherwise the fragment, which `static/live-region.js`
   swaps in. The fragment routes carry the same permission as their page; the citizen's
   fragment the same rate limit as the page. The script is generic: a region declares its
   poll URL and interval in data attributes; the status line is announced in one polite live
   region per page; focus inside the replaced part moves to the region's heading; a redirect
   (session ended) or a fragment without poll URL (the call ended) stops it. Nothing reloads
   the page (WCAG 2.2.1, 3.2.5), and every page works without the script, with its Refresh
   link. The updates page keeps its own script for now; moving it to the generic one is a
   small follow-up.
7. **Webex adapter.** `meeting_body` sends whole seconds, and a start that has passed is
   moved to the moment of the request: Webex refuses microseconds and a start more than about
   a minute ago, and a meeting for a call that has started is a meeting from now. The booking
   keeps its own start; only the request to Webex changes. The group check's probe is
   scheduled ahead and is not affected.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `QUEUE_REFRESH_SECONDS` | C platform | 10 | 5–60 | `settings.platform.manage` | Poll interval of the queue, the claimed booking and the citizen's waiting page; announced in ADR 0043 and added now. Bounded to protect the server: a direct call keeps a page open for minutes, a queue for a whole shift. |
| `offer` (per staffing block) | D organisation (per group, `schedule.manage`) | `scheduled` | `scheduled`, `direct`, `both` | `schedule.manage` | Replaces the group switch `direct_call_enabled`. |

Unchanged and reused: `not_picked_up_minutes` and `direct_call_max_concurrent` (per group,
ADR 0019 and 0026), the public rate limits (ADR 0042 §7).

**Fixed on purpose:** the pages never reload themselves and never move focus on an update
(WCAG); the fragments carry the permission and scope of their page (a protection); the
provider's reason stays on the timeline and never reaches the citizen (PII rule: a reason may
echo the request).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | Everything above in one PR: offer per block with migration, `wait_state`, video state in the queue, three fragments and `live-region.js`, Webex fix, `QUEUE_REFRESH_SECONDS` | this PR | in PR |
| 2 | Updates page on `live-region.js` instead of `update-progress.js` | | follow-up |

## Consequences

- Migration (reversible): `schedule_rules.offer`, `schedule_overrides.offer`; drop
  `groups.direct_call_enabled` after converting it into `both` on the group's blocks. The
  downgrade sets the switch again for every group with a block that offers direct calls.
- `docs/settings.md` regenerated (`QUEUE_REFRESH_SECONDS`); `docs/admin-map.md` and
  `docs/authorization.md` unchanged (same screens, same permissions).
- Threat model: fragments as new read paths ("poll flood" bounded by the rate limit and the
  minimum interval; "fragment outside scope" covered by the same permission check); the
  matrix sweep covers the two admin fragments. The citizen's fragment shares the public read
  limit (60 per IP per minute by default, ADR 0042 §7): at 10 seconds one address keeps about
  ten waiting pages live; beyond that the poller gets `429`, keeps the page it has and tries
  again at the next interval, and the Refresh link still works. A site with many citizens
  behind one address raises the limit or the interval in the portal.
- When a claim disappears while the agent's page is open (released by a group admin,
  cancelled), the claimed fragment says so and stops polling.
- Manual: *Wachtrij* (the list refreshes itself, the video link's state, what to do when it
  failed) and *Groepsinstellingen* (offer per block replaces the switch), nl and en.
- ADR 0019 point 3 ("refreshes itself without scripts"), ADR 0026 point 4 ("never reloads
  by itself") and ADR 0043's "no automatic refresh yet" are amended by point 6; the pages
  still never *reload*.
- Tests: the Webex body with a direct call's start; `wait_state` per dispatch kind; the
  fragments answer `204` and the fragment; a direct-only block offers no slots and opens the
  direct call; the migration converts the switch both ways.
