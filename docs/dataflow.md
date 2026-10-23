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

## Stored personal data (WP2 schema)

| Data item | Table.column | Source | Retention | Leaves the system |
|---|---|---|---|---|
| Name | `bookings.name` | booking form | until slot end + 24 h, or cancellation + 24 h (`purge_pii` job planned at booking) | first name only, to WxCC (WP6) |
| Email | `bookings.email` | booking form | as above | mail provider (WP4) |
| Phone (optional per group) | `bookings.phone` | booking form | as above | SMS route (WP6) |
| Channel consents | `bookings.consents` | booking form (WhatsApp, roadmap) | as above | no |
| Video join URLs | `bookings.guest_join_url`, `host_join_url` | video provider (WP5) | as above | guest URL to the citizen, host URL to WxCC |
| Management token | `bookings.management_token_hash` (SHA-256 only) | generated | kept (not personal data once the booking is purged) | token itself only in the confirmation mail |

Jobs, slots, schedules and audit data carry no personal data.

## Planned flows

To be added per work package: booking (WP3), mail (WP4), Webex video (WP5), Webex Connect task
and SMS (WP6), admin/IdP (WP7), purge and backups (WP8). Each flow lists: data items, source,
destination, legal basis owner, retention.
