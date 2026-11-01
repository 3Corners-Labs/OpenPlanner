# 0040. Standard event schema, log levels and storage limits

Status: accepted (2026-10-27), requested by the product owner. Part 1 (storage limits) is
implemented with this ADR; parts 2 (one event schema), 3 (filters and CSV export) and 4
(diagnostic mode and retention per category) on 2026-10-28 and 29. The product owner set the
admin-action term to 2 years and made diagnostic mode the superuser's on 2026-10-29.
Extends ADR 0031 (audit log per level) and replaces the field list of ADR 0034 (proposed at the
time, since accepted) before it is built.
§7 extended by ADR 0085 §8 (`TRACE` below `debug`). Schema bumped to `openplanner.event/2` by
ADR 0065. Part 4 is the single home for retention per category: it supersedes ADR 0031 §5 for
admin actions, and ADR 0068 §2 adds a separate retention for request events.

## Context

OpenPlanner writes two kinds of records:

- **Container logs**: structured JSON on stdout (`app/logging.py`), PII masked, read by
  operations and shipped to a log platform by the host (ADR 0034). The lines carry no
  organisation or group, so they cannot be filtered per tenant, and nothing limits their size:
  Docker's default `json-file` driver grows without bound.
- **The audit log**: the `audit_events` table, shown per level in the portal (ADR 0031). It has
  outcomes but no log level, no way to follow one request or job across events, and no export.
  System events are pruned after 90 days, but SQLite keeps the freed pages, so the file never
  shrinks. Admin actions are kept forever.

The product owner asked for one standard structure for logging, audit and troubleshooting:
common formats and timestamps, a log level (debug included), organisation and group tags so
each role sees only its own lines, CSV export, and controls so no disk fills up.

## Decision

### Part 1: storage limits (built now)

1. **Container logs rotate.** Every service in `docker-compose.yml` uses the `json-file`
   driver with `max-size: 20m`, `max-file: 5` and `compress: "true"`: at most about 100 MB per
   container, oldest file dropped first. `json-file` stays the driver because log agents
   (Fluent Bit, Splunk forwarder, OpenTelemetry collector) read its files.
2. **The database gives freed space back.** Every connection sets
   `PRAGMA auto_vacuum=INCREMENTAL`, so a new database is created in that mode. An existing
   database is converted once by a `VACUUM` that `migrations/env.py` runs after the
   migrations, before the web process serves anything. If that `VACUUM` fails (for example
   because the disk is too full to hold the temporary copy), startup continues and the log
   says so. The worker runs `PRAGMA incremental_vacuum` once an hour, which returns the pages
   freed by pruning to the file system.
3. **The worker watches the disk.** Each loop the worker measures the free space on the data
   volume and the size of the database (with its WAL file) and grades it:
   - `critical` below `STORAGE_CRITICAL_FREE_PERCENT` free (default 5);
   - `low` below `STORAGE_LOW_FREE_PERCENT` free (default 15), or a database larger than
     `DATABASE_MAX_MB` (default: no limit);
   - `ok` otherwise.
   A change of grade is written as a global system event `storage.check` (failure with reason
   `free_space_critical`, `free_space_low` or `database_too_large`, or success on recovery),
   with detail `free_percent:<n>,database_mb:<n>`. Only the superuser sees it, as with every
   global event (ADR 0031). While the grade is not `ok`, the worker also logs
   `storage.degraded` on stdout at `error` (critical) or `warning` (low), once per hour, so
   monitoring on the log platform keeps seeing it.
4. **Readiness tells monitoring.** `/readyz` answers `200 {"status": "degraded"}` when the
   grade is not `ok`. It stays `200` on purpose: a full disk is a reason to alert, not to take
   the web process out of service or to roll back an update (ADR 0029). Readiness still says
   nothing about why; the system event and the log do.

### Part 2: one event schema (built)

5. **One schema for table, log line, CSV and log platform**, aligned with Elastic Common Schema
   and the OpenTelemetry log data model so platforms need no mapping: `event_id`,
   `@timestamp`, `log.level`, `event.category` (`audit` for admin actions, `system` for jobs,
   `security` for webhook and token failures, `diagnostic` for debug), `event.action`,
   `event.outcome` and our own `outcome` and `reason`, `organisation`, `group`, `booking_id`,
   `actor`, `trace.id`, `service.name` (`web` or `worker`), `service.version`, `installation`
   and `detail`. This replaces the field list of ADR 0034, as `openplanner.event/1`.
6. **Timestamps** are RFC 3339 in UTC with milliseconds and `Z`
   (`2026-10-27T14:03:12.345Z`) everywhere they are stored or exported. The portal shows the
   group's time zone with the UTC value alongside. Host clocks are NTP-synchronised (runbook).
7. **Levels** are the syslog levels Python already has: `debug`, `info`, `warning`, `error`,
   `critical`. The outcome sets the level of an audit or system event: success and skipped are
   `info`, retry and denied are `warning`, failure is `error`.
8. **Tags in the container log too.** Request middleware and the worker bind `organisation`,
   `group` and `trace.id` as structlog context, so every stdout line can be filtered per tenant
   on the log platform. The portal still reads only the table: only there does the scope filter
   per role apply.

As built: `app/domain/event_schema.py` holds the schema and `rfc3339`; the trace, organisation
and group live in `app/domain/event_context.py` (context variables, so the domain stamps
events without knowing about logging). Every HTTP request runs in its own trace (an incoming
`traceparent` is not trusted); admin routes bind the target of their permission check, public
`/g/{slug}` pages the group; the worker gives each round and each job its own trace, bound to
the job's booking. `audit_events` gains `event_id`, `level`, `category`, `trace_id` and
`service` (migration 0034); `service.version` and `installation` come from the settings when a
record is written out. Log lines rename structlog's `event` and `level` to `message` and
`log.level`, because a string `event` clashes with ECS's `event.*` object.

