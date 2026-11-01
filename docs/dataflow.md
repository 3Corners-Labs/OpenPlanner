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
worker ──SMTP──> mail provider of the organisation (admin portal; else SMTP_HOST) ──> participant
   reads booking at send time: first name, email, date, time, team, management link
   request to confirm a new booking and its reminder (ADR 0056): email, date, time, team
   and the confirm link only; no name, join link or calendar file (the address is not
   verified yet); the lapse message links the group page instead of the booking
   closure mails also: the closure's notice and heading, its copy of the contact options
   (organisation data) and the link to the closure page (ADR 0037 §5); never the image
```

Jobs carry booking IDs only. Messages and calendar attachments contain no phone number and
no full name. In development Mailpit (`--profile dev`) catches all mail locally.

## SMS reminders (ADR 0022, 0028)

```
worker ──HTTPS──> SMS API of the organisation's provider ──> mobile network ──> participant
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
are sealed with AES-256-GCM (ADR 0008). Group texts are text blocks without personal data. Organisation texts (ADR 0074, table `organisation_texts`) are the same kind of plain text, shown to every participant of the organisation's groups on pages and in messages; they never hold personal data and leave the system only inside those messages.
Neither leaves the system except as part of the messages above.

```
web ──DNS (TXT)──> the host's resolver          sender domain check (ADR 0073)
   the domain of the sender address, _dmarc.<domain> and <selector>._domainkey.<domain>;
   organisation data only, on the superuser's request; the answers are judged, not stored
```

The platform's fallback mail server, the update check's read token and the Webex Service App
(client secret, refresh token) can also be set in the portal (ADR 0042 step 6). Their password and token are sealed the same way, under
`setting:<key>` in `integration_credentials`, and are never shown, logged or audited by value;
they leave the system only to the mail server and to GitHub respectively.

## Archived groups (ADR 0084)

```
org admin ──POST /admin/groups/{slug}/settings/archive (name typed back)──> web
web ──SQL──> groups.archived_at = now; audit group.delete, detail archived (slug, actor)
participant ──GET /g/{slug}──> web ──> 404 (same page as an unknown slug)
worker (daily) ──SQL──> groups archived ≥ group_erase_after_days ago with no booking, slot,
                         closure, acceptance or API client ──> system event group.erased ──>
                         DELETE groups (cascade: staffing, exceptions, reminders, texts,
                         form fields, holidays, settings)
```

Archiving moves no personal data: the bookings of the group keep their own `purge_pii` jobs
and leave on their own schedule, and the row is erased only after the last of them is gone.
The audit log keeps the slug as text; nothing of a participant is in it.

**Deleting an organisation and an API client (ADR 0084).** The superuser can delete an
organisation that has no group (archived ones included) and no API client, and an API client
that was switched off. Neither holds personal data of participants: an organisation row holds
its name, language and identity-provider value, and its branding, channels (secrets sealed),
texts and settings go with it by the schema's cascades; a client row holds its name, networks
and the SHA-256 of its secret, and its access tokens and group links go with it. The audit
log keeps the organisation's slug and the client's id as text (`organisation.delete`,
`client.delete`) and nothing else; no booking can be affected, because bookings hang under
groups, and a group blocks the delete until the erase sweep above has removed it.

## Stored personal data (WP2 schema)

