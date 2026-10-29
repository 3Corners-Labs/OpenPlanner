# Settings

Generated from `app/domain/settings_registry.py` by
`python -m app.cli settings-doc > docs/settings.md`; do not edit by hand. CI fails when this
file differs from the code. The classes and their rules are in
[ADR 0042](adr/0042-settings-layers-env-lock-and-advanced-settings.md).

- **A and B** are read from the environment only; their values never appear in the portal.
- **C and D** will be set in the portal (build step 3 onwards). An environment variable
  *locks* them: it wins, and the portal shows the setting read-only. A locked value outside
  the portal bounds is accepted but flagged by `python -m app.cli settings list`.
- Secrets are write-only: the portal and the CLI only say whether they are set.

On the host, `python -m app.cli settings list` shows every setting with its source;
`python -m app.cli settings show KEY` shows one.

## A. Bootstrap (environment only)

| Variable | Kind | Default | Note |
|---|---|---|---|
| `ENVIRONMENT` | enum | `prod` | Selects the production checks. |
| `DATABASE_URL` | string | `sqlite+aiosqlite:///data/booking.db` | The database the portal settings live in. |
| `SETTINGS_ENCRYPTION_KEY` | secret | — | Seals every secret in the database (ADR 0008). |
| `MANAGEMENT_TOKEN_KEY` | secret | — | Derives management tokens (ADR 0011); required in production. |
| `SESSION_SECRET` | secret | — | Signs admin sessions; required in production. |
| `OIDC_DISCOVERY_URL` | url | — | IdP discovery document; without it the admin portal is disabled. |
| `OIDC_CLIENT_ID` | string | `openplanner-admin` | OIDC client of the portal. |
| `OIDC_CLIENT_SECRET` | secret | — | OIDC client secret. |
| `OIDC_SCOPES` | string | `openid profile email` | Scopes requested at login; wrong scopes break login. |

## B. Deployment (environment only)

| Variable | Kind | Default | Note |
|---|---|---|---|
| `PUBLIC_BASE_URL` | url | `http://127.0.0.1:8000` | Base of every link and of the OIDC redirect; https in production. |
| `WEBHOOK_BASE_URL` | url | — | Where Webex and WxCC deliver webhooks, if not PUBLIC_BASE_URL. |
| `FORWARDED_ALLOW_IPS` | string | — | Address of the reverse proxy (read by uvicorn). |
| `TLS_DOMAIN` | string | — | Certificate domain (proxy.env, ADR 0030). |
| `ACME_EMAIL` | string | — | Mailbox for CA notices (proxy.env). |
| `ACME_CA` | url | — | ACME directory (proxy.env). |
| `WORKER_HEARTBEAT_PATH` | path | `data/worker.heartbeat` | Heartbeat file the worker healthcheck reads. |
| `WORKER_HEARTBEAT_MAX_AGE_SECONDS` | float | `120.0` | Age after which the worker healthcheck fails. |
| `APP_VERSION` | string | `""` | Baked into release images (ADR 0029). |
| `APP_COMMIT` | string | `""` | Git commit baked into every image (ADR 0051). |
| `INSTALLATION_NAME` | string | `""` | Name of this installation in every log line and event record (ADR 0040/0034). |
| `WEBEX_API_BASE_URL` | url | `https://webexapis.com/v1` | Webex API endpoint; changed only for test doubles. |
| `SIMULATOR_ENABLED` | bool | `false` | Platform-event simulator on admin booking pages; refused in production. |
| `AGENT_API_ENABLED` | bool | `false` | Machine API for contact-center bots; off means the routes do not exist (ADR 0023). |
| `RATE_LIMIT_ENABLED` | bool | `true` | Off switch for every rate limit; never in the portal. |
| `MAINTENANCE_MODE` | bool | `false` | Maintenance mode from the host, also while the database is unavailable (ADR 0038); the portal cannot switch it off. |
| `DEV_TOKEN_KEY_PATH` | path | `data/dev-token.key` | Development management-token key file. |
| `DEV_SESSION_KEY_PATH` | path | `data/dev-session.key` | Development session key file. |
| `DEV_SETTINGS_KEY_PATH` | path | `data/dev-settings.key` | Development settings key file. |

