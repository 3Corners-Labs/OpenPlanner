# 0033. Audit events to an external log platform

Status: proposed (2026-10-27). Extends ADR 0031 (audit log per level, with system events).

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
