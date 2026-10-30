# 0068. Troubleshooting per booking: requests, versions, retries and the worker in the group check

Status: accepted (2026-10-30), requested by the product owner. Extends ADR 0010 (timeline),
ADR 0031 (system events), ADR 0040 (event schema, retention) and ADR 0065 (provider reason,
group check).

## Context

On 2026-10-30 the product owner saw two bookings on docker01 fail with `rejected_400` at
14:26 and 14:38, ran "Koppeling controleren" at 15:59 and got *Webex: OK*. Both were right:
the bookings ran on 0.3.0, which still sent UTC times next to `Europe/Amsterdam` (fixed in
rc.7, ADR 0065), and the check ran on rc.7, where the fix was live. The portal gave no way to
see that:

1. **The timeline promised a reason it did not have.** The hint under *Videomeeting aanmaken
   mislukt* says "het antwoord van Webex staat hierboven", but the events were written by a
   version that stored no reason (ADR 0065 came later), so nothing stood there.
2. **An event does not say which version wrote it.** On the rc channel with auto-apply the
   version changes several times a day; an event of 14:26 and a check of 15:59 may come from
   different code.
3. **The container log is the only place with the HTTP detail, and it is gone after an
   update.** The `integration.refused` line (status, path, Webex `trackingId`) lived in the
   worker's stdout; the update recreated the container and the log of 14:26 with it.
4. **Nothing says when a job is tried again, and there is no button for it.** The two
   bookings got their meeting at 15:49 through jobs that no code plans; the timeline only
   shows *Videomeeting aangemaakt* out of nowhere.
5. **"Succeeded" for a job that did nothing.** The dispatch job of a group on the own queue
   (ADR 0043) reports success although no work item is created; that reads as "dispatched".
6. **The check does not cover the worker.** The probe meeting runs in the `web` container;
   bookings run in `worker`. A worker started without the Webex settings never claims the
   meeting jobs, which then stay *pending* in silence, and nothing in the check or on the
   booking page says so.

Constraints: no personal data of citizens in events (CLAUDE.md), so a request record holds
the method, path and status only, never a body, a query string or a header; one event
schema shared with the log platform (ADR 0040), so new detail goes into existing fields;
the admin structure (ADR 0047): no new screen, the detail lives on the booking page and in
the group check.

## Decision

1. **Every call to Webex and WxCC made for a booking is a system event.** The HTTP adapter
   (`AuthorizedClient`) reports each request to a `RequestLog` port; the domain writes a
   system event `integration.request` at the level of the booking's group with `booking_id`,
   outcome success or failure, `reason` the error code (`rejected_400`, `unreachable`), and
   `detail` with `method`, `path` (no query string), `status`, `ms`, `tracking` (Webex's
   `trackingId`) and `version`. Never a body or header. The event shares the job's trace id,
   so the audit log's chain view ("Hele keten") lists it under the job, and it ships to the
   log platform with the other system events (ADR 0034). The booking page shows them as
   *Verzoeken aan Webex en Webex Contact Center*. A probe meeting of the group check is
   recorded the same way, at the group's level, without a booking.
2. **Request events have their own retention.** `integration_request_retention_days` (C,
   default 30) prunes them apart from the other system events (default 90 days): they are
   many and only useful while a failure is fresh.
3. **A timeline event carries the version that wrote it** (`booking_events.version`,
   `APP_VERSION`), shown next to the event. The hint for a failure without a stored reason
   says so, instead of pointing at a line above that is not there.
4. **A job that has nothing to do is *skipped*, not *succeeded*.** The meeting and work item
   jobs return a reason: `already_done` (the meeting or task exists), `no_video` (no host),
   `own_queue` (ADR 0043: no work item by design) or `no_dispatch` (no WxCC mapping). The
   system event says *Overgeslagen* with that reason.
5. **The booking page lists the booking's jobs and offers "Opnieuw proberen".** Every job
   (type, planned time, status, attempts, last error) is shown with the timeline. Whoever
   holds `bookings.manage` on the group may plan a failed `create_video_room` or
   `dispatch_wxcc_task` again for an active booking that still lacks the meeting or task.
   The retry is audited as a `bookings.manage` change with detail `retry.<job type>`; the
   job's own outcome lands on the timeline as before.
6. **The group check covers the worker and the queue.** The worker writes a heartbeat row in
   the database once a minute: when it was seen, its version and the job types it claims.
   The check adds an area *Achtergrondtaken (worker)*: never seen or stale (fail, using
   `WORKER_HEARTBEAT_MAX_AGE_SECONDS`), alive (ok, with its version), a version that differs
   from the web container's (warning: an update is half way), no `create_video_room` among
   its job types while the group has a host (fail: the worker runs without the Webex
   settings), and the group's failed and overdue jobs (warning with the counts; a pending
   job is overdue `job_overdue_minutes` after its planned time).

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `integration_request_retention_days` | C platform | 30 | 1–365 | `settings.platform.manage` | retention of `integration.request` events, apart from the other system events |
| `job_overdue_minutes` | C platform | 10 | 1–1440 | `settings.platform.manage` | a pending job this long past its planned time counts as overdue in the group check |

Used, not new: `WORKER_HEARTBEAT_MAX_AGE_SECONDS` (B, 120) decides when the worker's heartbeat
is stale, as it does for the container healthcheck; `APP_VERSION` (B) is the version stamped
on events.

**Fixed on purpose:** the worker writes its heartbeat row once a minute (a constant of the
loop, far below the stale threshold; the file heartbeat for the container stays as it is);
the booking page lists the newest 100 request events (a display cap: older ones stay in the
audit log); the `path` in a request event is cut at 80 characters and the `detail` at the column's 255;
the retry covers the two job types that create something at a platform, not messages (a
message has its own channel test and the citizen must not be mailed twice).

## Build status

| Step | What | PR | Status |
|---|---|---|---|
| 1 | Request events with retention; version and honest hint on the timeline; skipped jobs; jobs list and retry on the booking page; worker and queue in the group check | #152 | built |

## Consequences

- `migrations/versions/0059`: `version` on `booking_events` (reversible). The worker heartbeat
  reuses `integration_reports` (name `worker-heartbeat`): no new table.
- `AuditFilter` gets `action`; `EventRetention` gets `requests`; `Transaction` gets
  `get_booking_jobs`, `count_group_jobs`, `save_worker_heartbeat`, `get_worker_heartbeat`.
- `docs/audit-events.md` lists `integration.request`; `docs/runbook.md` (*Troubleshooting with
  the audit log*, *Webex or WxCC is down*) describes the request events, the retry and the
  worker area; `docs/dataflow.md` and `docs/threat-model.md` state that a request event holds
  no body, query or header; the manual topics of the agenda (booking page) and the group
  settings (check) describe the new blocks.
- Threat model: the retry button is a write on a booking's jobs behind `bookings.manage`,
  same-origin and the maintenance gate; it can at most plan one job per type per click, for a
  booking that lacks the result, so it cannot be used to flood Webex.
- Tests: the adapter reports every request (success, refusal, unreachable) with the tracking
  id and never a body; the domain writes the event at the booking's level with the version;
  the prune uses the request retention; the timeline shows the version and the unexplained
  hint; skipped reasons per job; the retry authorises, refuses an inactive booking and a
  booking that has the result, plans one job and audits; the worker findings for every
  state; the heartbeat row is written and read; the matrix covers the new route.
