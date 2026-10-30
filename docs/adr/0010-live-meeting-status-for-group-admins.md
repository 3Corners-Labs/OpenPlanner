# 0010. Live meeting status for group admins

Status: accepted (2026-10-23), built. Webhooks and the agenda columns came with work packages 5 and 7; the polling fallback and the self-refreshing agenda came on 2026-10-30 (see *Build status*). Amended by *Amendments* below.

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

## Amendments (2026-10-30)

Written when the missing parts were built; the decision above stays as it was.

1. **Point 4, the 300 s window.** Webex's webhook carries no signed event time, so there is
   nothing to compare with `now`: the WxCC check (`|now − ts| ≤ 300 s`) cannot exist for Webex.
   What protects against replay is the signature plus the `event_key` that the domain remembers
   for 30 days (`webhook_events`); a replayed or redelivered event is recorded once. The
   threat model says so.
2. **Point 5, HTMX.** The agenda refreshes through the polled-fragment pattern of ADR 0072
   (`static/live-region.js`, one small own script, no HTMX), as ADR 0016 and 0048 allow. The
   day view's table is a fragment (`GET /admin/groups/{slug}/agenda/live?day=…&seen=KEY`, `204`
   while nothing changed) with the permission and scope of the page. Without the script the
   page shows its status as loaded and offers a *Refresh* link. The script is allowed on the
   day view only, with `SCRIPT_CSP` (`script-src 'self'`), like the queue; the week view stays
   without scripts. Days in the past do not poll.
3. **Point 2, polling.** `VideoProvider.meeting_status(meeting_id, host_email)` is the fallback.
   The Webex adapter reads `GET /meetings/{id}` for the state and, once the meeting started,
   `GET /meetingParticipants?meetingId=…`, and reduces them to `MeetingStatus` (state, count,
   host joined, first join, last leave) inside the adapter. `MeetingStatusService` asks only for
   a booking that is confirmed, has a meeting, lies between 15 minutes before its start and 30
   minutes after its end and is not ended, whose status has not been touched for the refresh
   window; a booking is asked at most once per window per process, whether Webex answered or
   not; at most 25 bookings per view, 5 requests at once. The result replaces the counts built
   from webhooks and adds a timeline event only for a transition (started, host joined, ended),
   marked `polled`. A failure keeps the status as it was and goes to the timeline with the
   provider's own reason (ADR 0065), once per distinct failure.
4. **Open check.** The field names of the two Webex responses are taken from the Webex
   reference and fixed in `tests/adapters/test_webex_meetings.py`; they have not yet been
   compared with a recorded answer from the production Webex site. A probe on the test host
   (`GET /meetings/{id}` for a running meeting) closes that.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `MEETING_STATUS_REFRESH_SECONDS` | C platform | 30 | 10–300 | `settings.platform.manage` | How old a status may be before the provider is asked, and the interval of the agenda's live region. One value for both so what the page shows is never older than one window plus one poll. Bounded below to protect Webex's rate limit: each open agenda asks at most once per window per booking. |

**Fixed on purpose:** the join window the poll covers (15 minutes before to 30 minutes after,
the citizen's join window of `app.domain.calls`), the ceilings of 25 bookings per view and 5
requests in flight (protections: raising them only multiplies Webex's load), and that past days
do not poll.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | `VideoProvider`, webhooks (`adapters/webhooks.py`), `CallStatus`, agenda columns | #10, #81 | merged |
| 2 | `VideoProvider.meeting_status`, Webex adapter, `MeetingStatusService`, the agenda's polled fragment, `MEETING_STATUS_REFRESH_SECONDS` | AG-3 | in PR |
