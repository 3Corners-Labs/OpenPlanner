# Data flow

Input for the DPIA (HANDOVER §7). Status: work package 2. The schema can hold personal data;
no entry point collects it yet (booking endpoints arrive in WP3).

## Current state

```
operator ──HTTP──> web (/healthz, /readyz) ──SQL──> SQLite volume (no tables with data)
                   worker ──SQL──> SQLite volume (heartbeat only)
web, worker ──stdout──> container log (JSON, PII fields masked; trace id, organisation and
                         group slugs, service and installation name per line, ADR 0040)
visitor ──HTTP──> web (GET /, ?lang=) ──> HTML page in the negotiated language
web ──Set-Cookie: lang=<locale>──> visitor browser (functional preference, 1 year, no PII)
```

The `lang` cookie stores only a locale code after an explicit choice in the language switcher.
It identifies nobody and is strictly functional, so no consent banner is needed.

## Outgoing email (WP3, ADR 0022)

```
worker ──SMTP──> mail provider of the organisation (admin portal; else SMTP_HOST) ──> citizen
   reads booking at send time: first name, email, date, time, team, management link
   request to confirm a new booking and its reminder (ADR 0056): email, date, time, team
   and the confirm link only; no name, join link or calendar file (the address is not
   verified yet); the lapse message links the group page instead of the booking
   a planner's proposal of a new time (ADR 0057): first name, the old and the proposed
   time, team, the fixed reason, the confirm and "choose another time" links, a calendar
   file that removes the old time; SMS with consent: first name, new time, reason, link
   closure mails also: the closure's notice and heading, its copy of the contact options
   (organisation data) and the link to the closure page (ADR 0037 §5); never the image
```

Jobs carry booking IDs only. Messages and calendar attachments contain no phone number and
no full name. In development Mailpit (`--profile dev`) catches all mail locally.

## SMS reminders (ADR 0022, 0028)

```
worker ──HTTPS──> SMS API of the organisation's provider ──> mobile network ──> citizen
   body: phone number (E.164), text with first name, date, time, team, management link
```

| Data item | Destination | When | Legal basis owner | Retention at the processor |
|---|---|---|---|---|
| Phone number | the organisation's SMS provider (today only Webex Connect, Cisco), mobile operators | per SMS reminder rule of the group, and on a planner's move, cancellation or request to rebook (ADR 0044), only for bookings with a phone number | organisation (controller) | per Cisco processor agreement; to be fixed contractually (HANDOVER §7, B6) |
| First name, date, time, team, management link | as above (message text) | as above | as above | as above |

A planner's move, cancellation or request to choose a new time, its one reminder and its lapse (ADR 0044) are sent by SMS as well, to bookings with a phone
number, with the same data items; a cancellation adds the planner's reason, a fixed text of the
organisation (never typed by the planner), and has no management link.

Only groups whose booking form asks for a phone number can have SMS reminders. The text is
built from the allow-listed placeholders only (ADR 0009); group admins cannot add other data.
OpenPlanner logs a random correlation ID per SMS, never the number or the text. Adding an SMS
provider (ADR 0028) adds a processor: add its row to the table above in the same PR.

## Channel settings and message texts (ADR 0022)

SMTP and SMS settings per organisation are organisation data; their password and SMS API key
are sealed with AES-256-GCM (ADR 0008). Group texts are text blocks without personal data.
Neither leaves the system except as part of the messages above.

The platform's fallback mail server, the update check's read token and the Webex Service App
(client secret, refresh token) can also be set in the portal (ADR 0042 step 6). Their password and token are sealed the same way, under
`setting:<key>` in `integration_credentials`, and are never shown, logged or audited by value;
they leave the system only to the mail server and to GitHub respectively.

## Stored personal data (WP2 schema)