## C. Platform (portal, *Advanced settings*; lockable by env)

| Variable | Kind | Default | Portal bounds | Permission | Note |
|---|---|---|---|---|---|
| `LOG_LEVEL` | enum | `INFO` | DEBUG, INFO, WARNING, ERROR, CRITICAL | `settings.platform.manage` | Log level; set in the portal it expires after at most 24 hours. |
| `WORKER_POLL_SECONDS` | float | `30.0` | 5–300 | `settings.platform.manage` | Interval of the worker loop. |
| `MAINTENANCE_CACHE_SECONDS` | float | `5.0` | 0–5 | `settings.platform.manage` | How long the web process trusts its last read of the maintenance switch (ADR 0038). |
| `MAINTENANCE_RETRY_AFTER_SECONDS` | int | `300` | 30–3600 | `settings.platform.manage` | Retry-After of the 503 that citizens and the machine API get during maintenance (ADR 0038). |
| `STAFF_LAST_SEEN_INTERVAL_SECONDS` | int | `300` | 60–3600 | `settings.platform.manage` | An admin's last-seen time is written at most once per this interval (ADR 0046). |
| `SYSTEM_EVENT_RETENTION_DAYS` | int | `90` | 30–3650 | `settings.platform.manage` | Days after which system events leave the audit log (ADR 0031). |
| `AUDIT_RETENTION_DAYS` | int | `730` | 365–3650 | `settings.platform.manage` | Days after which admin actions leave the audit log (ADR 0040). |
| `SECURITY_EVENT_RETENTION_DAYS` | int | `365` | 30–3650 | `settings.platform.manage` | Days after which security events leave the audit log (ADR 0040). |
| `DIAGNOSTIC_EVENT_RETENTION_DAYS` | int | `7` | 1–90 | `settings.platform.manage` | Days after which debug events of diagnostic mode are deleted (ADR 0040). |
| `DIAGNOSTIC_MODE_HOURS` | int | `24` | 1–72 | `settings.platform.manage` | Hours after which diagnostic mode ends by itself (ADR 0040). |
| `SECURITY_EVENT_WINDOW_MINUTES` | int | `15` | 1–1440 | `settings.platform.manage` | One security event per source and reason per window; repeats are counted (ADR 0034). |
| `AUDIT_EXPORT_MAX_ROWS` | int | `100000` | 1000–1000000 | `settings.platform.manage` | Most rows one CSV export of the audit log may hold; more is refused (ADR 0040). |
| `AUDIT_EXPORT_MAX_DAYS` | int | `366` | 1–3660 | `settings.platform.manage` | Most days one CSV export of the audit log may cover (ADR 0040). |
| `STORAGE_LOW_FREE_PERCENT` | float | `15.0` | 1–50 | `settings.platform.manage` | Free space below which storage is low (ADR 0040). |
| `STORAGE_CRITICAL_FREE_PERCENT` | float | `5.0` | 1–50 | `settings.platform.manage` | Free space below which storage is critical; below the low limit. |
| `DATABASE_MAX_MB` | int | — | empty or ≥ 100 | `settings.platform.manage` | Database size above which storage is degraded; empty means no limit. |
| `RATE_LIMIT_BOOKINGS` | rate | `10/hour` | 1–30 per hour | `settings.platform.manage` | Bookings per IP address. |
| `RATE_LIMIT_READS` | rate | `60/minute` | 10–300 per minute | `settings.platform.manage` | Availability reads per IP address. |
| `RATE_LIMIT_AGENT_TOKENS` | rate | `10/minute` | 1–30 per minute | `settings.platform.manage` | Machine API token requests per client. |
| `RATE_LIMIT_AGENT_CALLS` | rate | `120/minute` | 10–600 per minute | `settings.platform.manage` | Machine API calls per client. |
| `ADMIN_SESSION_HOURS` | int | `8` | 1–8 | `settings.platform.manage` | Lifetime of an admin session. |
| `ADMIN_REAUTH_MINUTES` | int | `60` | 5–60 | `settings.platform.manage` | Maximum login age for a change (ADR 0015). |
| `OIDC_ROLE_CLAIM` | string | `roles` | — | `settings.login.manage` | Claim holding the roles. |
| `OIDC_ORGANISATION_CLAIM` | string | `organisations` | — | `settings.login.manage` | Claim holding the organisations. |
| `OIDC_GROUP_CLAIM` | string | `groups` | — | `settings.login.manage` | Claim holding the groups. |
| `OIDC_ROLE_SUPERUSER` | string | `openplanner-superuser` | — | `settings.login.manage` | Role value of a superuser. |
| `OIDC_ROLE_ORG_ADMIN` | string | `openplanner-org-admin` | — | `settings.login.manage` | Role value of an organisation admin. |
| `OIDC_ROLE_GROUP_ADMIN` | string | `openplanner-group-admin` | — | `settings.login.manage` | Role value of a group admin. |
| `OIDC_ROLE_AUDITOR` | string | `openplanner-auditor` | — | `settings.login.manage` | Role value of an auditor (ADR 0039). |
| `SMTP_HOST` | string | — | — | `settings.platform.manage` | Fallback mail server (ADR 0022); empty means no fallback mail. |
| `SMTP_PORT` | int | `25` | 1–65535 | `settings.platform.manage` | Port of the fallback mail server. |
| `SMTP_STARTTLS` | bool | `false` | — | `settings.platform.manage` | Upgrade the connection with STARTTLS. |
| `SMTP_TLS` | bool | `false` | — | `settings.platform.manage` | Implicit TLS (also on port 465). |
| `SMTP_USERNAME` | string | — | — | `settings.platform.manage` | Login; needs STARTTLS or TLS. Also accepted: `SMTP_USER`. |
| `SMTP_PASSWORD` | secret | — | — | `settings.platform.manage` | Password of the login; write-only. Also accepted: `SMTP_USER_PASSWORD`. |
| `MAIL_FROM` | string | `OpenPlanner <noreply@openplanner.invalid>` | — | `settings.platform.manage` | Fallback sender. |
| `WEBEX_CLIENT_ID` | string | — | — | `settings.platform.manage` | Service App (ADR 0017/0018); moves to the organisation with ADR 0032. |
| `WEBEX_CLIENT_SECRET` | secret | — | — | `settings.platform.manage` | Service App secret; write-only. |
| `WEBEX_REFRESH_TOKEN` | secret | — | — | `settings.platform.manage` | Bootstraps the token store; write-only. |
| `WEBEX_WEBHOOK_SECRET` | secret | — | — | `settings.platform.manage` | Webex webhook secret; to be generated and rotated by the server. |
| `WXCC_API_BASE_URL` | url | — | https only | `settings.platform.manage` | WxCC API host of the region. |
| `WXCC_WEBHOOK_SECRET` | secret | — | — | `settings.platform.manage` | WxCC webhook secret; to be generated and rotated by the server. |
| `UPDATE_REPOSITORY` | string | — | — | `settings.platform.manage` | owner/name of the release repository (ADR 0029). |
| `UPDATE_CHECK_TOKEN` | secret | — | — | `settings.platform.manage` | Read token for a private repository; write-only. |

## D. Organisation (portal, organisation settings; lockable by env)

| Variable | Kind | Default | Portal bounds | Permission | Note |
|---|---|---|---|---|---|
| `TIME_FORMAT` | enum | `24h` | 24h, 12h, locale | `settings.organisation.manage` | How clock times are written. |
| `ADMIN_TIMEZONE` | string | `Europe/Amsterdam` | — | `settings.organisation.manage` | IANA time zone of the admin portal. |
| `STAFF_DORMANT_AFTER_DAYS` | int | `90` | 14–365 | `settings.organisation.manage` | Days without a login after which a person is marked dormant in the staff list (ADR 0046). |
