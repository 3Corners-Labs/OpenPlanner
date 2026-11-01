# Logging: levels, the record, the trace points and where lines go

What OpenPlanner writes to its log, at which level, in which shape, and how it leaves the
installation. Decisions: ADR 0040 (one event schema, levels), ADR 0034 (audit events on the
log stream), ADR 0042 §6 (`LOG_LEVEL` with expiry), ADR 0085 (the `TRACE` level and the
trace points; log destinations in the portal, not built yet).

## The record

Every line is one JSON object on stdout in the `openplanner.event/2` envelope (ADR 0040,
`app/logging.py`): `@timestamp` (RFC 3339 UTC, milliseconds), `log.level`, `message`,
`service.name` (`web` or `worker`), `service.version`, `installation` (`INSTALLATION_NAME`)
and, inside a request or job, `trace.id`, `organisation` and `group`. Every line passes the
PII mask: the fields of `app/domain/privacy.py` are replaced by `***` at any depth, as a
safety net on top of the rule that code never passes personal data to a logger (semgrep,
`.semgrep/rules.yml`). The access log of uvicorn is off (paths carry management tokens) and
httpx's own request lines are not logged (they carry the full URL); the `http.request` and
`outbound.request` trace points carry what is safe instead.

Audit, system and security events are on the same stream after their transaction commits,
marked `log_type: "audit"` (`docs/audit-events.md`). Diagnostic events (ADR 0040 part 4) are
in the portal only.

## Levels

| Level | Numeric | What |
|---|---|---|
| `critical` | 50 | the process cannot go on |
| `error` | 40 | a job or request failed |
| `warning` | 30 | retried, denied, degraded |
| `info` | 20 | the normal trail: jobs done, settings changed, start-up |
| `debug` | 10 | detail for troubleshooting |
| `trace` | 5 | the fixed trace points below, one line each (ADR 0085) |

`LOG_LEVEL` (class C, *Advanced settings* or `settings set LOG_LEVEL ... --hours N`) is the
minimum. Set in the portal or from the host it falls back to the default after at most 24
hours; `TRACE` after at most `TRACE_LEVEL_MAX_HOURS` (default 4), because the volume is higher
and the lines closer to the data. The audit line bypasses the level, so a quiet log never
drops the trail.

## Trace points

The only sources of `trace` lines are the points below, written through
`app.logging.tracer.trace(point, **fields)`. A point takes its registered fields and nothing
else: the registry `TRACE_POINTS` in `app/logging.py` refuses any other field, and a test
feeds personal data into every field and asserts the stream is clean. This table is checked
against the registry by `tests/test_logging_doc.py`.

| Point | Fields | Where | Never |
|---|---|---|---|
| `http.request` | `method`, `route`, `status`, `ms` | every HTTP request (`app/api/trace.py`); `route` is the matched route template, `/g/{slug}`, empty for an unmatched path | the raw path (`/b/{token}` carries the management token), the query, the client address |
| `job.run` | `type`, `attempt`, `ms` | every job the worker runs (`app/jobs/runner.py`) | the booking's data |
| `job.step` | `type`, `step`, `ms` | the steps of a message job: `load`, `compose`, `send` | the recipient, the message |
| `outbound.request` | `method`, `host`, `status`, `ms`, `error` | every outgoing HTTP call of web and worker (`app/adapters/http_trace.py`); `error` is the exception type when no response came | the URL, a query, a header, a body |
| `sql.slow` | `statement`, `table`, `ms` | a statement slower than `SQL_SLOW_MS` (`app/adapters/db/engine.py`); `statement` is the kind (`Select`, `Insert`, `raw`), `table` the first table | the statement text, parameters |
| `template.render` | `template`, `ms` | every page rendered through `app.web.templates.render` | the context |

Adding a trace point is a change to the registry, this table and the PII test together.

## Where the lines go

- **stdout, always.** `docker compose logs -f web worker`; the `json-file` driver rotates at
  5 files of 20 MB per container (ADR 0040 part 1).
- **The host route** (ADR 0034): Docker's `splunk` logging driver, a Splunk Universal
  Forwarder or Fluent Bit reading the rotated files; `docs/runbook.md`, *Shipping audit events
  to a log platform*. Route `log_type = audit` to its own index.
- **Log destinations** (ADR 0085 part 1): syslog (RFC 5424 over TLS, TCP or UDP), Splunk HEC,
  Elastic or OpenSearch (`_bulk` into a data stream) and NDJSON over HTTP, each a copy of the
  masked lines stdout carries, with its own minimum level, the audit categories it takes and an
  opt-in for trace lines and diagnostic events. Every process (web, worker) keeps one bounded
  queue per destination and delivers in batches in the background; a full queue drops the
  oldest line, a failing platform is retried with backoff, and a batch the platform will never
  accept is dropped instead of blocking the rest. A destination narrows the process's own
  level and never widens it: it sees trace lines only while `LOG_LEVEL` is `TRACE` too. The
  superuser manages them under Platform → Log destinations (`docs/manual/en/logging.md`):
  add, send a test line, switch on, change and delete; the auditor sees the list and the
  status of each process.

## Settings

| Key | Class | Default | Bounds |
|---|---|---|---|
| `LOG_LEVEL` | C | `INFO` | `TRACE`–`CRITICAL`, expires |
| `TRACE_LEVEL_MAX_HOURS` | C | 4 | 1–24 |
| `SQL_SLOW_MS` | C | 200 | 10–10 000 |
| `INSTALLATION_NAME` | B | empty | `[A-Za-z0-9_.-]{0,64}` |

The full list with every class is in `docs/settings.md`.
