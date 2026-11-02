# Logging: levels, the record, the actions and where lines go

Generated from `app/domain/log_events.py` by
`python -m app.cli logging-doc > docs/logging.md`; do not edit by hand. CI fails when this
file differs from the code.

What OpenPlanner writes to its log, at which level, in which shape, and how it leaves the
installation. Decisions: ADR 0040 (one event schema, levels), ADR 0034 (audit events on the
log stream), ADR 0042 §6 (`LOG_LEVEL` with expiry), ADR 0085 (the `TRACE` level, the trace
points and the log destinations in the portal), ADR 0103 (the register of actions).

## The record

Every line is one JSON object on stdout in the `openplanner.event/2` envelope (ADR 0040,
`app/logging.py`): `@timestamp` (RFC 3339 UTC, milliseconds), `log.level`, `message`,
`service.name` (`web` or `worker`), `service.version`, `installation` (`INSTALLATION_NAME`)
and, inside a request or job, `trace.id`, `organisation` and `group`. Every line passes the
PII mask: the fields of `app/domain/privacy.py` are replaced by `***` at any depth, as a
safety net on top of the rule that code never passes personal data to a logger (semgrep,
`.semgrep/rules.yml`). The access log of uvicorn is off (paths carry management tokens) and
the `http.request` and `outbound.request` trace points carry what is safe instead of the
request lines of httpx (full URL) and the statement lines of the SQL driver (bound values).
Libraries never log below `warning` (see *Levels*).

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

`LOG_LEVEL` governs our own loggers only: `openplanner.*` (including the trace logger),
the structlog loggers of the app and `uvicorn.error`. Every other logger (`httpcore`,
`aiosqlite`, `sqlalchemy`, a library added later) lets nothing below `warning` through, at any
`LOG_LEVEL`, after a live level change, and even if the library sets its own logger to
`DEBUG`: libraries log at DEBUG what they handle (bound SQL values, response headers), and a
line can leave the host through log shipping (ASVS V16.2.5). It is a fixed protection, not a
setting (ADR 0042: a protection is never switched off in C or D). An allowlist, so a new
library is covered without a change here. Tests: `tests/test_logging.py`.

## Trace points

The only sources of `trace` lines are the points below, written through
`app.logging.tracer.trace(point, **fields)`. A point takes its registered fields and
nothing else: the `Tracer` refuses any other field, and a test feeds personal data into
every field and asserts the stream is clean. Adding a trace point is a change to the
register and the PII test together.

| Point | Fields | Where | Never |
|---|---|---|---|
| `http.request` | `method`, `route`, `status`, `ms` | One HTTP request, from `app/api/trace.py`; `route` is the matched route template (`/g/{slug}`), empty for a path that matched no route. | the raw path (`/b/{token}` carries the management token), the query, the client address |
| `job.run` | `type`, `attempt`, `ms` | One job the worker ran (`app/jobs/runner.py`); while diagnostic mode is on for its group it is also a diagnostic event with the same values in `detail`. | the booking's data |
| `job.step` | `type`, `step`, `ms` | A step of a message job: `load`, `compose`, `send`. | the recipient, the message |
| `outbound.request` | `method`, `host`, `status`, `ms`, `error` | One outgoing HTTP call of web or worker (`app/adapters/http_trace.py`); `error` is the exception type when no response came. | the URL, a query, a header, a body |
| `sql.slow` | `statement`, `table`, `ms` | A statement slower than `SQL_SLOW_MS` (`app/adapters/db/engine.py`); `statement` is the kind (`Select`, `Insert`, `raw`), `table` the first table. | the statement text, parameters |
| `template.render` | `template`, `ms` | Every page rendered through `app.web.templates.render`. | the context |

## Actions

Every action the application logs or audits is registered once in
`app/domain/log_events.py` with its category, level, fields, an English message, what it
means and what to do. A log call that names an unregistered action, passes an undeclared
field or logs at an unlisted level fails `tests/test_log_events_scan.py`; an audited action
that is not registered fails the test run (`tests/conftest.py`). *Level* `outcome` means the
level follows the event's outcome: success and skipped `info`, retry and denied `warning`,
failure `error`. The fields in braces fill the message; `actor`, `organisation`, `group`,
`booking_id`, `outcome`, `reason` and `detail` are on every event that has them.

### Security

