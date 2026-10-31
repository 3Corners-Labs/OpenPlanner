# Settings

Generated from `app/domain/settings_registry.py` by
`python -m app.cli settings-doc > docs/settings.md`; do not edit by hand. CI fails when this
file differs from the code. The classes and their rules are in
[ADR 0042](adr/0042-settings-layers-env-lock-and-advanced-settings.md).

- **A and B** are read from the environment only; their values never appear in the portal.
  A bootstrap secret `X` may instead come from the file named by `X_FILE` (§9);
  `python -m app.cli init` generates the three keys into `secrets/secrets.env`.
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
| `TLS_CERT_FILE` | path | — | Own certificate chain (PEM) for the proxy; with TLS_KEY_FILE it replaces ACME (proxy.env, ADR 0064). |
| `TLS_KEY_FILE` | path | — | Private key of the own certificate (proxy.env, ADR 0064). |
| `TLS_PROXY_BUNDLED` | bool | `false` | The bundled HTTPS proxy runs, so the portal manages the certificate source (docker-compose.yml sets it from OPENPLANNER_TLS; ADR 0072). |
| `TLS_PORTAL_CERTIFICATE_DIR` | path | `portal-certs` | The volume shared with the proxy that holds the own certificate's private key, its request and the served pair; never under data/ (ADR 0072). |
| `TLS_PORTAL_POLL_SECONDS` | int | `10` | How often the proxy looks for a changed certificate from the portal (proxy.env, ADR 0072). |
| `WORKER_HEARTBEAT_PATH` | path | `data/worker.heartbeat` | Heartbeat file the worker healthcheck reads. |
| `WORKER_HEARTBEAT_MAX_AGE_SECONDS` | float | `120.0` | Age after which the worker healthcheck fails. |
| `APP_VERSION` | string | `""` | Baked into release images (ADR 0029). |
| `APP_COMMIT` | string | `""` | Git commit baked into every image (ADR 0052). |
| `INSTALLATION_NAME` | string | `""` | Name of this installation in every log line and event record (ADR 0040/0034). |
| `WEBEX_API_BASE_URL` | url | `https://webexapis.com/v1` | Webex API endpoint; changed only for test doubles. |
| `SIMULATOR_ENABLED` | bool | `false` | Platform-event simulator on admin booking pages; refused in production. |
| `AGENT_API_ENABLED` | bool | `false` | Machine API for contact-center bots; off means the routes do not exist (ADR 0023). |
| `RATE_LIMIT_ENABLED` | bool | `true` | Off switch for every rate limit; never in the portal. |
| `MAINTENANCE_MODE` | bool | `false` | Maintenance mode from the host, also while the database is unavailable (ADR 0038); the portal cannot switch it off. |
| `DEV_TOKEN_KEY_PATH` | path | `data/dev-token.key` | Development management-token key file. |
| `DEV_SESSION_KEY_PATH` | path | `data/dev-session.key` | Development session key file. |
| `DEV_SETTINGS_KEY_PATH` | path | `data/dev-settings.key` | Development settings key file. |
| `OIDC_CLAIM_MODE` | enum | `legacy` | Where rights come from: legacy (three claims), shadow (compare), entitlements (ADR 0049); moving back to legacy weakens a protection, so env only. |

## C. Platform (portal, *Advanced settings*; lockable by env)

