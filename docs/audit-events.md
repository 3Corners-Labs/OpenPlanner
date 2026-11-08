# Event schema `openplanner.event/3`

One schema for the audit log table, the container log, the CSV export and a log platform
(ADR 0040, part 2). It follows Elastic Common Schema (ECS) and the OpenTelemetry log data
model, so Elastic, Splunk or an OpenTelemetry collector reads it without mapping. The code is
`app/domain/event_schema.py`; a change of field is a new schema version. Ordinary log lines
carry the same envelope (`docs/logging.md`).

## Fields of an event record

In this order on the line and in the CSV export; the line adds `labels.*` and
`log_type: "audit"` after them.

| Field | Example | Meaning |
|---|---|---|
| `@timestamp` | `2026-10-27T14:03:12.345Z` | RFC 3339, UTC, milliseconds |
| `log.level` | `warning` | `debug`, `info`, `warning`, `error`, `critical`; for audit and system events it follows the outcome: success and skipped `info`, retry and denied `warning`, failure `error` |
| `event.category` | `audit` | `audit` (admin actions), `system` (jobs, storage), `security` (webhook and credential failures), `diagnostic` (debug) |
| `event.action` | `group.edit`, `job.send_reminder` | a permission or an event key; `docs/logging.md` says what each means |
| `message` | `An identity provider was changed. Outcome: denied (out_of_scope). Organisation acme.` | the action's sentence from the register (ADR 0103), with the outcome and reason when it is not a success and the template does not say it, and the organisation and group when the template does not name them |
| `outcome` | `retry` | our own: `success`, `denied`, `failure`, `retry`, `skipped` |
| `event.outcome` | `failure` | ECS: `success`, `failure` or `unknown` (skipped) |
| `reason` | `out_of_scope`, `smtp_unreachable` | machine-readable reason or error code |
| `organisation`, `group` | `acme`, `balie` | slugs; empty for global events |
| `booking_id` | `42` | id of the booking a system event is about; never its contents |
| `actor` | `8c1f…` or `system` | the identity provider's subject of the admin, `anonymous`, `system`, `system:cli`, `host`, `host-updater`, or an API client id |
| `actor.type` | `user` | `user`, `system`, `host`, `api` or `anonymous` (ADR 0103 point 7) |
| `actor.role` | `org_admin`, `custom.3` | for a `user`: the role key that granted the permission; empty otherwise. Never a name |
| `error.message` | `timezone 'Europe/Amsterdam' and timezone offset in '...+00:00' do not match` | the provider's own reason for a failed job (ADR 0065): Webex's `errors[].description` or `message`, email addresses masked, at most 300 characters, only from requests that carry no participant data; empty for every other event |
| `detail` | `horizon_days,name`, `update.request,from:0.3.0,to:0.4.0` | what changed: comma-separated parts, each `name:value` or a bare flag; never personal data |
| `trace.id` | `a1b2…` (32 hex) | the HTTP request or worker job that caused it; also on its log lines |
| `event_id` | `3f2b…` (32 hex) | unique per event; the same id as the row in `audit_events` |
| `service.name` | `web` | `web` or `worker` |
| `service.version` | `1.4.0` | `APP_VERSION`; empty in development |
| `installation` | `acme-prod` | `INSTALLATION_NAME`; empty when not set |
| `schema` | `openplanner.event/3` | name and version of this schema |
| `labels.<name>` | `labels.from: "0.3.0"`, `labels.what: "update.request"` | on the line only: each `name:value` part of `detail` as its own field (dots and dashes in the name become `_`, digits become numbers), the bare flags together in `labels.what`, a repeated name with its values comma-separated |

### Changes against version 2 (ADR 0103)

- `message` is the readable sentence; the provider's reason that was in `message` is now
  `error.message`. A saved search that matched `message` against a provider's text must use
  `error.message`; one that matched a code must use `event.action`.
- New: `actor.type`, `actor.role`, `labels.*`.
- The fields come in reading order, also in the CSV export.
- Ordinary log lines carry `schema`, `event.action`, `event.category` and `labels.*` too, so a
  filter on `event.action` or `event.category` sees the whole stream. Authorisation decisions
  are category `authz`.

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

`database.error` (category `security`, outcome `failure`, reason `database_error`, detail
`surface:web`, `surface:widget` or `surface:agent`, ASVS V16.3.4) is written when a request
fails on a database error that is not a busy lock: the transaction is rolled back, the
participant gets a 503 page (or `{"error": "database_unavailable"}` from the widget API) and
the event has a code and nothing else. At most one per window. When the database is gone and
the write fails too, the same event goes to stdout in the format of this page. SQLAlchemy never
puts bound values in an exception text (`hide_parameters` on every engine, ASVS V16.2.5), and
the log processor cuts any `[parameters: …]` it still meets.