| Action | Level | Fields | Message | Meaning | What to do |
|---|---|---|---|---|---|
| `admin.login.mfa_missing` | outcome | – | A login was refused: no second factor shown. | The identity provider's token showed no MFA (ADR 0100). | Check the provider's MFA policy for the user. |
| `agent_api.invalid_request` | `info` | `fields` | The machine API got an invalid request (fields {fields}). | A request to `/api/v1` did not validate; only the field names are logged. | Nothing. |
| `agent_api.refused` | `info` | `reason` | A machine API caller was refused: {reason}. | The bearer token was missing, unknown, expired or from a disallowed address. | Repeated refusals from one client: check its credentials, or a guessing attempt. |
| `database.error` | outcome | – | A request failed on a database error. | A request hit a database error that is not a busy lock; the participant got a 503 (ASVS V16.3.4). | Look at the container log for the exception type; check the disk. |
| `idp.discovery_failed` | `warning` | `error` | The identity provider's discovery document could not be read: {error}. | An organisation's identity provider did not answer its `.well-known` address. | Check the provider's address under the organisation's *Login*. |
| `idp.discovery_refused` | `warning` | `reason` | The identity provider's address was refused: {reason}. | The address points to a host the outbound guard does not allow. | Correct the provider's address. |
| `oidc.discovery_failed` | `warning` | `error` | The login provider's discovery document could not be read: {error}. | Admin login cannot start until the provider answers. | Run `python -m app.cli oidc-check` on the host. |
| `oidc.invalid_token` | `warning` | `error` | An ID token was refused: {error}. | A login returned a token that failed a check (signature, audience, expiry, `azp`). | One-off: nothing. Repeated: check the provider's keys and client configuration. |
| `oidc.issuer_mismatch` | `warning` | – | The login provider's issuer does not match the configured one. | The discovery document names another issuer than `OIDC_ISSUER` (exact match). | Correct `OIDC_ISSUER` or the provider; `python -m app.cli oidc-check` shows both. |
| `oidc.mfa_missing` | `warning` | `provider` | A login through {provider} showed no second factor. | The token carried no `acr` or `amr` value that shows MFA (ADR 0100). | Check the provider's MFA policy for the user. |
| `oidc.no_role` | `warning` | `actor`, `claims_present`, `entitlements_seen`, `missing_claim`, `role_values_seen` | A login carried no known role (missing claim: {missing_claim}). | The user signed in but no claim value maps to a role; only claim names and counts are logged. | Compare the claims with the mapping under *Login*; `oidc-check` prints the mapping. |
| `oidc.no_scope` | `warning` | `actor`, `claims_present`, `missing_claim` | A login carried a role without its organisation or group (missing claim: {missing_claim}). | A scoped role needs its scope claim; the login is refused. | Add the scope claim at the identity provider. |
| `oidc.subject_refused` | `warning` | `reason` | A login's subject was refused: {reason}. | The `sub` claim is empty, too long or has characters that are not allowed. | Check the identity provider's subject format. |
| `oidc.token_exchange_failed` | `warning` | `error` | The login code could not be exchanged for a token: {error}. | The provider's token endpoint refused or did not answer. | Check the client secret and the provider's status. |
| `oidc.too_many_entitlements` | `warning` | `actor`, `values` | A login carried {values} entitlements, more than allowed. | The token had more entitlement values than `oidc_max_entitlements`; the login is refused. | Reduce the user's groups at the provider, or raise the limit. |
| `request.rate_limited` | outcome | – | A caller went over a rate limit. | Too many requests from one address (ADR 0053); the caller is a pseudonym. | Repeated: a bot or a misconfigured client. |
| `request.rejected` | `info`, outcome | `expected`, `origin`, `reason`, `sec_fetch_site` | A request was rejected before it reached the application: {reason}. | A honeypot field, a cross-site POST or a foreign `Origin` (ADR 0053). | Repeated `origin_mismatch` from your own site: check the proxy's `Host` header. |
| `settings.secret_file_readable` | `warning` | `variable` | The secret file of {variable} can be read by others. | A bootstrap secret file (`X_FILE`, ADR 0042 §9) has loose permissions. | Run `chmod 600` on the file. |
| `token.rejected` | outcome | – | A token or link that matches nothing was used: {reason}. | A guessed, forged or long-expired token or management link (ADR 0053). | Repeated from one caller: a guessing attempt. |
| `webex.token_refresh` | outcome | – | Refreshing the Webex token failed: {reason}. | Webex refused or could not be reached for a token refresh (at most one per window). | `unauthorized`: authorise the service app again. `unreachable`: check the network. |
| `webhook.rejected` | `warning`, outcome | `source` | A webhook from {source} was rejected: its signature did not verify. | A webhook with a wrong signature or an old timestamp (CLAUDE.md, 300 s). | Repeated: check the webhook secret, or someone is posting to the endpoint. |
| `widget.origin_refused` | `info` | `group`, `origin` | The widget of {group} refused origin {origin}. | A site that is not on the group's allow-list tried to use the widget. | Add the site under the group's *Embedding*, or ignore when it is not yours. |

