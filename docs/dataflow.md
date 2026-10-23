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

## Planned flows

Live meeting status (WP5/WP7, ADR 0010): Webex → service through signed webhook events and
Meetings API polling. Only aggregates are stored (state, participant count, host joined,
start/end times); participant names, emails and IP addresses are processed in memory only.

To be added per work package: booking (WP3), mail (WP4), Webex video (WP5), Webex Connect task
and SMS (WP6), admin settings (WP7b-d), purge and backups (WP8). Each flow lists: data items, source,
destination, legal basis owner, retention.