ADR 0068 adds `integration.request` (category `system`): one per call to Webex or WxCC, at the
level of the booking (or group, for the probe meeting) of the trace that made it. `outcome`
success or failure, `reason` the error code (`rejected_400`, `unreachable`, ...), `booking_id`,
and `detail` `method:POST,path:/v1/meetings,status:400,ms:231,tracking:ROUTER_…,version:0.4.0`.
No body, query string or header. Kept `INTEGRATION_REQUEST_RETENTION_DAYS` (default 30). Meeting
and work item jobs with nothing to do are `skipped` with reason `already_done`, `no_video`,
`own_queue` or `no_dispatch`.

ADR 0082 adds `integration.service_app_check` (category `admin`, global): the Service App check
from *Koppelingen*, outcome `success` or `failure`, reason `passed` or `failed`; the verdicts
are in `integration_reports`, not in the event. The probe work item of a group check is
recorded as `integration.request` events at the group's level, like the probe meeting.

ADR 0082 steps 2 to 4 add these system events (category `system`; each failure is an alert of
ADR 0061 until the matching success closes it; never participant data):

| `event.action` | `outcome` and `reason` | `detail` | Level |
|---|---|---|---|
| `integration.health` | failure with reason `degraded` or `down`; success on the return to `ok` (after `integration_recovery_minutes`) or `unknown` | `platform:webex` or `platform:wxcc`, `from:<state>`, `to:<state>`, `requests:<n>`, `failures:<n>`, `why:` `failures`, `token_refresh`, `probe`, `recovering`, `recovered` or `few_requests` | organisation |
| `integration.webhooks_missing` | failure with reason `missed` when the reconcile found task states the webhooks did not bring; success when a run finds none | `count:<n>` | organisation |
| `integration.check` with reason `wxcc.probe.*` | the probe work item of a group check (ADR 0082 point 2); there is no separate `integration.probe` action: a failed probe is an `integration.check` failure whose reason starts with `wxcc.probe.` (for example `wxcc.probe.no_events`), and it makes the organisation's WxCC health `down` (`why:probe`) | the check's detail | group |
| `dispatch.fallback` | failure with reason `on` when a group falls back to its own queue; success when it returns (health `ok` again, or ended by hand) | `fallback.on` or `fallback.off` | group |
| `alert.delivery_failed` | failure, reason the sender's error code (`no_sender`, `unauthorized`, `rejected_404`, ...) | `target:<id>`, `kind:email` or `kind:webex_space`, `alert:<event.action>` | organisation (or global) |

Ending a fallback by hand is also an admin event `bookings.manage` with detail `fallback.off`.
Alert targets and the bot token are admin events under the scope's permission
(`settings.platform.manage` or `integrations.manage`) with detail `alert_target.add,id:<n>,kind:<kind>`,
`alert_target.remove,id:<n>`, `alert_target.test,id:<n>,outcome:<sent or code>`,
`alert_bot.set` or `alert_bot.remove`; the address and the token never appear. A booking that
lands in the queue during a fallback gets the timeline event `fallback_queued` (detail
`dispatch_failed` or `contact_center_down`), and `fallback_ended` when it goes back to the
contact center; a reconciled task state carries the detail `reconciled`.

ADR 0053 adds the attempts on public entry points, all with outcome `denied` and a caller
pseudonym `client:<16 hex>` in `detail` (also on `webhook.rejected`); the window then counts
per kind and caller:

| `event.action` | `reason` | `detail` |
|---|---|---|
| `request.rate_limited` | `too_many_requests` | `surface:web`, `surface:widget`, `surface:agent` or `surface:links` (unknown management links), `client:…` |
| `request.rejected` | `honeypot`, `cross_site`, `origin_mismatch` | `client:…` |
| `admin.login` (actor `anonymous`) | the identity error code, e.g. `invalid_state`, `invalid_token`, `nonce_mismatch` | `client:…` |
| `token.rejected` | `unknown_token` (machine API), `unknown_management_link` (`/b/…`) | `client:…` |

The pseudonym is an HMAC of the IP address (IPv6 by /64), never the address; `unknown` when
there is none, `many` once `SECURITY_EVENT_MAX_SOURCES` callers are tracked.

ADR 0044 adds two warnings about volume (category `system`, outcome `failure`, level `error`;
`app/domain/activity_alert.py`). Each is written once when one person's successful events of
one action within an hour pass a class C threshold, at the organisation and group of the event
that crossed it. They never refuse the action and are not alerts of ADR 0061, so a rule on the
log platform is what turns them into a page:

| `event.action` | `reason` | `detail` | Counts |
|---|---|---|---|
| `planner.changes_alert` | `too_many_changes` | `actor:<subject>,changes:<n>` | `bookings.manage` changes (a planner's change, a retried call, a fallback ended by hand), above `planner_changes_alert_per_hour` |
| `booking.contact_reveals_alert` | `too_many_contact_reveals` | `actor:<subject>,reveals:<n>` | requests for a participant's contact details (detail `booking.contact_viewed`, the only audited success under `bookings.view`), above `contact_reveals_alert_per_hour` |

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