### Authorisation decisions

| Action | Level | Fields | Message | Meaning | What to do |
|---|---|---|---|---|---|
| `agent_api.decision` | `info` | `allowed`, `client`, `scope` | Machine API client {client}: scope {scope}, allowed {allowed}. | The decision on a machine API call (ADR 0023). | Nothing. |
| `authz.decision` | `info` | `actor`, `allowed`, `permission`, `reason`, `target_group`, `target_organisation` | Permission {permission}: allowed {allowed} ({reason}). | The decision on an admin request (ADR 0014); a denial is also an audit event. | Nothing; a destination may leave the category `authz` out. |
| `authz.maintenance_refused` | `info` | `actor`, `permission`, `target_group`, `target_organisation` | Permission {permission} refused during maintenance. | A change was refused because maintenance mode is on (ADR 0084). | Nothing. |

### Admin actions (audit)

| Action | Level | Fields | Message | Meaning | What to do |
|---|---|---|---|---|---|
| `admin.login` | `info`, outcome | `roles` | Admin {actor} signed in. | An admin signed in; the subject is the identity provider's, never a name. | Nothing. |
| `admin.logout` | outcome | – | Admin {actor} signed out. | An admin ended their session. | Nothing. |
| `admin.session_ended` | outcome | – | An admin session ended: {reason}. | A session ended by expiry, idle time or the session limit (ADR 0098). | Nothing. |
| `admin.sessions_ended_all` | outcome | – | All admin sessions were ended. | The superuser or the host ended every session at once. | Nothing; the audit log shows who did it. |
| `admin.sessions_ended_by_admin` | outcome | – | An admin's sessions were ended by another admin. | A staff manager signed someone out everywhere. | Nothing; the audit log shows who did it. |
| `admin.sessions_ended_by_self` | outcome | – | Admin {actor} ended their other sessions. | An admin signed out their other devices. | Nothing. |
| `api.call` | outcome | – | Machine API call: {outcome}. | A call to the machine API (ADR 0023). | Nothing. |
| `api.token` | outcome | – | Machine API token issued: {outcome}. | A machine API client got an access token. | Nothing. |
| `audit.export` | outcome | – | The audit log was exported. | An admin downloaded a CSV of the audit log (ADR 0040 point 10). | Nothing; the audit log shows who did it. |
| `auth.ambiguous_claims` | outcome | – | A login carried claims that map to more than one organisation. | The login is refused because the scope is ambiguous. | Fix the user's claims at the identity provider. |
| `auth.entitlement_mismatch` | outcome | – | A login's entitlements do not match its organisation's provider. | An organisation's identity provider sent entitlements for another organisation (ADR 0083). | Check the entitlement prefix and the provider's mapping. |
| `auth.entitlement_refused` | outcome | – | A login's entitlement was refused: {reason}. | An entitlement value did not match the installation's prefix or a known role. | Check the entitlement values at the identity provider. |
| `group.idp_key_changed` | outcome | – | A group's identity provider key changed. | The group's staff now sign in through another provider. | Nothing; the audit log shows who did it. |
| `idp.created` | outcome | – | An identity provider was added. | An organisation registered a login provider (ADR 0083). | Nothing; the audit log shows who did it. |
| `idp.deleted` | outcome | – | An identity provider was deleted. | An organisation removed a login provider. | Nothing; the audit log shows who did it. |
| `idp.disabled` | outcome | – | An identity provider was switched off. | Its users can no longer sign in. | Nothing; the audit log shows who did it. |
| `idp.enabled` | outcome | – | An identity provider was switched on. | Its users can sign in. | Nothing. |
| `idp.key_rotated` | outcome | – | An identity provider's client secret was replaced. | The sealed client secret changed. | Nothing. |
| `idp.test_login` | outcome | – | A test login through an identity provider: {outcome}. | An admin tried the provider before switching it on. | Nothing. |
| `idp.updated` | outcome | – | An identity provider was changed. | Its settings changed; `detail` names the fields. | Nothing. |
| `idp_mapping.exported` | outcome | – | The login mapping was exported. | An admin downloaded the claim mapping for the identity provider team. | Nothing. |
| `integration.service_app_check` | outcome | – | The Webex service app was checked: {outcome}. | An admin checked the platform's Webex service app. | Nothing. |
| `logging.destination` | outcome | – | Log destination {detail}: {outcome}. | A log destination was created, changed, tested or deleted (audit), or started or stopped failing (system, ADR 0085). | On failure: open *Log destinations*; it shows the last error. |
| `organisation.closure` | outcome | – | An organisation-wide closure changed. | An emergency closure for the whole organisation was set or ended (ADR 0024). | Nothing; the audit log shows who did it. |
| `organisation.idp_key_changed` | outcome | – | An organisation's identity provider key changed. | The organisation's staff now sign in through another provider. | Nothing; the audit log shows who did it. |
| `role.created` | outcome | – | A role was created. | A custom role was added to the catalogue (ADR 0096). | Nothing; the audit log shows who did it. |
| `role.deleted` | outcome | – | A role was deleted. | A custom role was removed. | Nothing; the audit log shows who did it. |
| `role.updated` | outcome | – | A role was changed. | A custom role's rights changed. | Nothing; the audit log shows who did it. |
| `simulator.event` | outcome | – | A simulated provider event was played. | An admin played an event of the integration simulator. | Nothing. |
| `simulator.prepare` | outcome | – | A simulated booking was prepared. | An admin prepared a booking for the integration simulator. | Nothing. |
| `staff.created` | outcome | – | A staff member was added. | A person was added to the staff directory (ADR 0046). | Nothing; the audit log shows who did it. |
| `staff.erased` | `info`, outcome | `staff` | Staff erased after their retention. | Suspended staff past their retention were erased (ADR 0046). | Nothing. |
| `staff.membership_changed` | outcome | – | A staff member's roles or groups changed. | `detail` names the role or grant added or removed. | Nothing; the audit log shows who did it. |
| `staff.reinstated` | outcome | – | A staff member was reinstated. | A suspended person can sign in again. | Nothing; the audit log shows who did it. |
| `staff.suspended` | outcome | – | A staff member was suspended. | The person can no longer sign in; their sessions ended. | Nothing; the audit log shows who did it. |
| `system.maintenance` | outcome | – | Maintenance mode changed. | Maintenance mode was switched on or off, or a change was refused (ADR 0084). | Nothing; the audit log shows who did it. |