### Part 3: filters and CSV export (built)

9. The audit page gets filters for minimum level, category, date range and trace id; a trace
   id shows the whole chain of one request or job.
10. **CSV export** of exactly the current filter, inside the admin's scope, behind a new
    permission `audit.export`; every export is itself an audit event. The file is UTF-8 with a
    byte order mark, `;` as separator (Dutch Excel) and RFC 4180 quoting. Cells starting with
    `=`, `+`, `-` or `@` get a leading `'` against formula injection. At most 100 000 rows or
    366 days per export.

As built: `AuditFilter` gains minimum level, category, period and trace id, applied in SQL
(`app/adapters/db/uow.py`); dates are whole days in `ADMIN_TIMEZONE`, `to` inclusive. Each row
links to its whole chain. `GET /admin/audit/export.csv` takes the same parameters, behind
`audit.export`, which the organisation admin, the auditor (ADR 0039) and the superuser hold
and the group admin does not; *assumption until the product owner says otherwise*. Without a
period the export covers the last `AUDIT_EXPORT_MAX_DAYS`; an export over a limit is refused on
the page with the reason and is itself audited as a failed `audit.export`. The export event
sits at the group of the filter, else at the admin's one organisation, else global. The
columns are `openplanner.event/1` in schema order; `actor` is the subject, not the name.

### Part 4: diagnostic mode and retention per category (built)

11. Debug events reach the table only while an admin has switched on **diagnostic mode** for
    one group or organisation; it switches itself off after 24 hours, and switching it is
    audited. A group admin then sees the debug lines of their own group and nothing more.
12. **Retention per category**, each a setting: diagnostic 7 days, system 90 days (exists),
    security 1 year, admin actions 2 years. *Assumption until the product owner confirms the
    admin-action term, which is a records-policy choice (Archiefwet, BIO).*
13. **Emergency brake**: at grade `critical` the worker stops writing diagnostic events and
    switches diagnostic mode off. Audit and system events are always written.

As built: the product owner decided on 2026-10-29 that **only the superuser** switches
diagnostic mode (`diagnostics.manage`, global, locked to the superuser), and confirmed **2
years** for admin actions. The superuser switches it on the audit page for a group or a whole
organisation (`diagnostic_modes`, migration 0035); switching is audited as
`diagnostics.manage` with `diagnostics.on` or `diagnostics.off`. Debug events go through
`DiagnosticLog` (`app/domain/diagnostics.py`); the first debug point is the worker's `job.run`
per job (type, attempt, duration), and further points use the same call. Pruning runs per
category over `ix_audit_events_category_at`. At grade `critical` housekeeping calls the brake
every loop: all modes end and one global system event `diagnostics.brake` says how many.
Debug events are never shipped to the log platform (ADR 0034).

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `STORAGE_LOW_FREE_PERCENT` | B deployment | 15 | 0–100, above critical | env | part 1; tied to the host's volume |
| `STORAGE_CRITICAL_FREE_PERCENT` | B deployment | 5 | 0–100 | env | part 1 |
| `DATABASE_MAX_MB` | B deployment | none | ≥ 1 | env | part 1 |
| `AUDIT_EXPORT_MAX_ROWS` | C platform | 100 000 | 1 000–1 000 000 | superuser; env until the ADR 0042 registry exists | part 3 |
| `AUDIT_EXPORT_MAX_DAYS` | C platform | 366 | 1–3660 | superuser; env until the ADR 0042 registry exists | part 3 |
| `AUDIT_RETENTION_DAYS` | C platform | 730 | 365–3650 | superuser; env until the ADR 0042 registry exists | part 4, admin actions (product owner) |
| `SECURITY_EVENT_RETENTION_DAYS` | C platform | 365 | 30–3650 | as above | part 4 |
| `DIAGNOSTIC_EVENT_RETENTION_DAYS` | C platform | 7 | 1–90 | as above | part 4 |
| `DIAGNOSTIC_MODE_HOURS` | C platform | 24 | 1–72 | as above | part 4 |
| `SYSTEM_EVENT_RETENTION_DAYS` | C platform | 90 | 1–3650 | as above | existing (ADR 0031) |
| `INSTALLATION_NAME` | B deployment | empty | `[A-Za-z0-9_.-]{0,64}` | env | part 2; names the host installation on a shared log platform, needed before the database works |

**Fixed on purpose:** the schema's field names and the timestamp format (a change is a new
schema version), the mapping of outcome to level, and the 32-hex shape of event and trace ids;
the CSV dialect (BOM, `;`, RFC 4180) and the formula guard, which is a protection; the
emergency brake at grade critical (a protection, never switchable).

## Consequences

- The disk use of logs is bounded on the host (about 100 MB per container) and the database
  shrinks after pruning. Operations can see a filling disk on `/readyz`, in the log and in the
  superuser's audit log before it causes failures.
- The one-time `VACUUM` locks the database for its duration and needs free space about the
  size of the database. It runs at startup of the web container, before traffic, so for
  databases of this size (ADR 0036) it is a matter of seconds, once.
- Host-level log shipping is unaffected; rotated files that an agent has not read yet can be
  lost when the volume of logs is extreme, which is the trade-off of any bounded log.
- The `json-file` limits are fixed in `docker-compose.yml`; an installation that needs other
  values sets them in a `docker-compose.override.yml` or in the Docker daemon's defaults.
- Parts 2 to 4 each come with their own PR, migration and manual update; ADR 0034's
  implementation waits for part 2 so the external schema is defined once.
