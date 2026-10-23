# 0010. Live meeting status for group admins

Status: accepted (2026-10-23). Implemented in work packages 5 (video adapter) and 7 (admin UI).

## Context

From the group's dispatch lead before the start (T−5 minutes by default, configurable per
group as `dispatch_lead_minutes`) a booking is read-only (`job_plan.change_deadline`). Group admins want to see
which video meetings are actually running, as a check: has the agent joined, how many people
are in the call, did the meeting end. Today nothing in the design reports back from the video
platform. This also feeds open point O2: a task that no agent picks up shows up as a meeting
where the host never joins.

## Decision

1. **Port.** `VideoProvider` gets `meeting_status(room_id) -> MeetingStatus` with: state
   (`scheduled`, `in_progress`, `ended`), current participant count, whether the host (agent)
   has joined, and the actual start and end times.
2. **Source: events first, polling as fallback.** The adapter subscribes to Webex webhooks for
   meeting start/end and participant join/leave, and keeps the status current from those events.
   When the admin opens the overview, statuses older than 30 seconds are refreshed through the
   Meetings API (meeting state and participant list). The exact endpoints and event names are
   verified in WP5. If Instant Connect (B7) cannot provide this status, that counts towards the
   Meetings API in open point O1.
3. **Only aggregates are stored.** A `meeting_status` record per booking holds state,
   participant count, `host_joined`, `started_at`, `ended_at` and `updated_at`. Names, email
   addresses and IP addresses of participants returned by Webex are never stored or logged. The
   aggregates are not personal data and stay after the purge, for statistics.
4. **Webhook security.** Incoming Webex events are verified with the webhook secret (Webex's
   signature header), rejected outside the 300 s window, and ignored for unknown meeting IDs.
   The endpoint is rate-limited. It is authenticated by signature, not by `require_scope`.
5. **Admin view (WP7).** "Today" per group: time, booking status, meeting state, participants,
   host joined yes/no. State is shown as text plus an icon, never by colour alone. HTMX refreshes
   the table every 30 seconds; no websockets. The view uses `require_scope(group_id=…)`, so a
   group admin sees only their own groups.
6. **Signal for O2.** A meeting whose host has not joined X minutes after the start (X per group,
   default 10, HANDOVER §5) is marked in the overview. Follow-up actions (call back, reschedule)
   wait for the decision on O2.

## Consequences

- `docs/dataflow.md` gains the flow Webex → service (events, aggregates only). The DPIA records
  that participant identities are processed in memory only and never stored.
- WP5 adds the webhook endpoint, the Meetings API polling and contract tests with recorded
  responses. WP7 adds the overview.
- The threat model gets the webhook endpoint (forged events, replay, flooding).