| Data item | Table.column | Source | Retention | Leaves the system |
|---|---|---|---|---|
| First and last name | `bookings.first_name`, `bookings.last_name` (one `name` before ADR 0055) | booking form | until slot end + 24 h, or cancellation + 24 h (`purge_pii` job planned at booking) | first name only, to WxCC (WP6) |
| Email | `bookings.email` | booking form | as above | mail provider (WP4) |
| Phone (optional per group) | `bookings.phone` (E.164 since ADR 0055) | booking form | as above | the organisation's SMS provider, for SMS reminders (ADR 0022, 0028) |
| Answers to the group's custom fields | `bookings.form_answers` (JSON) | booking form, the group's own questions (ADR 0055) | as above: purged as a whole, whatever each field's tag | no. Shown on the booking page of the agenda (`bookings.view`); the queue, logs, audit and messages get only answers tagged *not personal data* |
| Channel consents | `bookings.consents` | booking form (WhatsApp, roadmap) | as above | no |
| Video join URLs | `bookings.guest_join_url`, `host_join_url` | video provider (WP5) | as above | guest URL to the citizen, host URL to WxCC |
| Management token | `bookings.management_token_hash` (SHA-256 only) | generated | kept (not personal data once the booking is purged) | token itself only in the confirmation mail |

Jobs, slots, schedules and audit events carry no personal data of citizens. The definitions of
custom fields (`group_form_fields`: keys, labels, options) are the organisation's questions,
not answers, and hold no personal data; the admin screen refuses questions about a BSN, an
identity document or special categories of personal data (ADR 0055).

**Exception: emergency closure (ADR 0024).** A booking suspended by an emergency closure keeps
the data above until it is moved, reinstated or cancelled (then the rules above apply again),
and at most until 30 days after the closure ends (`purge_pii` planned at the closure's end +
30 days; none while the closure is open-ended). Decided by the product owner on 2026-10-25; to
be confirmed in the DPIA (O7). The closure notice is admin text without personal data
(`closure_notices`); it goes to the citizen by mail and on the public group page.

**Alternative contact options (ADR 0037).** Phone numbers, email addresses, links and addresses
of the organisation (`organisation_contacts`), entered by an organisation admin, or of one team
(`group_contacts`), entered by its group admin, and shown publicly on closure and maintenance
pages, on the page of a booking on hold (CL-2) and in the suspension and update mails (CL-5). A closure keeps a copy of the block that applied when it started (`closures.contacts`)
and an optional heading per locale (`closure_notices.heading`). Organisation data, not personal data of citizens;
the admin page asks for general numbers and addresses only. No purge job; kept until changed.

**Embedded booking (ADR 0035).** With `<gov-booking>` on a host website, the citizen's
browser calls `/api/v1/widget/{org}/{group}/` directly: first and last name, email, (optional) phone
and the answers to the group's custom fields go
from the browser to OpenPlanner over TLS, never through the host's servers. The host site
does see that a citizen opened its page with the widget, and which group the tag names; it
cannot read what is typed into the shadow root's form through our API, and the answer to a
booking holds no personal data and no management token (the link goes by email only). The
same personal data rules apply as for the standalone page.

