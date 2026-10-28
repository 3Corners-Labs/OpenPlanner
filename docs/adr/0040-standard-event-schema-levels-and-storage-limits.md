# 0040. Standard event schema, log levels and storage limits

Status: accepted (2026-10-27), requested by the product owner. Part 1 (storage limits) is
implemented with this ADR; parts 2 to 4 are the agreed direction and get built in that order.
Extends ADR 0031 (audit log per level) and replaces the field list of ADR 0034 (still proposed)
before it is built.

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

### Part 2: one event schema (next)

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

### Part 3: filters and CSV export

9. The audit page gets filters for minimum level, category, date range and trace id; a trace
   id shows the whole chain of one request or job.
10. **CSV export** of exactly the current filter, inside the admin's scope, behind a new
    permission `audit.export`; every export is itself an audit event. The file is UTF-8 with a
    byte order mark, `;` as separator (Dutch Excel) and RFC 4180 quoting. Cells starting with
    `=`, `+`, `-` or `@` get a leading `'` against formula injection. At most 100 000 rows or
    366 days per export.

### Part 4: diagnostic mode and retention per category

11. Debug events reach the table only while an admin has switched on **diagnostic mode** for
    one group or organisation; it switches itself off after 24 hours, and switching it is
    audited. A group admin then sees the debug lines of their own group and nothing more.
12. **Retention per category**, each a setting: diagnostic 7 days, system 90 days (exists),
    security 1 year, admin actions 2 years. *Assumption until the product owner confirms the
    admin-action term, which is a records-policy choice (Archiefwet, BIO).*
13. **Emergency brake**: at grade `critical` the worker stops writing diagnostic events and
    switches diagnostic mode off. Audit and system events are always written.

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
