# 0034. Audit events to an external log platform

Status: accepted (2026-10-28) by the product owner, with the choices recorded under
*Acceptance* below. Extends ADR 0031 (audit log per level, with system events). The field list
of point 2 is replaced by `openplanner.event/1` of ADR 0040, part 2.

## Context

ADR 0031 made `audit_events` the one audit log: admin actions (logins, denials, changes) and
system events (one per job the worker runs). Admins read it in the portal, scoped to their
level. Two needs are not covered:

- **Security monitoring.** The organisation's security team watches all its applications in one
  log platform (SIEM, for example Splunk). It needs OpenPlanner's logins, denials and changes
  there to correlate them with other systems and to alert on them. The portal page does not
  help: it is per level, has no alerting and is not connected to anything.
- **Tamper evidence.** A superuser with database access could delete rows from
  `audit_events`. A copy outside the installation, written as the events happen, is the usual
  answer.

The container logs already reach any log platform: every line is structured JSON on stdout
with PII masked (`app/logging.py`), and the host or the platform's agent ships them. Audit
events are not in that stream; they exist only in the database.

## Decision

1. **Every audit event is also a log line.** After the transaction that wrote it commits, each
   audit event is emitted once on the normal log stream as a JSON line with
   `log_type: "audit"`. Emitting after commit means a rolled-back change never appears in the
   external log, and a committed one always does unless the process dies between commit and
   emit (accepted; see consequences).
2. **A fixed, versioned schema.** The line has `schema: "openplanner.audit/1"` and these fields
   only: `event_id`, `at` (UTC, ISO 8601), `source` (`admin` or `system`), `action`, `outcome`,
   `reason`, `organisation` and `group` (slugs, empty when not applicable), `booking_id`,
   `actor_subject` (the identity provider's `sub`, empty for system events), `actor_roles`,
   `detail` and `installation` (from the setting `INSTALLATION_NAME`). The schema is documented
   in `docs/audit-events.md`; changing a field means a new schema version.
3. **No personal data of citizens, no display names.** The line carries the admin's subject,
   not their display name or email; the log platform can resolve subjects through the identity
   provider. Citizen data never appears: audit events hold ids and codes only (ADR 0031), and
   the masking processor still runs over the line.
4. **Vendor-neutral delivery.** OpenPlanner does not talk to Splunk or any other platform
   itself. Shipping is configuration on the host: Docker's `splunk` logging driver, a Splunk
   Universal Forwarder or an OpenTelemetry/Fluent Bit agent reading the container logs. The
   platform routes `log_type: "audit"` to its own index. No new dependency and no new outbound
   credential in the application.
5. **One place in code.** The emit lives in the audit adapter next to the code that writes
   `AuditEventRow`, as a post-commit callback of the unit of work. Routes and services do not
   log audit events themselves.

## Acceptance (2026-10-28)

The product owner accepted this ADR with these choices:

- **Delivery:** one JSON line on stdout after commit (points 1 and 4), no outbox table; the
  gap when the process dies between commit and emit is accepted.
- **Schema:** the record is `openplanner.event/1` (ADR 0040) plus `log_type: "audit"`, not
  `openplanner.audit/1`; `docs/audit-events.md` lists the fields.
- **Categories shipped:** `audit`, `system` and `security`. `diagnostic` events never leave
  the installation.
- **Admin identity:** the subject only (point 3); no display name or email.
- **Security events (AU-4):** a rejected webhook (`webhook.rejected`, reason
  `invalid_signature`, detail `source:webex|wxcc`) and a failed Webex token refresh
  (`webex.token_refresh`, reason `unauthorized` or `unreachable`) are global events of
  category `security`. Per kind, at most one is written per window of
  `SECURITY_EVENT_WINDOW_MINUTES` (default 15); the next one says how many were held back
  (`suppressed:<n>`). The counters are kept in memory per process.

As built: `app/adapters/event_sink.py` (the line), `SqlUnitOfWork(sink=...)` (post-commit, also
for audit events written in a read transaction), `app/domain/security_log.py` (AU-4). The audit
line bypasses `LOG_LEVEL`, so a quiet log level never drops the trail. Audit events written by
the host CLI (`docker exec ... python -m app.cli`) are not shipped: that output does not reach
the container log; they are in the table.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `SECURITY_EVENT_WINDOW_MINUTES` | C platform | 15 | 1–1440 | superuser (env until the ADR 0042 registry exists) | window of AU-4 |
| `INSTALLATION_NAME` | B deployment | empty | `[A-Za-z0-9_.-]{0,64}` | env | from ADR 0040 part 2 |

**Fixed on purpose:** the categories that are shipped (a choice of the product owner, and
keeping diagnostic data inside the installation is a protection); the `log_type` marker.

## Consequences

- The security team gets logins, denials, admin changes and job outcomes in near real time,
  per organisation and group, and can alert on them without access to the portal.
- If the process dies between commit and emit, that event is in the database but not in the
  external log. For an installation of this size that gap is accepted; a later ADR can add an
  outbox table with a forwarder if a client requires guaranteed delivery.
- The external copy holds the subjects of admins. That is personal data of employees: the DPIA
  input (`docs/dataflow.md`) names the log platform as a recipient, and the retention there is
  the organisation's policy, not ADR 0031's.
- Webhook rejections and token-refresh failures (left open by ADR 0031) can use the same
  schema once they become audit events.
- `docs/runbook.md` gets a section on shipping audit events, with the Docker logging driver and
  Universal Forwarder examples.
- Tests: an audit event appears exactly once on a fake log stream after commit, never after
  rollback, with only the schema's fields and no PII.