**Closure image (ADR 0037 §3, CL-3).** One image per closure (`closure_images`), uploaded by an
admin with a text alternative in nl and en. Metadata a photo can carry about people (EXIF and
XMP, such as a GPS position or the photographer's name; PNG text chunks; JPEG comments) is
stripped before the file is stored, so only the picture itself is kept. Served from our own
origin and only while its closure is visible; never mailed, never sent to a third party.
Organisation data; no purge job.

**Closure scenarios (ADR 0037 §4, CL-4).** Prepared closure content per organisation or team
(`closure_scenarios`): an admin-facing name, notices and headings, optionally a contact block
and an image (`closure_images`, stripped as above). Admin-only until a closure starts from it;
then the closure gets its own copy, so deleting or editing the scenario never changes what
citizens were told. Organisation data; no personal data of citizens; no purge job.

**Allowed origins for embedding (ADR 0035).** The websites on which an organisation's groups
may appear as a widget (`organisation_origins`, such as `https://www.gemeente.nl`), entered by
an organisation admin, and a switch per group (`groups.embedding_enabled`). Organisation data,
not personal data; no purge job; kept until changed. The audit event holds counts only.

## Admin login and audit (WP7a)

```
admin ──browser──> web /admin/login ──redirect──> identity provider (PingFederate; Keycloak in dev)
identity provider ──ID token (sub, name, roles, organisations, groups)──> web /admin/callback
web ──signed session cookie op_admin (sub, name, roles, scope; 8 h)──> admin browser
web ──SQL──> audit_events (sub, action, outcome, organisation, group)
worker ──SQL──> audit_events (source=system: job type, outcome, error code, booking id; ADR 0031)
web ──SQL──> staff, staff_memberships (sub, display name, roles, organisation and group slugs,
             first/last login, last seen, last logout; ADR 0046)
```

| Data item | Table.column / place | Source | Retention | Leaves the system |
|---|---|---|---|---|
| Admin subject identifier | `audit_events.actor`, `staff.subject`, session cookie | identity provider | admin actions: `AUDIT_RETENTION_DAYS` (default 2 years, product owner 2026-10-29); system 90 days, security 1 year, debug 7 days (ADR 0040) | no |
| Admin display name | `staff.display_name`, session cookie | identity provider | replaced at every login; to be erased for admins inactive longer than `staff_retention_days` (ADR 0046, step 2) | no |
| Admin roles and memberships | `staff.roles`, `staff_memberships` (organisation and group slugs) | identity provider claims | replaced at every login; changes audited as `staff.membership_changed` | no |
| Admin activity times | `staff.first_seen_at`, `last_login_at`, `last_seen_at` (at most once per `STAFF_LAST_SEEN_INTERVAL_SECONDS`), `last_logout_at` | web | as the record | no |
| System events (job outcomes) | `audit_events` with `source = 'system'`: job type, outcome, error code, attempt, channel, booking id | worker | `SYSTEM_EVENT_RETENTION_DAYS` (default 90), pruned by the worker | no |
| Audit events on the log platform (ADR 0034) | one JSON line per audit, system and security event on stdout, shipped by the host to the organisation's log platform (SIEM) | web, worker | the organisation's log policy | no citizen data; **admin subjects** (personal data of employees): the log platform is a recipient |
| Caller pseudonym in security events (ADR 0053) | `audit_events.detail` as `client:<16 hex>`, and on the log platform | web (from the request's IP address; the address itself is never stored) | as the security event (`SECURITY_EVENT_RETENTION_DAYS`; the organisation's log policy on the platform) | pseudonymised personal data of the caller: HMAC-SHA256 under a key derived from `MANAGEMENT_TOKEN_KEY`, IPv6 by /64; only reversible by trying a known address on the host (`python -m app.cli client-pseudonym`) |
| Audit log export (ADR 0040) | CSV download of the audit log in the admin's scope: ids, codes, admin subjects | web → admin's browser | the organisation's policy; every export is an audit event | no citizen data; admin subjects |
| Diagnostic mode (ADR 0040) | `diagnostic_modes`: organisation and group slug, end time, superuser's subject; debug events in `audit_events` (category `diagnostic`: job type, attempt, duration) | web (superuser), worker | mode: until it ends; debug events `DIAGNOSTIC_EVENT_RETENTION_DAYS` (7) | no |
| Event schema fields (ADR 0040) | `audit_events.event_id`, `level`, `category`, `trace_id`, `service`; the same trace id on stdout | web, worker | as the event they belong to | no: random ids and codes |

System events hold no personal data: no names, addresses, message text or join links, only codes
and the booking id. Each admin sees them at their own level only (group admin: their groups;
organisation admin and auditor: their organisation and its groups; superuser: everything).
The auditor role (ADR 0039) is read-only and holds no permission that shows citizens' data; it
does see admin names in the audit log, which is the accountability it exists for.

Custom roles (ADR 0039 step 3) live in `roles`: template, names, the role claim value and the
permission codes, no personal data. The session cookie keeps, next to the built-in roles, at
most 20 other role claim values (group or role names from the IdP, no personal data); the
custom roles they grant are looked up per request. Changes are audited as `role.*` events
with codes only and shown on the matrix page with the actor's display name.

The retention overview (`/admin/retention`, ADR 0039) reads `bookings` only to count, per group,
how many still hold personal data, how many are past their purge moment and how many purges
failed, and when the last purge ran. It selects no personal data column and no booking id, so it
is no new processing of citizens' data; it is the control that the purge (CLAUDE.md privacy
rules) works.

Admin names are personal data of employees, processed for accountability (who did what in the
portal). They never appear in logs, exceptions or audit events; the audit log joins them in when
it is shown.

## Video meetings and WxCC work items (WP5/WP6)