The fields below are the fixed list of `app/domain/privacy.py` (ADR 0063): personal data is
shown in the admin portal only on the booking detail page and the queue item (first name only
on the group's agenda and queue), never on overview, list, export or alert pages, and no list
filter matches on it. The list and the rule are constants, not settings.

| Data item | Table.column | Source | Retention | Leaves the system |
|---|---|---|---|---|
| First and last name | `bookings.first_name`, `bookings.last_name` (one `name` before ADR 0055) | booking form | until slot end + 24 h, or cancellation + 24 h (`purge_pii` job planned at booking) | first name only, to WxCC (WP6) |
| Email | `bookings.email` | booking form | as above | mail provider (WP4) |
| Phone (optional per group) | `bookings.phone` (E.164 since ADR 0055) | booking form | as above | the organisation's SMS provider, for SMS reminders (ADR 0022, 0028) |
| Answers to the group's custom fields | `bookings.form_answers` (JSON) | booking form, the group's own questions (ADR 0055) | as above: purged as a whole, whatever each field's tag | no. Shown on the booking page of the agenda (`bookings.view`); the queue, logs, audit and messages get only answers tagged *not personal data* |
| Channel consents | `bookings.consents` | booking form (WhatsApp, roadmap) | as above | no |
| Video join URLs | `bookings.guest_join_url`, `host_join_url` | video provider (WP5) | as above | guest URL to the participant, host URL to WxCC |
| Management token | `bookings.management_token_hash` (SHA-256 only) | generated | kept (not personal data once the booking is purged) | token itself only in the confirmation mail |

Jobs, slots, schedules and audit events carry no personal data of participants. A failed meeting
job keeps Webex's own reason (`message`, ADR 0065): only from the meeting request, which carries
the group name and the host's address (masked), never a participant's data; the system event of the
automatic group check (ADR 0065 step 2) carries the same masked reason of the probe meeting,
which belongs to no booking. The probe work item of a group check (ADR 0082) leaves for WxCC
with a fixed first name ("Probe"), a reference that is no booking's and the probe meeting's
link: no participant data; its `task:new` and `task:ended` are kept as event kinds in
`integration_reports` (row `wxcc_probe`), replaced by the next check. A request event (`integration.request`, ADR 0068) records each
call to Webex and WxCC made for a booking as method, path without its query string, status,
duration, the platform's tracking id and the version: never a body, a query or a header, so
neither the host's address (a query parameter of `DELETE /meetings`) nor a participant's first name
(the work item body) reaches it. Request events are kept `INTEGRATION_REQUEST_RETENTION_DAYS`
(default 30). The definitions of
custom fields (`group_form_fields`: keys, labels, options) are the organisation's questions,
not answers, and hold no personal data; the admin screen refuses questions about a BSN, an
identity document or special categories of personal data (ADR 0055).

**Exception: emergency closure (ADR 0024).** A booking suspended by an emergency closure keeps
the data above until it is moved, reinstated or cancelled (then the rules above apply again),
and at most until 30 days after the closure ends (`purge_pii` planned at the closure's end +
30 days; none while the closure is open-ended). Decided by the product owner on 2026-10-25; to
be confirmed in the DPIA (O7). The closure notice is admin text without personal data
(`closure_notices`); it goes to the participant by mail and on the public group page.

**Alternative contact options (ADR 0037).** Phone numbers, email addresses, links and addresses
of the organisation (`organisation_contacts`), entered by an organisation admin, or of one team
(`group_contacts`), entered by its group admin, and shown publicly on closure and maintenance
pages, on the page of a booking on hold (CL-2) and in the suspension and update mails (CL-5). A closure keeps a copy of the block that applied when it started (`closures.contacts`)
and an optional heading per locale (`closure_notices.heading`). Organisation data, not personal data of participants;
the admin page asks for general numbers and addresses only. No purge job; kept until changed.

**Embedded booking (ADR 0035).** With `<gov-booking>` on a host website, the participant's
browser calls `/api/v1/widget/{org}/{group}/` directly: first and last name, email, (optional) phone
and the answers to the group's custom fields go
from the browser to OpenPlanner over TLS, never through the host's servers. The host site
does see that a participant opened its page with the widget, and which group the tag names; it
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
participants were told. Organisation data; no personal data of participants; no purge job.

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
superuser ──form──> web ──guarded GET──> organisation provider /.well-known/openid-configuration
web ──SQL──> identity_providers (registration; client secret sealed), identity_provider_keys
             (public JWK; private key sealed; ADR 0083 step 2)
anyone ──GET /admin/jwks.json──> web (public keys of every organisation provider)
```

| Data item | Table.column / place | Source | Retention | Leaves the system |
|---|---|---|---|---|
| Admin subject identifier | `audit_events.actor`, `staff.subject`, session cookie | identity provider | admin actions: `AUDIT_RETENTION_DAYS` (default 2 years, product owner 2026-10-29); system 90 days, security 1 year, debug 7 days (ADR 0040) | no |
| Admin display name | `staff.display_name`, session cookie | identity provider | replaced at every login; to be erased for admins inactive longer than `staff_retention_days` (ADR 0046, step 2) | no |
| Admin roles and memberships | `staff.roles`, `staff_memberships` (role key, organisation and group slugs: one row per grant, ADR 0049) | identity provider claims | replaced at every login; changes audited as `staff.membership_changed` | no |
| Entitlement values (ADR 0049) | session cookie (the recognised values), `staff_refused_entitlements` (value, reason code, organisation), `audit_events` (`auth.entitlement_refused`, `auth.entitlement_mismatch`, `auth.ambiguous_claims`) | identity provider claim `openplanner_entitlements` | session: its lifetime; refusals: replaced at every login; events: as admin actions | no; the values hold the prefix, a role key and the IdP values of organisations and groups, linked to the employee's subject |
| Organisation provider registration (ADR 0083 step 2) | `identity_providers` (key, name, discovery URL, issuer, client id, method, scopes, claim and prefix, on/off; client secret sealed AES-256-GCM), `identity_provider_keys` (public JWK; private key sealed) | the superuser's form; the provider's discovery document | until the superuser removes the provider; a retired key stays published 24 h | public keys at `/admin/jwks.json`; the client assertion to the provider's token endpoint; no personal data in any of it |
| IdP values of organisations and groups | `organisations.idp_key`, `groups.idp_key` | superuser and org admins in the portal | as the organisation or group; changes audited with old and new value | no (they are what PingFederate sends) |
| Admin activity times | `staff.first_seen_at`, `last_login_at`, `last_seen_at` (at most once per `STAFF_LAST_SEEN_INTERVAL_SECONDS`), `last_logout_at` | web | as the record | no |
| System events (job outcomes) | `audit_events` with `source = 'system'`: job type, outcome, error code, attempt, channel, booking id | worker | `SYSTEM_EVENT_RETENTION_DAYS` (default 90), pruned by the worker | no |
| Audit events on the log platform (ADR 0034) | one JSON line per audit, system and security event on stdout, shipped by the host to the organisation's log platform (SIEM) | web, worker | the organisation's log policy | no participant data; **admin subjects** (personal data of employees): the log platform is a recipient |
| Caller pseudonym in security events (ADR 0053) | `audit_events.detail` as `client:<16 hex>`, and on the log platform | web (from the request's IP address; the address itself is never stored) | as the security event (`SECURITY_EVENT_RETENTION_DAYS`; the organisation's log policy on the platform) | pseudonymised personal data of the caller: HMAC-SHA256 under a key derived from `MANAGEMENT_TOKEN_KEY`, IPv6 by /64; only reversible by trying a known address on the host (`python -m app.cli client-pseudonym`) |
| Audit log export (ADR 0040) | CSV download of the audit log in the admin's scope: ids, codes, admin subjects | web → admin's browser | the organisation's policy; every export is an audit event | no participant data; admin subjects |
| Diagnostic mode (ADR 0040) | `diagnostic_modes`: organisation and group slug, end time, superuser's subject; debug events in `audit_events` (category `diagnostic`: job type, attempt, duration) | web (superuser), worker | mode: until it ends; debug events `DIAGNOSTIC_EVENT_RETENTION_DAYS` (7) | no |
| Event schema fields (ADR 0040) | `audit_events.event_id`, `level`, `category`, `trace_id`, `service`; the same trace id on stdout | web, worker | as the event they belong to | no: random ids and codes |

System events hold no personal data: no names, addresses, message text or join links, only codes
and the booking id. Each admin sees them at their own level only (group admin: their groups;
organisation admin and auditor: their organisation and its groups; superuser: everything).
The auditor role (ADR 0039) is read-only and holds no permission that shows participants' data; it
does see admin names in the audit log, which is the accountability it exists for.

Custom roles (ADR 0039 step 3) live in `roles`: template, names, the role claim value and the
permission codes, no personal data. The session cookie keeps, next to the built-in roles, at
most 20 other role claim values (group or role names from the IdP, no personal data); the
custom roles they grant are looked up per request. Changes are audited as `role.*` events
with codes only and shown on the matrix page with the actor's display name.

The retention overview (`/admin/retention`, ADR 0039) reads `bookings` only to count, per group,
how many still hold personal data, how many are past their purge moment and how many purges
failed, and when the last purge ran. It selects no personal data column and no booking id, so it
is no new processing of participants' data; it is the control that the purge (CLAUDE.md privacy
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
participant ──GET /b/{token}/join/go──> OpenPlanner ──303──> Webex web app (no data added to the link)
participant ──POST /b/{token}/test──> OpenPlanner ──meeting (group name, times)──> Webex ──id, link──> OpenPlanner (ADR 0071)
participant ──GET /b/{token}/test/go──> OpenPlanner ──303──> the trial meeting in the Webex web app
Webex, WxCC ──signed webhooks──> OpenPlanner: meeting/participant/task events → booking_events, call_status
web ──HTTPS GET /meetings/{id}, /meetingParticipants?meetingId=──> Webex (ADR 0010, when an admin opens or polls the agenda and a status is older than MEETING_STATUS_REFRESH_SECONDS): meeting id and the group's host address out; state and participant list back, reduced in the adapter to state, count, host flag, first join and last leave → call_status. Participant names, addresses and IPs are processed in memory only, never stored or logged
```

| Data item | Where | Source | Retention | Leaves the system |
|---|---|---|---|---|
| Meeting join link | `bookings.guest_join_url`, `host_join_url` | Webex | purged with the contact (PII field) | in the participant's link (via OpenPlanner) and to WxCC (`meetingLink`) |
| Trial call link | `bookings.trial_join_url` (with `trial_room_id`, `trial_expires_at`, `trial_count`) | Webex, when the participant opens a trial call (ADR 0071) | link: purged with the contact (PII field); the meeting is deleted at Webex minutes after it closes | to the participant only (via OpenPlanner's redirect); no WxCC task |
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
participant ──GET /g/{slug}──> web (holidays are closed days: no times, no holiday data shown)
```

Holiday calendars are configuration, not personal data; nothing leaves the system. Changes are
audited with keys only (`<calendar>:holiday.add`, `calendars`, `holiday.open`).

## Updates from the admin portal (ADR 0029)

```
worker ──GET releases?per_page=30; the channel is applied in the domain (ADR 0059, 0067;
        no data sent; optional read-only token)──> api.github.com
        <── tag, release page, publication time, notes ── stored in release_checks and
            release_listing (notes as PR titles and numbers only; authors stripped, ADR 0067)
superuser ──POST update / restore / cancel──> web ──SQL──> update_requests (versions, subject id)
superuser's browser ──GET /admin/updates/progress (every 5 s while open; ADR 0048)──> web
        <── steps and status of the request (no personal data) ──
host updater ──docker exec python -m app.cli (claim, progress, report)──> web ──SQL──>
             update_requests (status, step), audit_events
             ──backup-db──> data/backups/request-<id>.db (copy of the whole database: PII)
```

Nothing about participants leaves the system: the release lookup sends no data. The database
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
participant ──GET/POST /g/{slug}…, /b/{token}/cancel|reschedule──> web ──503 maintenance page (no write)
bot ──non-GET /api/agent/v1/… (not the token)──> web ──503 {"error": "maintenance"} (no write)
```

The switch holds a subject identifier or a host actor, times and a reason code: no personal
data. A refused change is audited with its permission and target only, never the form content.

## HTTPS certificate (ADR 0030, ADR 0064, ADR 0070)

```
proxy ──ACME (https)──> Let's Encrypt: TLS_DOMAIN, ACME_EMAIL (operator), public key
Let's Encrypt ──HTTP-01 / TLS-ALPN-01──> proxy (ports 80/443): challenge token only
visitor ──https──> proxy ──http, edge network──> web (X-Forwarded-For set by the proxy)
worker ──TLS handshake, no request──> proxy (public name, port 443): reads the public certificate
       ──> certificate_checks (issuer, names, validity, finding; no personal data; ADR 0064)
```

```
web ──> portal-certs volume (uid 10001, mode 600): private key + CSR made here (ADR 0070)
superuser ──https, admin session──> web: the CSR (public) out; the certificate chain in
   (a migration: chain + private key in, the key written to the volume only)
web ──> own_certificates: chain (public), public key, fingerprint, subject, issuer, names,
        validity; audit_events: fingerprint + expiry only
web ──portal-certs volume──> proxy: privkey.pem, fullchain.pem, serial
```

**The database holds certificates but no private keys** (DPIA input): the key of an own
certificate is made on the host or imported straight into the `portal-certs` volume, which the
database backup leaves out on purpose; it is never in a log, an audit event, an error message or
a page. It concerns the organisation's server identity, not a participant. The CA learns the domain name and the operator's e-mail address; no participant data
leaves through this flow. The proxy keeps no access log, so booking paths with management tokens are not
written anywhere. Certificates are logged publicly in Certificate Transparency logs (domain
name only).

## Planned flows

Subjects and routing attributes (ADR 0081, not built): the participant's chosen subject and the
switched-on attributes (preferred language, channel preference) are stored on the booking as codes
from a closed list and leave the system only as routing variables in the contact-center task,
next to the first name and the host join URL; purged with the booking.

To be added per work package: purge sweep and backups (WP8). Each flow lists: data items, source, destination, legal
basis owner, retention.
