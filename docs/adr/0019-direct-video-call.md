# 0019. Direct video call ("now") per group

Status: accepted (2026-10-23). Settles open point O9. Amended by ADR 0026 (cap on open direct
calls per group, queue feedback) and by ADR 0072 (the switch moves from the group to the
staffing block; the waiting page refreshes itself through a polled fragment). Built; point 4's
leave/cancel control was the last gap and is built too (AG-2, 2026-10-30); see `docs/adr-status.md`.

## Context

Besides scheduling, citizens should be able to start a video call right away when the team is
open. It goes to the same group and queue as scheduled calls, and it is the quickest way to test
the WxCC interworking end to end.

## Decision

1. Per group a switch `direct_call_enabled` (off by default). The group page shows "Nu een
   gesprek starten" only within the opening hours of today and when the switch is on.
2. Starting a direct call creates a booking of kind `direct` (contact: first name and email,
   as for a scheduled call, so the citizen gets the link by mail too), a meeting and a work
   item immediately (`kind: direct`) in one flow; no slot capacity is used.
3. The citizen lands on a waiting page with the join link and the status ("we are looking for
   an agent", "an agent has accepted"). The page refreshes itself without scripts.
4. If no agent accepts within the group's `not_picked_up_minutes`, the page says so and offers
   to schedule instead (open point O2 for the follow-up). Leaving or cancelling ends the task
   (End Task API, or a cancellation update while that is open, ADR 0018).
   **How leaving works (built 2026-10-30):** the waiting page links to "Stoppen met wachten"
   while the call is searching, accepted or in progress. The link opens a question that says
   what happens; its form is a plain POST (`/b/{token}/wait/leave`, same-origin, the booking
   rate limit), so it works without JavaScript. The POST cancels the direct booking in one
   locked transaction and replaces its pending jobs with `end_wxcc_task` (ADR 0021),
   `delete_video_room`, `delete_trial_call` and the purge; no cancellation mail goes out, since
   the citizen did it themselves and the page says so. The cancelled booking stops counting
   under the group's cap at once (ADR 0026) and drops off the own queue (ADR 0043), which lists
   confirmed bookings only. The timeline gets `citizen_left` with detail `waiting` or `agent`.
   **Decision (assumption, to be confirmed by the product owner):** when an agent already took
   the call (task connected, claim in the own queue, or the host in the meeting), leaving ends
   the call *for the citizen only*: their link stops working and the work item is marked
   cancelled and ended as in ADR 0021, but the Webex meeting is not deleted, so the agent is
   not cut off mid-sentence; the meeting runs out by itself. Repeating the request, or leaving
   a call that already ended, changes nothing and shows the waiting page again.
5. The same rate limits and honeypot as booking apply.

## Settings

None. Leaving uses the existing booking rate limit (`RATE_LIMIT_BOOKINGS`, ADR 0042 §7) and
`not_picked_up_minutes`; no new limit or period.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | Points 1 to 3 and 5: switch, direct booking with meeting and work item, waiting page, limits | #10, #24, #155 | merged |
| 2 | Point 4: "no agent" text and the link to schedule instead | #10 | merged |
| 3 | Point 4: leave or cancel from the waiting page ends the work item (AG-2) | #NNN | in PR #NNN |

## Consequences

- Capacity of direct calls is managed by WxCC (agent availability), not by OpenPlanner.
- PII rules and the purge apply as for scheduled bookings.
- The citizen's way out is a page of its own (`wait_leave.html`); no personal data is added to
  URLs, jobs or logs, and the abuse cases are in `docs/threat-model.md`.
