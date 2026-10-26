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

## Outgoing email (WP3)

```
worker ──SMTP──> mail provider (per organisation in WP7, ADR 0008) ──> citizen
   reads booking at send time: first name, email, date, time, team, management link
```

Jobs carry booking IDs only. Messages and calendar attachments contain no phone number and
no full name. In development Mailpit (`--profile dev`) catches all mail locally.

## Stored personal data (WP2 schema)

| Data item | Table.column | Source | Retention | Leaves the system |
|---|---|---|---|---|
| Name | `bookings.name` | booking form | until slot end + 24 h, or cancellation + 24 h (`purge_pii` job planned at booking) | first name only, to WxCC (WP6) |
| Email | `bookings.email` | booking form | as above | mail provider (WP4) |
| Phone (optional per group) | `bookings.phone` | booking form | as above | SMS route (WP6) |
| Channel consents | `bookings.consents` | booking form (WhatsApp, roadmap) | as above | no |
| Video join URLs | `bookings.guest_join_url`, `host_join_url` | video provider (WP5) | as above | guest URL to the citizen, host URL to WxCC |
| Management token | `bookings.management_token_hash` (SHA-256 only) | generated | kept (not personal data once the booking is purged) | token itself only in the confirmation mail |

Jobs, slots, schedules and audit events carry no personal data of citizens.

**Exception: emergency closure (ADR 0021).** A booking suspended by an emergency closure keeps
the data above until it is moved, reinstated or cancelled (then the rules above apply again),
and at most until 30 days after the closure ends (`purge_pii` planned at the closure's end +
30 days; none while the closure is open-ended). Decided by the product owner on 2026-10-25; to
be confirmed in the DPIA (O7). The closure notice is admin text without personal data
(`closure_notices`); it goes to the citizen by mail and on the public group page.

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
citizen ──GET /b/{token}/join/go──> OpenPlanner ──303──> Webex web app (no data added to the link)
Webex, WxCC ──signed webhooks──> OpenPlanner: meeting/participant/task events → booking_events, call_status
```

| Data item | Where | Source | Retention | Leaves the system |
|---|---|---|---|---|
| Meeting join link | `bookings.guest_join_url`, `host_join_url` | Webex | purged with the contact (PII field) | in the citizen's link (via OpenPlanner) and to WxCC (`meetingLink`) |
| First name | derived from `bookings.name` at dispatch | booking form | as the contact | to WxCC (`firstName`, `origin.name`) |
| Meeting and task ids, timeline, call status | `bookings.video_room_id`, `wxcc_task_id`, `booking_events`, `call_status` | Webex, WxCC | kept for statistics (no personal data) | no |
| Participant names/emails in webhook payloads | processed in memory only | Webex | not stored, not logged | no |
| Service App tokens | `integration_credentials` (AES-256-GCM) | Webex | rotated automatically | sent only to Webex |

## Branding and the calendar script (WP3b)

```
superuser ──POST name, colour, logo──> web ──SQL──> organisations, organisation_logos
visitor ──GET /o/{slug}/theme.css, /o/{slug}/logo──> web (organisation data, no PII)
visitor browser ──fetch same page (?day=)──> web   (calendar.js, same origin, no new data)
```

Branding is organisation data, not personal data. The calendar script sends nothing the page
link does not already send, and stores nothing in the browser.

## Planned flows

To be added per work package: SMS reminders (WP4/WP6), integration settings in the admin UI
(WP7d), purge sweep and backups (WP8). Each flow lists: data items, source, destination, legal
basis owner, retention.