```
worker/web ──HTTPS (Service App token)──> Webex Meetings API: title "Videogesprek <group>", start, end, time zone, host account
Webex ──meeting id, join link──> OpenPlanner (bookings.video_room_id, guest_join_url)
worker/web ──HTTPS──> WxCC Create Task v2: booking reference, first name, start (text), group name, meeting link, kind
WxCC ──task id──> OpenPlanner (bookings.wxcc_task_id)
worker ──HTTPS──> WxCC Task Messages v2: booking reference, kind=cancelled; then End Task v1 (task id only)
operator CLI <──HTTPS── WxCC configuration (wxcc-config-check): channel, asset, entry point, queue names and ids, schema keys, flow export; nothing stored, no personal data
operator CLI <──HTTPS── Webex webhooks (ownedBy=org), WxCC subscriptions (webhooks-list/-reset): ids, events, hostEmail filters, status, secret set or not, target without query → integration_reports
operator CLI ──DELETE──> Webex webhooks, WxCC subscriptions (webhooks-reset --yes): ids of stale registrations only
operator CLI ──signed test events (unknown ids)──> WEBHOOK_BASE_URL/webhooks/* (webhooks-health) → outcome codes in integration_reports
citizen ──GET /b/{token}/join/go──> OpenPlanner ──303──> Webex web app (no data added to the link)
Webex, WxCC ──signed webhooks──> OpenPlanner: meeting/participant/task events → booking_events, call_status
```

| Data item | Where | Source | Retention | Leaves the system |
|---|---|---|---|---|
| Meeting join link | `bookings.guest_join_url`, `host_join_url` | Webex | purged with the contact (PII field) | in the citizen's link (via OpenPlanner) and to WxCC (`meetingLink`) |
| First name | `bookings.first_name` | booking form | as the contact | to WxCC (`firstName`, `origin.name`) |
| Meeting and task ids, timeline, call status | `bookings.video_room_id`, `wxcc_task_id`, `booking_events`, `call_status` | Webex, WxCC | kept for statistics (no personal data) | no |
| Participant names/emails in webhook payloads | processed in memory only; webhooks filtered on the groups' host accounts, so other meetings of the organisation are not delivered | Webex | not stored, not logged | no |
| Webex event keys (SHA-256 of resource, event, event id, join/leave time) | `webhook_events` | OpenPlanner | 30 days, removed on a later event | no |
| Service App tokens | `integration_credentials` (AES-256-GCM) | Webex | rotated automatically | sent only to Webex |
| Last webhook listing and delivery test (registration ids, events, host account filters, target URLs without query, statuses, outcome codes) | `integration_reports` | Webex, WxCC, the self-test | replaced by the next check | no; shown read-only on **Koppelingen** |

## Own queue (ADR 0043)

A group with dispatch kind `openplanner_queue` sends nothing to a contact center. Its agents
sign in to the admin portal (OIDC, role `agent`) and claim bookings.

```
admin portal (agent of the group) <── web: first name, booking reference, start, lobby state
agent ──claim──> web ──> bookings.claimed_at, claimed_by (HMAC pseudonym of the OIDC subject)
web ──> agent who claimed: the meeting link (only after the claim)
web ──> audit_events: queue.claimed / queue.released (actor subject, group, booking id)
```

| Data | Where | Source | Retention | Leaves OpenPlanner? |
|---|---|---|---|---|
| First name, meeting link | read from `bookings` | booking form, Webex | as the contact | shown to agents of the group only (link only after a claim) |
| Who claimed | `bookings.claimed_by` (pseudonym, not the subject) | the agent's login | removed by `purge_pii` with the contact; `claimed_at` stays for statistics | no |
| Team invite: first name, booking reference, group, start/end, queue link | built in memory by the worker | `bookings`, the group | not stored by OpenPlanner; **in the team's mailbox outside our purge** (DPIA) | by email to `groups.calendar_invite_address` (own queue only), withdrawn with `METHOD:CANCEL` on cancel, move or suspension; never the meeting link, email or phone |

## Branding and the calendar script (WP3b)

```
superuser ──POST name, colours, logo, fonts, theme file──> web ──SQL──> organisations,
                                                     organisation_logos, organisation_fonts
visitor ──GET /o/{slug}/theme.css, /logo, /font/{role}──> web (organisation data, no PII)
visitor browser ──fetch same page (?day=)──> web   (calendar.js, same origin, no new data)
```

Branding is organisation data, not personal data. Fonts come from our own origin only
(`font-src 'self'`, ADR 0041), so no font or stylesheet request reaches a third party. The calendar script sends nothing the page
link does not already send, and stores nothing in the browser.

## Machine API for contact-center bots (ADR 0023)

