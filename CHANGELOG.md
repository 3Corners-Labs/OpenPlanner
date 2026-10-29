# Changelog

All notable changes are listed here, generated from Conventional Commits on release.

## Unreleased

- Project skeleton: FastAPI app with health endpoints, worker loop with heartbeat, SQLite (WAL)
  via SQLAlchemy async, Alembic baseline, structlog JSON logging with PII masking, Docker image
  and compose file, CI pipeline.
- Docstring (ruff `D`, Google style) and copy-paste (jscpd) gates; layer contracts in
  import-linter.
- Browser accessibility harness (`e2e/`: Playwright, axe-core, Lighthouse), mutation testing,
  GHCR publishing with cosign signing and SBOM attestation.
- ADRs 0004 (i18n module), 0005 (modularity and documentation), 0006 (CI tooling and signing);
  skill map in `docs/skills.md`.
- i18n core (`app/i18n`): locale negotiation (`?lang=` → cookie → browser → default Dutch),
  lookup chain with override hook, safe placeholder formatting, nl/en catalogues.
- Bilingual home page `/` with an accessible language switcher; shared HTML layer `app/web`.
- Domain: groups, weekly schedules with per-date overrides, DST-safe slot calculation,
  booking with capacity snapshots, cancel and reschedule, reminder rules and job planning.
- Persistence: booking schema (migration 0002), SQL unit of work with `BEGIN IMMEDIATE`,
  UTC-safe datetime columns.
- ADRs 0007 (WxCC Tasks API), 0008 (admin-managed secrets), 0009 (channels and templates).
- Bookings are read-only from the WxCC dispatch moment (`dispatch_lead_minutes` per group,
  default 5): cancel and reschedule raise `booking_locked`.
- ADR 0010: live meeting status (state, participants, host joined) for group admins.
- Citizen booking pages: group page with week slot picker, contact form, management page,
  cancel and reschedule; same-origin checks, honeypot and rate limits.
- Confirmation, change, reminder and cancellation emails with calendar attachment, sent by
  the worker with retries; PII purge job executed.
- Authorisation matrix as the single source of rights (ADR 0014): scoped admin queries,
  audit events for logins, logouts and denials, generated `docs/authorization.md`.
- Admin portal with OIDC login (ADR 0013; httpx + PyJWT, PKCE, RP-initiated logout), pages for
  overview, group, my rights, matrix and audit log; menu and actions follow the matrix.
- Audit log shows admins by name (`admin_profiles`, migration 0005) and lets organisation
  admins see the logins of their own admins.
- Development Keycloak with one test user per role; Playwright specs walk the portal as every
  role, with axe on every admin page, also in CI.
- Modern admin layout: side navigation, cards, one fixed look, no JavaScript (ADR 0016).
- Groups and planning in the admin portal: create groups, settings (incl. WxCC queue), weekly
  opening hours with capacity, exceptions per date, a preview of the citizen's slots;
  changes audited with the changed fields (migration 0006).
- Changes need a login of at most 60 minutes (ADR 0015).
- Webex Meetings (ADR 0017): a meeting per booking on behalf of the group's host, staff as
  cohost, guests in the lobby, chat on; deleted on cancellation.
- WxCC work items (ADR 0018): Create Task v2 at T−5 with reference, first name, start, group
  and meeting link; routing value per group for the flow.
- Direct video call (ADR 0019): start a call right away while the team is open; waiting page
  that says honestly when no agent is available.
- Join page for citizens (link never expires; opens 15 minutes before the start).
- Signed webhooks from Webex and WxCC; booking timeline and call status; agenda and
  integrations status in the admin portal; register-webhooks command.
- Service App tokens refreshed and stored AES-256-GCM encrypted (ADR 0008).
- Migrations: SQLite table rebuilds work on databases with data.
- ADR 0011: management tokens derived from a per-booking salt and a server key.
- `seed-demo` command and a Mailpit `dev` profile for demos.
- Citizen pages (WP3b, ADR 0020): month calendar with "first available", times grouped by
  part of the day, steps indicator, confirmation before moving a booking, a modern layout on
  `--booking-*` tokens, and an ARIA grid with arrow keys as progressive enhancement; days
  beyond the booking horizon are marked "nog niet te boeken"; a filter "Wanneer past het u?"
  (weekdays, from/until) finds the first slot that fits.
- Branding per organisation (O5): display name, accent colour (at least 4.5:1 against white)
  and logo (PNG/JPEG/WebP) on every citizen page; Admin → Branding for the superuser
  (`branding.manage`, migration 0011).

- Message channels per organisation (ADR 0022): SMTP server and sender (STARTTLS, TLS or
  none) and a Webex Connect SMS service under Koppelingen → Berichtkanalen, superuser only;
  password and service key encrypted and write-only (migration 0013). `SMTP_HOST` stays the
  fallback.
- SMS reminders through the Webex Connect SMS API; the worker chooses the sender per
  organisation and channel and fails `channel_not_configured` jobs visibly.
- Admin tabs per group: Herinneringen (reminder rules: email or SMS, 5 minutes to 30 days,
  at most five) and Berichtteksten (subject, introduction, closing and SMS text per locale,
  with allow-listed placeholders).
- "Koppeling controleren" per group: a read-only check of the Webex host, the WxCC routing
  chain and the webhooks, with translated findings and an audit entry.
- Simulator on admin booking pages (`SIMULATOR_ENABLED`, off by default, refused in
  production): stand-in meeting and task, and signed platform events played through
  OpenPlanner's own webhook endpoints.
- Environment bar on every admin page outside production ("Ontwikkelomgeving",
  "Testomgeving").

- Alternative contact options per organisation (ADR 0037, CB-1): up to six phone, email,
  website, chat and address options with labels and opening hours in nl/en, validated per
  kind, with a preview; the shared block for closure and maintenance pages.
- Closure content (ADR 0037, CL-1): optional heading per locale on a closure, a team's own
  contact block overriding the organisation default, and a copy of the block that applies taken
  when a closure starts; an update can take over the current block.
- Closure pages for citizens (ADR 0037, CL-2): while a team is closed its page shows the
  closure page (heading, period, notice, contact options) instead of the calendar; the banner of
  an upcoming closure links to that page at `/g/{slug}/closure`; the page of a booking on hold
  shows the contact options above its actions. `seed-closures` adds the examples that e2e checks
  (axe, Lighthouse, keyboard) in nl and en; refused in production.
- Release and versioning policy (ADR 0051): version rules from Conventional Commits,
  `make release-plan`, a release PR plus tag, CI refuses a tag off `main` or out of step with
  `pyproject.toml`; every image carries its commit (`APP_COMMIT`), shown on `/admin/updates`
  and by `python -m app.cli version`.
- Embedding on host websites (ADR 0035, EM-1): allowed origins per organisation under
  Insluiten (`embedding.manage`, organisation admin and superuser), exact `https` origins,
  localhost only outside production; a group's own switch "Insluiten op websites" (off by
  default); audited with counts. ADR 0035 accepted.