| Variable | Kind | Default | Portal bounds | Permission | Note |
|---|---|---|---|---|---|
| `LOG_LEVEL` | enum | `INFO` | DEBUG, INFO, WARNING, ERROR, CRITICAL | `settings.platform.manage` | Log level; set in the portal it expires after at most 24 hours. |
| `WORKER_POLL_SECONDS` | float | `30.0` | 5–300 | `settings.platform.manage` | Interval of the worker loop. |
| `MAINTENANCE_CACHE_SECONDS` | float | `5.0` | 0–5 | `settings.platform.manage` | How long the web process trusts its last read of the maintenance switch (ADR 0038). |
| `MAINTENANCE_RETRY_AFTER_SECONDS` | int | `300` | 30–3600 | `settings.platform.manage` | Retry-After of the 503 that citizens and the machine API get during maintenance (ADR 0038). |
| `MAINTENANCE_ALERT_HOURS` | int | `4` | 1–72 | `settings.platform.manage` | Maintenance on longer than this is reported as a system event (ADR 0038). |
| `MAINTENANCE_ALERT_REPEAT_HOURS` | int | `24` | 1–168 | `settings.platform.manage` | The overdue maintenance event repeats at this interval while it lasts (ADR 0038). |
| `TLS_CHECK_INTERVAL_HOURS` | int | `6` | 1–168 | `settings.platform.manage` | How often the worker probes the certificate of the public address (ADR 0064). |
| `TLS_EXPIRY_WARNING_DAYS` | int | `20` | 1–60 | `settings.platform.manage` | Fewer days left than this makes the certificate finding 'expiring' (ADR 0064). |
| `TLS_ALERT_REPEAT_HOURS` | int | `24` | 1–168 | `settings.platform.manage` | A lasting certificate finding is reported again at this interval (ADR 0064). |
| `TLS_CERTIFICATE_SOURCE` | enum | `acme` | acme, own | `settings.platform.manage` | Where the bundled proxy's certificate comes from: Let's Encrypt (acme) or the pair uploaded in the portal (own); 'own' needs an upload (ADR 0072). |
| `INTEGRATION_CHECK_INTERVAL_HOURS` | int | `24` | 1–168 | `settings.platform.manage` | The worker checks every group with a Webex host this often and reports a broken chain as a system event (ADR 0065). |
| `JOB_OVERDUE_MINUTES` | int | `10` | 1–1440 | `settings.platform.manage` | A pending job this many minutes past its planned time counts as overdue in a group's integration check (ADR 0068). |
| `STAFF_LAST_SEEN_INTERVAL_SECONDS` | int | `300` | 60–3600 | `settings.platform.manage` | An admin's last-seen time is written at most once per this interval (ADR 0046). |
| `SYSTEM_EVENT_RETENTION_DAYS` | int | `90` | 30–3650 | `settings.platform.manage` | Days after which system events leave the audit log (ADR 0031). |
| `INTEGRATION_REQUEST_RETENTION_DAYS` | int | `30` | 1–365 | `settings.platform.manage` | Days after which request events (every call to Webex and WxCC for a booking) leave the audit log, apart from the other system events (ADR 0068). |
| `AUDIT_RETENTION_DAYS` | int | `730` | 365–3650 | `settings.platform.manage` | Days after which admin actions leave the audit log (ADR 0040). |
| `SECURITY_EVENT_RETENTION_DAYS` | int | `365` | 30–3650 | `settings.platform.manage` | Days after which security events leave the audit log (ADR 0040). |
| `DIAGNOSTIC_EVENT_RETENTION_DAYS` | int | `7` | 1–90 | `settings.platform.manage` | Days after which debug events of diagnostic mode are deleted (ADR 0040). |
| `DIAGNOSTIC_MODE_HOURS` | int | `24` | 1–72 | `settings.platform.manage` | Hours after which diagnostic mode ends by itself (ADR 0040). |
| `SECURITY_EVENT_WINDOW_MINUTES` | int | `15` | 1–1440 | `settings.platform.manage` | One security event per source and reason per window; repeats are counted (ADR 0034). |
| `SECURITY_EVENT_MAX_SOURCES` | int | `1000` | 10–100000 | `settings.platform.manage` | Windows (kind of event × caller) a process keeps; beyond that, callers count as many (ADR 0053). |
| `AUDIT_EXPORT_MAX_ROWS` | int | `100000` | 1000–1000000 | `settings.platform.manage` | Most rows one CSV export of the audit log may hold; more is refused (ADR 0040). |
| `AUDIT_EXPORT_MAX_DAYS` | int | `366` | 1–3660 | `settings.platform.manage` | Most days one CSV export of the audit log may cover (ADR 0040). |
| `EMBEDDING_MAX_ORIGINS` | int | `20` | 1–100 | `settings.platform.manage` | Most host websites one organisation may allow for the embedded widget (ADR 0035). |
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
| `OIDC_ROLE_AGENT` | string | `openplanner-agent` | — | `settings.login.manage` | Role value of an agent who works a group's own queue (ADR 0043). |
| `OIDC_ENTITLEMENT_CLAIM` | string | `openplanner_entitlements` | — | `settings.login.manage` | Claim holding the entitlements, one value per role and target (ADR 0049). |
| `OIDC_ENTITLEMENT_PREFIX` | string | `openplanner` | — | `settings.login.manage` | Prefix of every entitlement value; separates installations that share one IdP. |
| `OIDC_ENTITLEMENTS_MAX_VALUES` | int | `200` | 20–1000 | `settings.login.manage` | Values in the entitlement claim above which the login is refused (ADR 0049). |
| `IDP_MAPPING_REFUSED_WINDOW_DAYS` | int | `30` | 7–365 | `settings.platform.manage` | How far back the identity provider mapping page lists refused values (ADR 0049). |
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
| `UPDATE_CHANNEL` | enum | `release` | release, rc | `settings.platform.manage` | What the installation follows (ADR 0059, 0065): releases only, or release candidates (`rc`) too; `rc` is not usable in production. |
| `UPDATE_AUTO_APPLY` | bool | `false` | — | `settings.platform.manage` | The worker requests a newer version itself as soon as the check finds one (ADR 0059, 0065); not usable in production. |
| `UPDATE_CHECK_MINUTES` | int | `60` | 5–1440 | `settings.platform.manage` | Minutes between two release checks by the worker (ADR 0067); "Check now" on the updates page asks for one ahead of time. |
| `PLANNER_CHANGES_ALERT_PER_HOUR` | int | `50` | 5–1000 | `settings.platform.manage` | Changes by one planner per hour above which a system event warns (ADR 0044). |
| `QUEUE_REFRESH_SECONDS` | int | `10` | 5–60 | `settings.platform.manage` | Seconds between two refreshes of the own queue, a claimed booking and the citizen's waiting page (ADR 0072). |
| `MEETING_STATUS_REFRESH_SECONDS` | int | `30` | 10–300 | `settings.platform.manage` | Seconds a meeting status on the agenda may be old before the video provider is asked (webhooks come first), and between two refreshes of the agenda's status columns (ADR 0010). |
| `BOOKING_FORM_MAX_CUSTOM_FIELDS` | int | `5` | 0–15 | `settings.platform.manage` | Custom fields a group's booking form may have at most; 0 switches them off (ADR 0055). |
| `BOOKING_CONFIRM_MIN_MINUTES` | int | `15` | 5–120 | `settings.platform.manage` | Less time to confirm than this: confirm until the change deadline, no reminder. |

