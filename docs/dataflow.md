# Data flow

Input for the DPIA (HANDOVER §7). Status: work package 1 — no personal data is processed yet.

## Current state

```
operator ──HTTP──> web (/healthz, /readyz) ──SQL──> SQLite volume (no tables with data)
                   worker ──SQL──> SQLite volume (heartbeat only)
web, worker ──stdout──> container log (JSON, PII fields masked)
```

## Planned flows

To be added per work package: booking (WP3), mail (WP4), Webex video (WP5), Webex Connect task
and SMS (WP6), admin/IdP (WP7), purge and backups (WP8). Each flow lists: data items, source,
destination, legal basis owner, retention.
