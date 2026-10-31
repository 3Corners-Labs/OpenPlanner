# Event schema `openplanner.event/2`

One schema for the audit log table, the container log, the CSV export and a log platform
(ADR 0040, part 2). It follows Elastic Common Schema (ECS) and the OpenTelemetry log data
model, so Elastic, Splunk or an OpenTelemetry collector reads it without mapping. The code is
`app/domain/event_schema.py`; a change of field is a new schema version.

## Fields of an event record

| Field | Example | Meaning |
|---|---|---|
| `schema` | `openplanner.event/2` | name and version of this schema; version 2 (ADR 0065) added `message`, nothing else changed |
| `event_id` | `3f2b…` (32 hex) | unique per event; the same id as the row in `audit_events` |
| `@timestamp` | `2026-10-27T14:03:12.345Z` | RFC 3339, UTC, milliseconds |
| `log.level` | `warning` | `debug`, `info`, `warning`, `error`, `critical`; for audit and system events it follows the outcome: success and skipped `info`, retry and denied `warning`, failure `error` |
| `event.category` | `audit` | `audit` (admin actions), `system` (jobs, storage), `security` (webhook and credential failures), `diagnostic` (debug) |
| `event.action` | `group.edit`, `job.send_reminder` | a permission or an event key |
| `event.outcome` | `failure` | ECS: `success`, `failure` or `unknown` (skipped) |
| `outcome` | `retry` | our own: `success`, `denied`, `failure`, `retry`, `skipped` |
| `reason` | `out_of_scope`, `smtp_unreachable` | machine-readable reason or error code |
| `organisation`, `group` | `gemeente`, `burgerzaken` | slugs; empty for global events |
| `booking_id` | `42` | id of the booking a system event is about; never its contents |
| `actor` | `8c1f…` or `system` | the identity provider's subject of the admin, `anonymous`, or `system` |
| `trace.id` | `a1b2…` (32 hex) | the HTTP request or worker job that caused it; also on its log lines |
| `service.name` | `web` | `web` or `worker` |
| `service.version` | `1.4.0` | `APP_VERSION`; empty in development |
| `installation` | `gemeente-prod` | `INSTALLATION_NAME`; empty when not set |
| `detail` | `horizon_days,name` | what changed, as machine keys; never values |
| `message` | `timezone 'Europe/Amsterdam' and timezone offset in '...+00:00' do not match` | the provider's own reason for a failed job (ADR 0065): Webex's `errors[].description` or `message`, email addresses masked, at most 300 characters, only from requests that carry no participant data; empty for every other event |

## Updates and versions (ADR 0029, 0065)

Every update event is global (`system.update`), with the versions in its detail so the history
can be read from the log alone: `update.request,from:0.3.0,to:0.4.0` (actor: the superuser's
subject), `update.auto_request,…` (actor `system`, `UPDATE_AUTO_APPLY`), the updater's steps
`update.start`, `update.pulling`, `update.verifying`, `update.backing_up`, `update.starting`,
`update.rolling_back` and outcomes `update.succeeded`, `update.failed`, `update.rolled_back`
(actor `host-updater`, `reason` the updater's code), the same for `restore.*`, and
`request.cancel`. The web process notes at start-up when it runs another version or commit
than the last time: `system.version` with detail
`version.changed,from:<version>,to:<version>,commit:<12 hex>`, actor `host-updater` when a
running update request brought it, else `host` (the installer, or a change by hand on the
server). The updates page shows these events, filtered on the milestones, with who did it.

## On the log platform (ADR 0034)

After commit, each audit, system and security event is written once to stdout as the record
above plus `"log_type": "audit"`, for the log platform to route; diagnostic events stay inside
the installation. Security events (`event.category = security`): `webhook.rejected` (reason
`invalid_signature`, detail `source:webex` or `source:wxcc`) and `webex.token_refresh`
(reason `unauthorized` or `unreachable`, detail `status:<n>` or an exception type), at most one
per kind per window, with `suppressed:<n>` for the ones held back. Shipping is described in
`docs/runbook.md`.

ADR 0068 adds `integration.request` (category `system`): one per call to Webex or WxCC, at the
level of the booking (or group, for the probe meeting) of the trace that made it. `outcome`
success or failure, `reason` the error code (`rejected_400`, `unreachable`, ...), `booking_id`,
and `detail` `method:POST,path:/v1/meetings,status:400,ms:231,tracking:ROUTER_…,version:0.4.0`.
No body, query string or header. Kept `INTEGRATION_REQUEST_RETENTION_DAYS` (default 30). Meeting
and work item jobs with nothing to do are `skipped` with reason `already_done`, `no_video`,
`own_queue` or `no_dispatch`.

ADR 0053 adds the attempts on public entry points, all with outcome `denied` and a caller
pseudonym `client:<16 hex>` in `detail` (also on `webhook.rejected`); the window then counts
per kind and caller:

| `event.action` | `reason` | `detail` |
|---|---|---|
| `request.rate_limited` | `too_many_requests` | `surface:web`, `surface:agent` or `surface:links` (unknown management links), `client:…` |
| `request.rejected` | `honeypot`, `cross_site`, `origin_mismatch` | `client:…` |
| `admin.login` (actor `anonymous`) | the identity error code, e.g. `invalid_state`, `invalid_token`, `nonce_mismatch` | `client:…` |
| `token.rejected` | `unknown_token` (machine API), `unknown_management_link` (`/b/…`) | `client:…` |

The pseudonym is an HMAC of the IP address (IPv6 by /64), never the address; `unknown` when
there is none, `many` once `SECURITY_EVENT_MAX_SOURCES` callers are tracked.

## What never appears

No personal data of participants (name, email, phone, note, join links) and no admin display
names or email addresses: `actor` is a subject identifier, which the organisation can resolve
in its identity provider. The masking processor of `app/logging.py` still runs over every log
line as a safety net, and a test asserts that no schema field is a PII field.

## Ordinary log lines

Log lines that are not events share the envelope: `@timestamp`, `log.level`, `message` (the
event key, e.g. `job.sent`), `service.name`, `service.version`, `installation`, and inside a
request or job `trace.id`, `organisation` and `group`. See `docs/runbook.md`, *Log lines and
the event schema*.