## D. Organisation (portal, organisation settings; lockable by env)

| Variable | Kind | Default | Portal bounds | Permission | Note |
|---|---|---|---|---|---|
| `TIME_FORMAT` | enum | `24h` | 24h, 12h, locale | `settings.organisation.manage` | How clock times are written. |
| `ADMIN_TIMEZONE` | string | `Europe/Amsterdam` | — | `settings.organisation.manage` | IANA time zone of the admin portal. |
| `PRODUCT_FOOTER` | bool | `true` | — | `settings.organisation.manage` | "Appointments via OpenPlanner" with the product mark at the foot of the booking pages and in the HTML part of emails (ADR 0060 §6). |
| `STAFF_DORMANT_AFTER_DAYS` | int | `90` | 14–365 | `settings.organisation.manage` | Days without a login after which a person is marked dormant in the staff list (ADR 0046). |
| `SHORTFALL_HORIZON_DAYS` | int | `14` | 1–60 | `settings.organisation.manage` | Days ahead the shortfall notice on group pages and the dashboard looks (ADR 0044). |
| `PLANNER_REBOOK_DAYS` | int | `14` | 1–30 | `settings.organisation.manage` | Days after the original start after which an unanswered request to rebook lapses. |
| `PLANNER_REBOOK_REMINDER_DAYS` | int | `3` | 1–29 | `settings.organisation.manage` | Days before the lapse at which the one reminder to rebook goes (below the lapse). |
| `PHONE_DEFAULT_REGION` | enum | `NL` | AC, AD, AE, AF, AG, AI, AL, AM, AO, AR, AS, AT, AU, AW, AX, AZ, BA, BB, BD, BE, BF, BG, BH, BI, BJ, BL, BM, BN, BO, BQ, BR, BS, BT, BW, BY, BZ, CA, CC, CD, CF, CG, CH, CI, CK, CL, CM, CN, CO, CR, CU, CV, CW, CX, CY, CZ, DE, DJ, DK, DM, DO, DZ, EC, EE, EG, EH, ER, ES, ET, FI, FJ, FK, FM, FO, FR, GA, GB, GD, GE, GF, GG, GH, GI, GL, GM, GN, GP, GQ, GR, GT, GU, GW, GY, HK, HN, HR, HT, HU, ID, IE, IL, IM, IN, IO, IQ, IR, IS, IT, JE, JM, JO, JP, KE, KG, KH, KI, KM, KN, KP, KR, KW, KY, KZ, LA, LB, LC, LI, LK, LR, LS, LT, LU, LV, LY, MA, MC, MD, ME, MF, MG, MH, MK, ML, MM, MN, MO, MP, MQ, MR, MS, MT, MU, MV, MW, MX, MY, MZ, NA, NC, NE, NF, NG, NI, NL, NO, NP, NR, NU, NZ, OM, PA, PE, PF, PG, PH, PK, PL, PM, PR, PS, PT, PW, PY, QA, RE, RO, RS, RU, RW, SA, SB, SC, SD, SE, SG, SH, SI, SJ, SK, SL, SM, SN, SO, SR, SS, ST, SV, SX, SY, SZ, TA, TC, TD, TG, TH, TJ, TK, TL, TM, TN, TO, TR, TT, TV, TW, TZ, UA, UG, US, UY, UZ, VA, VC, VE, VG, VI, VN, VU, WF, WS, XK, YE, YT, ZA, ZM, ZW | `settings.organisation.manage` | Country of phone numbers typed without a country code (ADR 0055). |
| `BOOKING_CONFIRM_WINDOW_HOURS` | int | `24` | 1–72 | `settings.organisation.manage` | Hours after booking within which the citizen confirms by email link (ADR 0056). |
| `BOOKING_CONFIRM_CUTOFF_HOURS` | int | `2` | 0–24 | `settings.organisation.manage` | Hours before the start by which the citizen confirms at the latest (ADR 0056). |
| `BOOKING_CONFIRM_REMINDERS` | int | `1` | 0–3 | `settings.organisation.manage` | Reminders to confirm, spread evenly over the time to confirm (ADR 0056). |
