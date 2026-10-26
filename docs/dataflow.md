# Data flow

Input for the DPIA (HANDOVER §7). Status: work package 2. The schema can hold personal data;
no entry point collects it yet (booking endpoints arrive in WP3).

## Current state

```
operator ──HTTP──> web (/healthz, /readyz) ──SQL──> SQLite volume (no tables with data)
                   worker ──SQL──> SQLite volume (heartbeat only)
web, worker ──stdout──> container log (JSON, PII fields masked)
visitor ──HTTP──> web (GET /, ?lang=) ──> HTML page in the negotiated language
web ──Set-Cookie: lang=<locale>──> visitor browser (functional preference, 1 year, no PII)
```

The `lang` cookie stores only a locale code after an explicit choice in the language switcher.
It identifies nobody and is strictly functional, so no consent banner is needed.

## Outgoing email (WP3, ADR 0022)

```
worker ──SMTP──> mail provider of the organisation (admin portal; else SMTP_HOST) ──> citizen
   reads booking at send time: first name, email, date, time, team, management link
```

Jobs carry booking IDs only. Messages and calendar attachments contain no phone number and
no full name. In development Mailpit (`--profile dev`) catches all mail locally.

## SMS reminders (ADR 0022)

```
worker ──HTTPS──> Webex Connect SMS API of the organisation ──> mobile network ──> citizen
   body: phone number (E.164), text with first name, date, time, team, management link
```

| Data item | Destination | When | Legal basis owner | Retention at the processor |
|---|---|---|---|---|
| Phone number | Webex Connect (Cisco), mobile operators | per SMS reminder rule of the group, only for bookings with a phone number | organisation (controller) | per Cisco processor agreement; to be fixed contractually (HANDOVER §7, B6) |
| First name, date, time, team, management link | as above (message text) | as above | as above | as above |

Only groups whose booking form asks for a phone number can have SMS reminders. The text is
built from the allow-listed placeholders only (ADR 0009); group admins cannot add other data.
OpenPlanner logs a random correlation ID per SMS, never the number or the text.

## Channel settings and message texts (ADR 0022)

SMTP and SMS settings per organisation are organisation data; their password and service key
are sealed with AES-256-GCM (ADR 0008). Group texts are text blocks without personal data.
Neither leaves the system except as part of the messages above.

## Stored personal data (WP2 schema)

| Data item | Table.column | Source | Retention | Leaves the system |
|---|---|---|---|---|
| Name | `bookings.name` | booking form | until slot end + 24 h, or cancellation + 24 h (`purge_pii` job planned at booking) | first name only, to WxCC (WP6) |
| Email | `bookings.email` | booking form | as above | mail provider (WP4) |
| Phone (optional per group) | `bookings.phone` | booking form | as above | Webex Connect, for SMS reminders (ADR 0022) |
| Channel consents | `bookings.consents` | booking form (WhatsApp, roadmap) | as above | no |
| Video join URLs | `bookings.guest_join_url`, `host_join_url` | video provider (WP5) | as above | guest URL to the citizen, host URL to WxCC |
| Management token | `bookings.management_token_hash` (SHA-256 only) | generated | kept (not personal data once the booking is purged) | token itself only in the confirmation mail |

Jobs, slots, schedules and audit events carry no personal data of citizens.

## Admin login and audit (WP7a)

```
admin ──browser──> web /admin/login ──redirect──> identity provider (PingFederate; Keycloak in dev)
identity provider ──ID token (sub, name, roles, organisations, groups)──> web /admin/callback
web ──signed session cookie op_admin (sub, name, roles, scope; 8 h)──> admin browser
web ──SQL──> audit_events (sub, action, outcome, organisation, group)
web ──SQL──> admin_profiles (sub, display name, last login)
```

| Data item | Table.column / place | Source | Retention | Leaves the system |
|---|---|---|---|---|
| Admin subject identifier | `audit_events.actor`, `admin_profiles.subject`, session cookie | identity provider | audit events: kept as the audit trail (retention to be set with the hosting owner, O7) | no |
| Admin display name | `admin_profiles.display_name`, session cookie | identity provider | replaced at every login; to be removed for admins inactive longer than the audit retention (WP8) | no |

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
citizen ──GET /b/{token}/join/go──> OpenPlanner ──303──> Webex web app (no data added to the link)
Webex, WxCC ──signed webhooks──> OpenPlanner: meeting/participant/task events → booking_events, call_status
```

| Data item | Where | Source | Retention | Leaves the system |
|---|---|---|---|---|
| Meeting join link | `bookings.guest_join_url`, `host_join_url` | Webex | purged with the contact (PII field) | in the citizen's link (via OpenPlanner) and to WxCC (`meetingLink`) |
| First name | derived from `bookings.name` at dispatch | booking form | as the contact | to WxCC (`firstName`, `origin.name`) |
| Meeting and task ids, timeline, call status | `bookings.video_room_id`, `wxcc_task_id`, `booking_events`, `call_status` | Webex, WxCC | kept for statistics (no personal data) | no |
| Participant names/emails in webhook payloads | processed in memory only; webhooks filtered on the groups' host accounts, so other meetings of the organisation are not delivered | Webex | not stored, not logged | no |
| Webex event keys (SHA-256 of resource, event, event id, join/leave time) | `webhook_events` | OpenPlanner | 30 days, removed on a later event | no |
| Service App tokens | `integration_credentials` (AES-256-GCM) | Webex | rotated automatically | sent only to Webex |

## Branding and the calendar script (WP3b)

```
superuser ──POST name, colour, logo──> web ──SQL──> organisations, organisation_logos
visitor ──GET /o/{slug}/theme.css, /o/{slug}/logo──> web (organisation data, no PII)
visitor browser ──fetch same page (?day=)──> web   (calendar.js, same origin, no new data)
```

Branding is organisation data, not personal data. The calendar script sends nothing the page
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

## Planned flows

To be added per work package: purge sweep and backups (WP8). Each flow lists: data items, source, destination, legal
basis owner, retention.
