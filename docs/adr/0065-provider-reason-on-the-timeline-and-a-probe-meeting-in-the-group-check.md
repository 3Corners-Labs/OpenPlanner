# 0065. Webex's reason on the timeline and in the chain; the group check schedules a probe meeting

Status: accepted (2026-10-30), requested by the product owner. Extends ADR 0010 (timeline),
ADR 0017 (meetings), ADR 0040 (event schema) and amends the "read-only" wording of the
group check in ADR 0038.

## Context

On 2026-10-30 every video meeting on docker01 failed with `rejected_400`. The booking's
timeline showed the code and a generic hint ("usually the host has no licence ..."); the only
place with Webex's own words was the worker's stdout log (`integration.refused`). The product
owner had to ask what was wrong. The cause turned out to be none of the hinted ones: OpenPlanner
sent `start` and `end` in UTC (`+00:00`) next to `timezone: Europe/Amsterdam`, and Webex refuses
a time zone that does not match the offset ("timezone 'Europe/Amsterdam' and timezone offset in
'...+00:00' do not match"). That is fixed in the same change: the meeting adapter sends the times
in the group's time zone.

Two things made the failure cost a conversation instead of a glance:

1. **The reason stayed in a log nobody opens.** Webex explains most refusals in `message` and
   `errors[].description`, with a `trackingId` for Cisco support. The audit log's chain view
   ("Hele keten") and the booking's timeline showed only the code.
2. **Nothing checked the real call before a citizen booked.** The group's "Koppeling
   controleren" button listed the host's meetings (`GET /meetings`), which proves the host
   exists and may be read for, not that the Service App may *schedule* for it with the options
   OpenPlanner sends. The product owner expects a wrong user, scope or request to be reported
   before the first booking fails, not after.

Constraints: no personal data of citizens in events, logs or the audit table (CLAUDE.md);
Webex echoes submitted values in its error text, so a provider message may only be kept for
requests that carry none; the event schema is shared with the log platform (ADR 0040).

## Decision

1. **A refusal carries the provider's reason.** `IntegrationError` gets a `reason`: Webex's
   `errors[].description` (or `message` when there are none), email addresses masked, at most
   300 characters. The HTTP adapter sets it only for requests marked `explain` (today: creating
   a meeting, which carries the group name and the host's address, never a citizen's data).
   Requests with citizen data (work items with a first name, messages) never set it.
2. **The reason is stored with the failure, not only logged.** The booking's timeline event
   (`booking_events.message`) and the job's system event (`audit_events.message`) keep it.
   The booking page shows it under the event as "Antwoord van Webex: ..."; the audit log and
   its chain view show it under the outcome; the CSV export and the shipped record carry it as
   `message` (ECS's human-readable text). Adding a field makes the schema
   `openplanner.event/2`; everything else is unchanged, so a platform mapping for version 1
   keeps working.
3. **The hints stop pointing at the log.** The failure hints of ADR 0010 say what to check and
   that Webex's answer is on the page; the licence and scope hints stay, as the usual causes.
4. **The group check schedules a probe meeting.** "Koppeling controleren" creates a meeting
   through the same adapter and with the same body as a booking (title "OpenPlanner
   controle", tomorrow at the same clock time, 30 minutes, no invitation mail) and deletes it
   at once. A refusal is a finding with Webex's reason (`webex.schedule_refused`); a probe that
   could not be deleted is a warning naming the meeting (`webex.probe_left`). This exercises
   licence, scope, host, time zone and meeting options in one go. The check stays
   `maintenance_safe` (ADR 0038): it writes nothing to the database but its report; the
   "read-only" wording there is amended to "writes nothing but its report; it schedules and
   removes one probe meeting at Webex".
5. **Step 2 (next PR): the check runs on its own.** The worker checks every group with a Webex
   host once per `integration_check_interval_hours` and after the host address changes, keeps
   the result as the group's latest check, and records a system event `integration.check` at
   the group's level when the overall status is *Fout* (on change, and once per interval while
   it lasts), so admins hear about a wrong user, scope or site before a citizen does.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `integration_check_interval_hours` | C platform | 24 | 1–168 | `settings.platform.manage` | step 2; how often the worker checks every group with a Webex host |

**Fixed on purpose:** the reason is cut at 300 characters (the column size; a longer text is
a stack trace, not a reason); the probe meeting is 30 minutes, a day ahead, and deleted at once
(a constant of the check, not an operating choice); the button's rate limit stays 6 per minute
(ADR 0038).

## Build status

| Step | What | PR | Status |
|---|---|---|---|
| 1 | Times in the group's time zone; reason on timeline, chain, export and shipped record; probe meeting in the group check | this PR | built |
| 2 | Automatic check per group with a system event on failure, `integration_check_interval_hours` | #PRNUMBER | built |

## Consequences

- `migrations/versions/0056`: `message` on `audit_events` and `booking_events` (reversible,
  the downgrade drops the columns).
- `docs/audit-events.md` describes `openplanner.event/2`; `docs/runbook.md` §9 describes the
  probe meeting; the ADR 0038 wording is amended here, not rewritten there.
- Threat model: a provider message is text from an external party. It is stored masked and
  capped, shown as text (escaped by the template), never interpreted, and only for requests
  without citizen data; `docs/dataflow.md` says so.
- The host's calendar briefly shows and loses a probe meeting on every check; `sendEmail` is
  false, so nobody is mailed. With step 2 that is once per `integration_check_interval_hours`
  per group (the worker looks every ten minutes which groups are due) and once after a host
  address is saved.
- Step 2: `SystemLog.record` takes a `group_slug`, so an event about a group itself lands at
  that group's level; the audit page labels `integration.check` ("Koppeling gecontroleerd");
  `docs/runbook.md` §9, the group settings and audit manual topics and the threat model
  describe the automatic check.
- Tests: the meeting body in the group's zone; the reason from `errors[]` preferred over the
  generic `message`; the reason on the timeline and the system event; the probe meeting is
  created and deleted and a refusal becomes a finding with the reason; schema fields in order.
