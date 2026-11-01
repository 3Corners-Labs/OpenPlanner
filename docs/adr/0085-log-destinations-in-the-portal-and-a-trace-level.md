# 0085. Log destinations in the portal (syslog, Splunk, Elastic) and a trace level

Status: accepted (2026-10-31) by the product owner; partly built: step 1 (the `TRACE` level and the trace points) is merged, step 2 (the destination model, the service, the queue and sender, the rights and the migration) is in PR (#295), step 3 (the four adapters and the wiring in web and worker) is in PR (#296) on top of it, step 4 (the screen, #297) is in PR on top of that, step 5 (the kill switch and the documents, #299) is in PR on top of that. Amends ADR 0034
point 4 (shipping is the host's job only) and extends ADR 0040 (levels) and ADR 0042 (`LOG_LEVEL`
with expiry). ADR 0008 (encrypted integration secrets) and ADR 0047 (one home per screen)
apply.

## Context

The product owner looked in the portal for the place where the platform's logging is connected
to a log platform (syslog, Splunk, Elastic) and found none: nothing to create, change, test or
remove. They also looked for a way to take raw trace logging off the platform and found none.
Both observations are right. What exists today:

- **Log lines** are JSON on stdout with PII masked (`app/logging.py`), in the
  `openplanner.event/2` envelope with `trace.id`, `organisation` and `group` (ADR 0040). The
  minimum level is `LOG_LEVEL` (class C, portal, falls back after at most 24 hours). The
  levels are `DEBUG` to `CRITICAL`; there is no level below `DEBUG`, and the code has two
  debug points in all.
- **Audit, system and security events** are written once more as a log line after commit
  (`app/adapters/event_sink.py`, ADR 0034), for the log platform.
- **Diagnostic events** (`DiagnosticLog`, ADR 0040 part 4) go to the table while the superuser
  has diagnostic mode on for a group or organisation; they never leave the installation. Today
  there is one diagnostic point, the worker's `job.run`.
- **Shipping is the host's job** (ADR 0034 point 4): Docker's `splunk` logging driver, a Splunk
  Universal Forwarder or Fluent Bit reading the rotated `json-file` logs, each described in
  `docs/runbook.md`. The application has no address or credential of a log platform, and the
  portal shows nothing about shipping.

ADR 0034 chose the host route because it needs no new dependency and no outbound credential,
and because the first client's host team already runs a forwarder. The product owner now wants
the connection managed where the rest of the platform is managed (the portal, by the superuser,
as with version management in ADR 0067 and certificates in ADR 0070), with the host route kept
for installations whose operations team prefers it. The second wish, raw trace logging, needs
a level that does not exist yet and trace points in the code that write it.

## Decision

### Part 1: log destinations, managed in the portal

1. **A log destination is an object of the platform**, created, changed, tested and deleted by
   the superuser under a new global permission `logging.manage`; the auditor sees the list and
   the status under `logging.view`. Every change is an audit event (`logging.destination`,
   detail `created|changed|deleted|tested:<kind>`). The screen is
   platform → *Log destinations* (`/admin/logging`), a standard-width list with one sub-page
   per destination (`/admin/logging/{id}`); the status (last delivery, last error, lines
   dropped) is also a row on *System status*. No new section: it sits in the Platform section
   next to *Integrations*, where the other outbound connections of the platform live.
2. **A closed list of kinds**, each a small adapter behind one Protocol `LogDestination` in
   `app/domain/ports.py`, and the host route stays available for every installation:
   - `syslog`: RFC 5424 over TCP with TLS (default), TCP, or UDP; the structured JSON line is
     the message, `APP-NAME` is `openplanner`, `MSGID` is the kind of line (`log`, `audit`).
     RFC 5425 octet counting on TCP. Server certificate verified against the host's CA store
     or an uploaded CA chain (PEM, no key).
   - `splunk_hec`: HTTP Event Collector, `POST /services/collector/event` with the line as the
     `event` and `sourcetype` `_json`; index and source configurable; token stored encrypted.
   - `elastic`: the `_bulk` API against a data stream (`logs-openplanner-<namespace>` by
     default), API key stored encrypted; also fits OpenSearch.
   - `http`: NDJSON `POST` to one URL with an optional bearer token, for OpenTelemetry
     collectors, Fluent Bit's HTTP input, Logstash and anything else that takes JSON lines.
   Implemented with the standard library (`socket`, `ssl`) and `httpx`, which are already in the
   image: no new dependency.
3. **Each destination chooses what it receives:** a minimum level (`trace` to `critical`), the
   event categories of the audit line (`audit`, `system`, `security`), and whether diagnostic
   events and trace lines are included at all (default off). The record is the same
   `openplanner.event/2` line that stdout gets; a destination never gets a field stdout does
   not. **stdout stays, always**: a destination is a copy, so `docker compose logs`, the
   rotating files and the host route keep working unchanged.
4. **Delivery never slows a request or a job.** Each process (web, worker) keeps one bounded
   in-memory queue per destination and one sender task that batches lines (at most
   `LOG_SHIP_BATCH_SIZE` lines or `LOG_SHIP_FLUSH_SECONDS`, whichever first) with a connect and
   send timeout of `LOG_SHIP_TIMEOUT_SECONDS`. A full queue drops the oldest line and counts
   it; a failing destination is retried with exponential backoff up to `LOG_SHIP_BACKOFF_MAX_SECONDS`.
   Dropped and failed counts are visible on the destination's page and on *System status*;
   crossing from working to failing writes one global system event `logging.destination`
   (failure, reason `unreachable|refused|tls`, detail the kind and the id, never the address's
   credentials), with the AU-4 window of ADR 0034 against repetition, and recovery writes the
   success. The sender's own lines go to stdout only, never to the destination that fails, so a
   broken destination cannot feed itself.
5. **Test before trust.** The sub-page has *Send test line*: one `info` line
   `logging.test` with the destination id, sent synchronously with the configured timeout; the
   page shows the outcome and the platform's answer code (never the body). Saving a changed
   address or credential does not need a successful test, but the status row says *untested*
   until one succeeds.
6. **Secrets and addresses follow ADR 0008**: token, API key and bearer are write-only,
   AES-256-GCM encrypted in the `settings_secrets` store, shown as *set* or *not set*, never
   in a log, error, audit event or URL. The address is validated as `https://` (or
   `syslog+tls://`, `syslog+tcp://`, `syslog+udp://`) with a host name and port; an `http://`
   address is accepted only outside production (`ENVIRONMENT`), as with `PUBLIC_BASE_URL`.
   Addresses of private ranges are allowed (the platform usually is on the same network).
7. **Kill switch on the host.** `LOG_SHIPPING_ENABLED` (class B) stops every destination
   without deleting its configuration, for an incident where the platform's traffic itself is
   the problem. The portal shows *switched off on the host* on the list.

### Part 2: a trace level and trace points

8. **`TRACE` is a level below `DEBUG`** (numeric 5, as the syslog-aligned levels of ADR 0040
   leave room for it), registered on the stdlib and on structlog's filtering logger, so
   `LOG_LEVEL=TRACE` on stdout and `trace` as a destination's minimum work the same way.
   `TRACE` set in the portal falls back after at most `TRACE_LEVEL_MAX_HOURS` (default 4,
   bounds 1–24), shorter than the 24 hours of `LOG_LEVEL` in general, because the volume is
   higher and the lines closer to the data. The CLI (`settings set LOG_LEVEL TRACE --hours`)
   has the same cap.
9. **Trace points are fixed in the code and listed in `docs/logging.md`**, one line each, all
   through one helper `trace(event, **fields)` in `app/logging.py` so the semgrep rule that
   forbids PII field names in log calls covers them:
   - `http.request`: method, route template (never the raw path: `/b/{token}` carries the
     management token), status, duration in ms, `trace.id`;
   - `job.run` and `job.step`: type, attempt, step name, duration;
   - `outbound.request`: adapter name, method, host, status, duration, `IntegrationError`
     code; never a URL with a query, a header or a body;
   - `sql.slow`: statement name (the SQLAlchemy construct, not the text) and duration above
     `SQL_SLOW_MS`;
   - `template.render`: template name and duration.
   No trace line holds a PII field, a token, a body or a header; the mask still runs as the
   safety net, and the test of `app/logging.py` asserts that every trace point passes a fake
   stream clean with PII in its inputs.
10. **Raw trace logging leaves the platform in two ways, both of them already decided here:**
    stdout at `LOG_LEVEL=TRACE` for the host route, and a destination with minimum level
    `trace` and *include trace lines* on (point 3). Diagnostic events of the table can also go
    to a destination that opts in (point 3). This amends the acceptance of ADR 0034 ("diagnostic
    events never leave the installation") to "never, unless the superuser switches it on per
    destination, audited"; *assumption until the product owner confirms, since that acceptance
    was their choice.* The CSV export of ADR 0040 part 3 stays the way to take diagnostic
    events out by hand.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `LOG_SHIPPING_ENABLED` | B deployment | `true` | – | env | point 7, kill switch |
| `LOG_SHIP_BATCH_SIZE` | C platform | 100 | 1–1000 | `settings.platform.manage` | point 4 |
| `LOG_SHIP_FLUSH_SECONDS` | C platform | 2 | 1–60 | `settings.platform.manage` | point 4 |
| `LOG_SHIP_TIMEOUT_SECONDS` | C platform | 5 | 1–30 | `settings.platform.manage` | point 4, connect and send |
| `LOG_SHIP_QUEUE_SIZE` | C platform | 10 000 | 1 000–100 000 | `settings.platform.manage` | point 4, lines per destination per process |
| `LOG_SHIP_BACKOFF_MAX_SECONDS` | C platform | 300 | 10–3600 | `settings.platform.manage` | point 4 |
| `TRACE_LEVEL_MAX_HOURS` | C platform | 4 | 1–24 | `settings.platform.manage` | point 8 |
| `SQL_SLOW_MS` | C platform | 200 | 10–10 000 | `settings.platform.manage` | point 9 |
| `LOG_LEVEL` | C platform (exists) | `INFO` | `TRACE` added to the choices | `settings.platform.manage` | point 8 |

Per destination (data, not settings): kind, name, address, transport, index or data stream,
source, minimum level, categories, include diagnostic, include trace, CA chain, credential.

**Fixed on purpose:** the list of kinds (a new kind is a new adapter and a PR); the record
format (`openplanner.event/2`, a change is a new schema version); the rule that stdout always
stays; the rule that a destination never receives a field stdout does not; the list of trace
points and what each may carry (a protection against PII leaving the installation); drop
oldest on a full queue (a request is never blocked by logging).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #263, landed through #266 | merged |
| 1 | `TRACE` level, `tracer.trace()`, the trace points, `TRACE_LEVEL_MAX_HOURS`, `SQL_SLOW_MS`, `docs/logging.md` | #266 | merged |
| 2 | Domain: `LogDestinationSender` Protocol, destination model and validation, service, queue and sender with drop and backoff, counters, system event; `logging.manage` and `logging.view` in the matrix; migration 0080; the line tap. Not yet started in web or worker: step 3 wires it with the adapters | #295 | in PR |
| 3 | Adapters: `syslog` (TLS, TCP, UDP), `splunk_hec`, `elastic`, `http`; the shipper and the line tap run in web and worker; contract tests against recorded shapes and local receivers (TCP, TLS with its own authority, UDP) | #296 | in PR |
| 4 | Admin: *Log destinations* list and page (`/admin/logging`), *Send test line*, a row on *System status*; manual topic (nl, en); `docs/admin-map.md` regenerated | #297 | in PR |
| 5 | `LOG_SHIPPING_ENABLED` kill switch (class B) for the senders and the test line, with a notice on the screen; runbook section rewritten (portal route first, host route kept, failure table); `docs/dataflow.md`, `docs/threat-model.md` (abuse cases L1–L9), `docs/architecture.md` | #299 | in PR |

## Consequences

- The superuser connects a log platform in the portal in minutes, tests it, and sees whether it
  works, without a change on the host. Installations that prefer the host route lose nothing.
- The application gains outbound connections with credentials. They follow ADR 0008 like SMTP
  and WxCC, and the threat model gets the abuse cases of step 5. A destination address can
  point at an internal service: the sender posts only log lines, with a timeout, and the audit
  event records who set the address.
- Trace lines are many. The cap of `TRACE_LEVEL_MAX_HOURS`, the rotating `json-file` driver
  (ADR 0040 part 1) and drop-oldest on the queue keep the disk and the memory bounded; the
  emergency brake of ADR 0040 part 4 also resets `LOG_LEVEL` to `INFO` at grade `critical`.
- The log platform becomes a recipient of the same masked lines that stdout carries today,
  plus, when switched on, diagnostic events and trace lines. `docs/dataflow.md` names it;
  retention there is the organisation's policy.
- `docs/logging.md` is new: the levels, the trace points, the record and the destinations in
  one place, generated in part from the code (the trace point list) so it cannot drift.
- Per-request logging with route templates, open since ADR 0002 §5, is covered by the
  `http.request` trace point.
