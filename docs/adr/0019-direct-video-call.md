# 0019. Direct video call ("now") per group

Status: accepted (2026-10-23). Settles open point O9. Amended by ADR 0026 (cap on open direct
calls per group, queue feedback).

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
5. The same rate limits and honeypot as booking apply.

## Consequences

- Capacity of direct calls is managed by WxCC (agent availability), not by OpenPlanner.
- PII rules and the purge apply as for scheduled bookings.