```
superuser ──POST name, organisation, groups, scopes, networks──> web ──SQL──> api_clients (secret: SHA-256 only)
WxCC AI Agent ──POST /api/agent/v1/oauth/token (client id + secret)──> web ──> api_access_tokens (SHA-256 only)
WxCC AI Agent ──GET groups, availability (bearer token)──> web: group names, free times, places left
```

| Data item | Where | Source | Retention | Leaves the system |
|---|---|---|---|---|
| Client name, organisation, groups, scopes, networks | `api_clients`, `api_client_groups` | superuser | until removed (configuration, no personal data) | no |
| Client secret | SHA-256 in `api_clients`; shown once on screen | OpenPlanner | until rotated | only to the superuser's screen, once |
| Access tokens | SHA-256 in `api_access_tokens` | OpenPlanner | 10 minutes; expired rows removed on the next issue | to the bot |
| Caller IP address | compared in memory, not stored | bot | not stored | no |
| Free times | computed | schedule | not stored | to the bot (no personal data) |

No personal data flows through this API. The next step (change requests, ADR 0023) adds the
phone number from the bot; it will be compared in memory and never stored outside `bookings`.

## Holiday calendars (ADR 0025)

```
superuser ──POST calendar name/slug, holiday date/name, year──> web ──SQL──> holiday_calendars, holidays
organisation admin ──POST ticked calendars──> web ──SQL──> organisation_holiday_calendars
group admin ──POST source, own calendars, kept-open date──> web ──SQL──> groups.holiday_source,
                                                                 group_holiday_calendars, group_holiday_open_days
citizen ──GET /g/{slug}──> web (holidays are closed days: no times, no holiday data shown)
```

Holiday calendars are configuration, not personal data; nothing leaves the system. Changes are
audited with keys only (`<calendar>:holiday.add`, `calendars`, `holiday.open`).

## Updates from the admin portal (ADR 0029)

```
worker ──GET releases/latest, or the releases list on the rc channel (ADR 0059; no data
        sent; optional read-only token)──> api.github.com
        <── tag and release page ── stored in release_checks
superuser ──POST update / restore / cancel──> web ──SQL──> update_requests (versions, subject id)
superuser's browser ──GET /admin/updates/progress (every 5 s while open; ADR 0048)──> web
        <── steps and status of the request (no personal data) ──
host updater ──docker exec python -m app.cli (claim, progress, report)──> web ──SQL──>
             update_requests (status, step), audit_events
             ──backup-db──> data/backups/request-<id>.db (copy of the whole database: PII)
```

Nothing about citizens leaves the system: the release lookup sends no data. The database
backups the updater takes before every change contain the PII of `bookings`; they stay in the
data volume with mode 600 and are deleted after 24 hours (`backup-db --prune` on every updater
run). A backup that is put back is minutes old: purge jobs that ran after it are still pending
in it and run again on the next worker cycle.

## Maintenance mode (ADR 0038)

```
superuser ──POST /admin/maintenance/on|off (reason code, expected end)──> web ──SQL──> maintenance_state
host (CLI, updater) ──docker exec python -m app.cli maintenance──> web ──SQL──> maintenance_state, audit_events
operator ──MAINTENANCE_MODE (environment)──> web (no database read)
admin ──POST (any change) during maintenance──> web ──refused──> audit_events (permission, target slugs)
citizen ──GET/POST /g/{slug}…, /b/{token}/cancel|reschedule──> web ──503 maintenance page (no write)
bot ──non-GET /api/agent/v1/… (not the token)──> web ──503 {"error": "maintenance"} (no write)
```

The switch holds a subject identifier or a host actor, times and a reason code: no personal
data. A refused change is audited with its permission and target only, never the form content.

## HTTPS certificate (ADR 0030)

```
proxy ──ACME (https)──> Let's Encrypt: TLS_DOMAIN, ACME_EMAIL (operator), public key
Let's Encrypt ──HTTP-01 / TLS-ALPN-01──> proxy (ports 80/443): challenge token only
visitor ──https──> proxy ──http, edge network──> web (X-Forwarded-For set by the proxy)
```

The CA learns the domain name and the operator's e-mail address; no citizen data leaves through
this flow. The proxy keeps no access log, so booking paths with management tokens are not
written anywhere. Certificates are logged publicly in Certificate Transparency logs (domain
name only).

## Planned flows

To be added per work package: purge sweep and backups (WP8). Each flow lists: data items, source, destination, legal
basis owner, retention.