### System events

| Action | Level | Fields | Message | Meaning | What to do |
|---|---|---|---|---|---|
| `booking_rows.rollup` | `info`, outcome | `days`, `rows`, `skipped` | {rows} old booking rows merged into {days} daily counts. | Booking rows past their retention became daily counts and were deleted (ADR 0102). | Nothing. |
| `closure.retention_notice` | outcome | – | A closure is near its maximum retention. | An open-ended closure is close to `closure_max_retention_days` (ADR 0102). | End or shorten the closure. |
| `diagnostics.brake` | outcome | – | Diagnostic mode was switched off: the disk is nearly full. | The emergency brake of ADR 0040 point 13. | Free disk space on the host. |
| `group.erased` | outcome | – | A deleted group was erased. | A group's data was removed after its deletion period. | Nothing. |
| `integration.check` | outcome | – | Integration check of a group: {outcome} ({reason}). | A group's Webex and Contact Center connection was checked, by the worker or an admin. | On failure: open the group's *Integrations*; it names the provider's reason. |
| `integration.request` | outcome | – | A provider request for booking {booking_id}: {outcome}. | One request in a booking's chain to Webex or Contact Center (ADR 0065). | On failure: the booking's timeline shows the provider's reason. |
| `maintenance.overdue` | `warning`, outcome | `hours` | Maintenance mode has been on for {hours} hours. | Maintenance mode is on longer than planned (ADR 0084). | Switch it off when the work is done: `python -m app.cli maintenance off`. |
| `planner.changes_alert` | outcome | – | Planners were told about changes. | The alert about changed bookings went out (ADR 0044). | Nothing. |
| `public_address` | outcome | – | Public address round trip: {outcome} ({reason}). | The worker's round trip through the public address (ADR 0086 point 2). | On failure: *System status* names the finding. |
| `retention.sweep` | `warning`, outcome | `failed`, `overdue`, `purged` | Retention sweep: {overdue} bookings overdue, {purged} deleted, {failed} failed. | The nightly sweep found bookings whose personal data was not yet deleted and deleted them. | When `failed` is not zero: look at the worker's log; see also `retention.backlog`. |
| `storage.check` | outcome | – | Storage check: {outcome} ({reason}). | The worker's disk check changed grade (ADR 0040 point 3). | On failure: free disk space on the host. |
| `system.update` | outcome | – | Update {detail}: {outcome}. | A step of a requested update or restore (ADR 0059, 0067); `detail` holds the versions. | On failure: open *Updates*; the runbook explains a rollback. |
| `system.version` | outcome | – | The running version changed: {detail}. | At start-up the web process saw another version or commit than last time. | Nothing. |
| `tls.certificate` | `warning`, outcome | `finding`, `host` | Certificate check of {host}: {finding}. | The certificate the public address serves expires soon or does not fit (ADR 0064). | Renew or replace the certificate (*Certificate* in the portal, or the host's own proxy). |

### Operations

| Action | Level | Fields | Message | Meaning | What to do |
|---|---|---|---|---|---|
| `address.probe_failed` | `warning` | `error` | The public address probe failed: {error}. | The round trip of *System status* (ADR 0086) could not reach this installation through its public address. | Check `PUBLIC_BASE_URL`, DNS and the proxy; *System status* shows the finding. |
| `address.proxy_event_failed` | `warning` | `error` | The proxy event could not be written: {error}. | A request came through an unexpected proxy hop and the system event about it failed to save; the request itself went on. | Look at the container log around it; repeated failures point at the host or the database. |
| `address.round_trip` | `warning` | `finding`, `kind` | The public address check found {finding} ({kind}). | The worker's round trip through the public address (ADR 0086) did not come back as expected. | Open *System status*: it names the finding and the hop that answered. |
| `address.watch_failed` | `warning` | `error` | The public address check did not run: {error}. | The worker could not run the address round trip; often the schema is not migrated yet. | Look at the container log around it; repeated failures point at the host or the database. |
| `audit.emit_failed` | `error` | `error` | An audit event could not be written to the log stream: {error}. | The event is in the audit table, but its copy on stdout (and so on the log platform) failed. | Compare the audit log in the portal with the log platform for the period. |
| `backups.prune_failed` | `warning` | `error` | Old back-ups could not be removed: {error}. | Housekeeping could not prune `data/backups`. | Check the data volume's permissions and free space. |
| `backups.pruned` | `info` | `backups` | {backups} old back-ups removed. | Housekeeping removed back-ups older than the retention. | Nothing. |
| `booking_rows.rollup_failed` | `error` | `error` | Old booking rows could not be merged into daily counts: {error}. | The rollup of ADR 0102 failed this round; the rows stay until the next. | Look at the container log around it; repeated failures point at the host or the database. |
| `database.reclaim_failed` | `warning` | `error` | Free database pages could not be given back: {error}. | The incremental vacuum failed; the file keeps its size until the next round. | The step is skipped this round and tried again at the next; nothing else stops. |
| `database.reclaimed` | `info` | `pages` | {pages} free database pages given back to the disk. | The incremental vacuum shrank the database file (ADR 0040 point 2). | Nothing. |
| `database.unreachable` | `warning` | `error` | The database did not answer: {error}. | The readiness check could not reach SQLite. | Check the data volume and the disk; `/readyz` answers 503 meanwhile. |
| `diagnostics.brake_failed` | `warning` | `error` | Diagnostic mode could not be switched off for a full disk: {error}. | The emergency brake of ADR 0040 point 13 failed, probably because the disk is full. | Free disk space on the host at once. |
| `diagnostics.braked` | `warning` | `modes` | Diagnostic mode switched off in {modes} places because the disk is nearly full. | The emergency brake of ADR 0040 point 13 ended diagnostic mode. | Free disk space; switch diagnostic mode on again when needed. |
| `diagnostics.failed` | `warning` | `error`, `job_id` | The diagnostic event of job {job_id} could not be written: {error}. | Debug detail of a job was lost; the job itself went on. | Nothing. |
| `direct.deferred` | `warning` | `booking_id`, `error` | The call for booking {booking_id} is left to the worker: {error}. | A start-now call could not be set up during the request; the worker's jobs retry it. | Watch the booking's timeline; repeated failures show the provider's reason there. |
| `direct.left` | `info` | `agent_present`, `booking_id`, `meeting_id` | The participant of booking {booking_id} left the queue. | A participant left a start-now call before or after it began. | Nothing. |
| `dns.lookup_failed` | `warning` | `domain`, `error` | DNS lookup for {domain} failed: {error}. | The email deliverability check (ADR 0073) could not resolve a record. | Check the host's DNS resolver; the check on *Channels* stays incomplete meanwhile. |
| `group.erase_done` | `info` | `groups` | {groups} deleted groups erased. | Housekeeping erased groups whose deletion period ended. | Nothing. |
| `group.erase_failed` | `warning` | `error` | Deleted groups could not be erased: {error}. | The erasure of deleted groups failed this round. | The step is skipped this round and tried again at the next; nothing else stops. |
| `i18n.missing_key` | `warning` | `key`, `locale` | Text {key} is missing in {locale}. | A page asked for a text that has no translation; the key is shown instead (once per process and locale). | Report it: a catalogue entry is missing. |
| `i18n.overrides_refresh_failed` | `warning` | `error` | Organisation texts could not be reloaded: {error}. | The pages keep the texts they had; a change in the portal shows later. | The step is skipped this round and tried again at the next; nothing else stops. |
| `instance.prepare_failed` | `warning` | `error` | The instance identity could not be prepared: {error}. | At start-up the instance key for the address round trip was not ready; often the schema is not migrated yet. | Look at the container log around it; repeated failures point at the host or the database. |
| `integration.check_failed` | `warning` | `error`, `group` | The integration check did not run: {error}. | The scheduled or manual check of a group's Webex and Contact Center connection crashed. | Look at the container log around it; repeated failures point at the host or the database. |
| `integration.checked` | `info` | `groups` | Integrations of {groups} groups checked. | The worker's scheduled integration check ran. | Nothing. |
| `integration.refused` | `warning` | `path`, `reason`, `status`, `tracking_id` | The provider refused {path} with {status}: {reason}. | Webex answered a request with an error; `tracking_id` is Webex's own reference. | Look up `tracking_id` with Webex support when the reason does not explain it. |
| `integration.unreachable` | `warning` | `error` | A provider could not be reached: {error}. | An outgoing call to Webex, the SMS gateway or the release feed got no answer. | Check the host's outbound network and the provider's status page. |
| `job.checkpoint_failed` | `warning` | `error` | The WAL checkpoint after a purge failed: {error}. | The purged data is gone from the database, but old pages stay in the WAL file until the next checkpoint. | The step is skipped this round and tried again at the next; nothing else stops. |
| `job.failed` | `error` | `error`, `job_id`, `type` | Job {job_id} ({type}) failed for good: {error}. | A job ran out of attempts or failed in a way that does not retry. | Open the booking's timeline; the system log has the provider's reason. |
| `job.lapsed` | `info` | `booking_id`, `job_id` | Booking {booking_id} lapsed (job {job_id}). | A booking that waited for a new time or a confirmation passed its deadline. | Nothing. |
| `job.purged` | `info` | `booking_id`, `job_id` | Personal data of booking {booking_id} deleted (job {job_id}). | The retention job deleted the personal fields of a booking (ADR 0102). | Nothing. |
| `job.retry` | `warning` | `attempt`, `error`, `job_id` | Job {job_id} failed (attempt {attempt}) and will be retried: {error}. | A job failed with a retryable error and is planned again with backoff. | Nothing, unless it ends in `job.failed`. |
| `job.sent` | `info` | `channel`, `job_id`, `type` | Job {job_id} ({type}) sent its message by {channel}. | A message job handed its message to the email or SMS provider. | Nothing. |
| `job.skipped` | `info` | `job_id`, `reason` | Job {job_id} skipped: {reason}. | A job found nothing to do: the booking moved, was cancelled, or has no recipient. | Nothing. |
| `maintenance.watch_failed` | `warning` | `error` | The maintenance check did not run: {error}. | The worker could not check whether maintenance mode is overdue. | Look at the container log around it; repeated failures point at the host or the database. |
| `request_log.failed` | `warning` | `error` | A provider request could not be recorded: {error}. | The request chain of ADR 0065 lost one entry; the request itself went on. | Nothing. |
| `retention.backlog` | `error` | `bookings`, `failed` | {bookings} bookings still hold personal data past their retention ({failed} failed). | The nightly sweep found bookings whose personal data should have been deleted 48 hours ago. | Open *Retention* in the portal and the worker's log; this is a privacy incident when it persists. |
| `retention.checkpoint_failed` | `warning` | `error` | The WAL checkpoint after the retention sweep failed: {error}. | The deleted data is gone from the database; old pages stay in the WAL until the next checkpoint. | The step is skipped this round and tried again at the next; nothing else stops. |
| `retention.closure_notice` | `warning` | `closures` | {closures} closures near their maximum retention. | Open-ended closures are close to `closure_max_retention_days` (ADR 0102). | End or shorten the closures; their bookings are purged at the maximum. |
| `retention.sweep_failed` | `error` | `error` | The retention sweep failed: {error}. | The nightly check for personal data past its retention did not run. | Look at the worker's log at once; the sweep is the safety net of the purge. |
| `security_log.failed` | `warning` | `error` | A security event could not be written: {error}. | A refusal or failure was not saved to the security log; it is on stdout instead. | Look at the container log around it; repeated failures point at the host or the database. |
| `sessions.clean_failed` | `warning` | `error` | Expired admin sessions could not be removed: {error}. | Housekeeping could not delete expired sessions; they stay unusable. | The step is skipped this round and tried again at the next; nothing else stops. |
| `sessions.cleaned` | `info` | `sessions` | {sessions} expired admin sessions removed. | Housekeeping deleted admin sessions past their lifetime. | Nothing. |
| `settings.portal_values_refused` | `warning` | `version` | Portal settings version {version} refused; the previous values stay. | A combination of portal settings broke a rule of ADR 0042 and was not applied. | Open *Advanced settings*: the refused combination is shown there. |
| `settings.reload_failed` | `warning` | `error` | Portal settings could not be reloaded: {error}. | The process keeps the settings it had. | The step is skipped this round and tried again at the next; nothing else stops. |
| `settings_key.missing` | `warning` | – | The settings encryption key is missing. | An integration secret was asked for, but `SETTINGS_ENCRYPTION_KEY` is not set. | Set the key on the host (runbook, *Rotating secrets*). |
| `sms.accepted` | `info` | `correlation_id` | The SMS gateway accepted a message ({correlation_id}). | Webex Connect took the message; `correlation_id` is its reference. | Nothing. |
| `staff.erase_failed` | `warning` | `error` | Staff past their retention could not be erased: {error}. | Housekeeping could not erase suspended staff (ADR 0046). | The step is skipped this round and tried again at the next; nothing else stops. |
| `storage.degraded` | `warning`, `error` | `database_mb`, `free_percent`, `grade`, `reason` | Disk space is {grade}: {free_percent}% free, database {database_mb} MB ({reason}). | The worker measured the data volume below a limit of ADR 0040 point 3; logged on change and then hourly. | Free disk space on the host (old images: `docker image prune`); at `critical` diagnostic mode stops. |
| `storage.record_failed` | `warning` | `error` | The storage check could not be saved: {error}. | The system event about the disk failed, often because the disk is full; this line is the fallback. | Free disk space on the host. |
| `storage.recovered` | `info` | – | Disk space is back within its limits. | The data volume is healthy again after a `storage.degraded`. | Nothing. |
| `system_log.bind_failed` | `warning` | `error`, `job_id` | Job {job_id} could not be tagged with its group: {error}. | The job's log lines lack organisation and group; the job itself went on. | Nothing. |
| `system_log.failed` | `warning` | `error`, `job_id` | The system event of job {job_id} could not be written: {error}. | The job's outcome is settled, but the booking's timeline misses it. | Look at the container log around it; repeated failures point at the host or the database. |
| `system_log.prune_failed` | `warning` | `error` | Old system events could not be removed: {error}. | Pruning of the system log failed this round. | The step is skipped this round and tried again at the next; nothing else stops. |
| `system_log.pruned` | `info` | `removed` | {removed} old system events removed. | The system log was pruned to its retention (ADR 0040 point 12). | Nothing. |
| `tls.handshake_failed` | `warning` | `error`, `host` | The TLS handshake with {host} failed: {error}. | The certificate check could not complete a handshake. | Check the proxy's certificate and protocols; *System status* shows the finding. |
| `tls.unreachable` | `warning` | `error`, `host` | {host} could not be reached for the certificate check: {error}. | The certificate check got no connection. | Check the address and the proxy. |
| `tls.volume.key_imported` | `info` | – | An imported private key was placed on the certificate volume. | The key of an uploaded certificate is now on the host volume (ADR 0070). | Nothing. |
| `tls.volume.key_promoted` | `info` | – | The key of the signed request was promoted for use. | The certificate for the pending request arrived; its key becomes the serving key. | Nothing. |
| `tls.volume.key_unreadable` | `warning` | `file` | Key file {file} on the certificate volume cannot be read. | A key file is damaged or not a key. | Replace the certificate through the portal (*Certificate*). |
| `tls.volume.no_key_to_serve_with` | `warning` | – | A certificate chain is ready but there is no key to serve it with. | The proxy cannot use the new chain without its private key. | Create a new request or upload the certificate with its key. |
| `tls.volume.publish_failed` | `warning` | `error` | The certificate could not be published to the proxy: {error}. | Writing the chain to the certificate volume failed. | Check the volume's permissions and free space. |
| `tls.volume.published` | `info` | `fingerprint` | Certificate {fingerprint} published to the proxy. | The proxy picks up the new chain. | Nothing. |
| `tls.volume.request_made` | `info` | – | A certificate signing request was written to the volume. | A new key and CSR are waiting for a signed certificate (ADR 0070). | Nothing. |
| `tls.volume.unreadable` | `warning` | `error` | The certificate volume cannot be read: {error}. | The pending request or the published chain could not be read. | Check that the volume is mounted with the right owner. |
| `tls.volume.unwritable` | `warning` | `error` | The certificate volume cannot be written: {error}. | A certificate change was refused because the volume is read-only or full. | Check that the volume is mounted writable with the right owner. |
| `tls.volume.withdraw_failed` | `warning` | `error` | The certificate could not be withdrawn from the proxy: {error}. | Removing the chain from the certificate volume failed. | Check the volume's permissions. |
| `tls.volume.withdrawn` | `info` | – | The certificate was withdrawn from the proxy. | The proxy falls back to its default certificate. | Nothing. |
| `tls.watch_failed` | `warning` | `error` | The certificate check did not run: {error}. | The worker could not run the scheduled certificate check. | Look at the container log around it; repeated failures point at the host or the database. |
| `updates.check_failed` | `warning` | `error` | The release check did not run: {error}. | The worker could not look up the latest release. | Check the outbound network to GitHub. |
| `updates.checked` | `info` | `current` | Release check done; running {current}. | The worker looked up the latest release of its channel (ADR 0059). | Nothing. |
| `updates.release_ignored` | `warning` | `reason` | A release in the feed was ignored: {reason}. | The release feed held an entry that is not a version or points elsewhere. | Nothing. |
| `updates.version_note_failed` | `warning` | `error` | The running version could not be noted: {error}. | At start-up the version change was not written to the audit log. | Look at the container log around it; repeated failures point at the host or the database. |
| `webex.refresh_token_expiring` | `warning` | – | The Webex refresh token expires soon. | The service app's refresh token is close to its end of life. | Authorise the Webex service app again under *Integrations*. |
| `webex.token_refresh_failed` | `warning` | `error`, `status` | Refreshing the Webex token failed ({status}): {error}. | Webex could not be reached or answered an error for a token refresh; it is retried. | Check the outbound network; see also the security event `webex.token_refresh`. |
| `webex.token_refresh_refused` | `error` | `status` | Webex refused the token refresh ({status}). | The refresh token is no longer valid; calls to Webex fail until it is replaced. | Authorise the Webex service app again under *Integrations*. |
| `webhook.webex` | `info` | `kind`, `meeting_id`, `outcome` | Webex reported {kind} for meeting {meeting_id}: {outcome}. | A verified webhook of Webex Meetings was processed. | Nothing. |
| `webhook.wxcc` | `info` | `kind`, `outcome` | Webex Contact Center reported {kind}: {outcome}. | A verified webhook of Webex Contact Center was processed. | Nothing. |
| `widget.invalid_request` | `info` | `fields` | The widget sent an invalid request (fields {fields}). | A booking or availability request did not validate; only the field names are logged. | Nothing. |
| `worker.heartbeat_failed` | `warning` | `error` | The worker heartbeat could not be written: {error}. | *System status* may show the worker as stale. | Look at the container log around it; repeated failures point at the host or the database. |
| `worker.run_failed` | `warning` | `error` | The worker loop failed this round: {error}. | Running the due jobs crashed; the loop goes on. | Look at the container log around it; repeated failures point at the host or the database. |
| `worker.started` | `info` | `dispatch_enabled`, `fallback_mail`, `poll_seconds`, `release_check`, `video_enabled` | Worker started; polling every {poll_seconds} s. | The worker process started, with the switches it runs with. | Nothing. |
| `worker.stopped` | `info` | – | Worker stopped. | The worker process ended cleanly. | Nothing. |

### Generated

Three kinds of action are generated, not listed:

- **a permission** (`group.edit`, `staff.manage`, ...): an admin used, or was denied, that
  right; level `outcome`, category `audit`. `docs/authorization.md` says who holds each one.
- **a machine API scope** (`availability:read`, `configuration:transfer`): a client of the machine API used, or was denied, that
  scope (ADR 0023).
- **a worker job**, `job.<type>`: the outcome of one job, shown on the booking's timeline;
  the types are `create_video_room`, `delete_video_room`, `delete_trial_call`, `send_confirmation`, `send_change`, `send_reminder`, `send_cancellation`, `dispatch_wxcc_task`, `end_wxcc_task`, `purge_pii`, `send_suspension`, `send_closure_update`, `send_reinstatement`, `send_rebook_request`, `send_rebook_reminder`, `send_rebook_lapsed`, `lapse_rebook`, `send_confirm_request`, `send_confirm_reminder`, `expire_unconfirmed`, `send_unconfirmed_lapsed`, `send_team_invite`, `send_team_cancel`.

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
