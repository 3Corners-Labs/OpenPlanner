# 0029. Audit log per level, with system events for troubleshooting

Status: accepted (2026-10-26), requested by the product owner. Amends ADR 0014 (level of
`audit.view`, roles that hold it).

## Context

The audit log (ADR 0014) records logins, denials and admin changes. The product owner found it
too generic for troubleshooting: it says nothing about what the platform did on its own. Did
the reminder go out? Why did the work item not reach the queue? Was the job skipped because the
booking moved? All of that only reached the JSON container logs, which admins of an
organisation or a group cannot see and should not see, because those logs mix every
organisation together. The log also did not show the reason of a denial or failure, and group
admins had no log at all.

The product owner asked for a log at every role level that shows only that level: nothing from
a higher level, nothing from other organisations or groups.

## Decision

1. **One log, two sources.** `audit_events` gets a `source` (`admin` or `system`) and a
   nullable `booking_id` (a plain id with no foreign key, so events outlive bookings). We did not
   add a second table: the scope filter, the page and the retention rules stay in one place
   (ADR 0005).
2. **System events from the worker.** Every job the worker runs writes one event,
   `job.<job type>`, with outcome `success`, `skipped` (reason `stale`, `no_recipient`,
   `no_booking`), `retry` or `failure` (reason = the error code), and detail
   `attempt:<n>[,channel:<email|sms>]`. The event is placed at the booking's group and
   organisation. If no booking is known, the event is global. Writing the event never decides
   the job's fate: if it fails, the worker logs that and carries on.
3. **Exact per level.** `audit.view` becomes a group-level permission and is also given to
   `group_admin`. Its scope follows from the matrix as usual:
   - a group admin sees events of their own groups only (never an organisation's own events,
     whose `group` is empty);
   - an organisation admin sees their organisation's own events and those of its groups;
   - the superuser also sees global events.
   The SQL adapter applies this via `scope_filter`. Filters on the page (source, problems
   only, group, booking) are ANDed with the scope, so they can only narrow it. A group outside
   the scope is ignored.
4. **More detail on the page.** The outcome now shows its reason: a readable text for known
   reasons (authorisation reasons, skip reasons, `channel_not_configured`) and always the
   machine code, so other error codes can be looked up in the runbook. A booking id links to the
   booking's timeline (ADR 0010) when the admin holds `bookings.view` on that group.
5. **Retention.** System events are deleted after `SYSTEM_EVENT_RETENTION_DAYS` (default 90).
   The worker prunes them at most once an hour. Admin actions are kept as the audit trail, as
   before.

## Consequences

- Group admins can see the logins of other admins of their own group, changes made to their
  group by organisation admins or the superuser, and the outcome of every job of their
  bookings. They see nothing at organisation or global level.
- Nothing personal is added: system events carry codes, counts and ids. The PII rules of
  CLAUDE.md still hold, and a booking id is not personal data on its own.
- The table grows by about one row per job (four to six per booking). The index on
  `(source, at)` keeps pruning cheap, and the index on `group` keeps the group admin's view
  fast.
- The migration downgrade deletes system events, because the old outcome check cannot hold
  `retry` and `skipped`.
- Not yet covered: webhook rejections and token-refresh failures of the Webex service app.
  These are global and stay in the container logs for now. Recording them needs deduplication
  (an attacker can send many invalid webhooks) and is a follow-up.
