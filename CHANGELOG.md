# Changelog

All notable changes are listed here, generated from Conventional Commits on release.

## Unreleased

### Features

- The release is a workflow (ADR 0079, steps 1 and 2): `release.yml` opens the release PR
  (`release-pr` dispatch; `scripts/release_pr.py` edits `pyproject.toml`, `uv.lock` and
  `CHANGELOG.md` and fills the PR template), runs the audit of a minor or major release
  with `openai/codex-action` and writes it into the PR with `Verdict: open`, and sets the
  annotated tag on the merged release (`tag` dispatch) with the dispatcher as the recorded
  instruction. A `dry_run` input shows the result without opening or tagging anything.
- Version picker (ADR 0067, step 3): the updates page lists every version newer than the
  running one on the channel, with the release notes as plain text (PR titles and numbers),
  and installs the chosen one; going back stays the 24-hour restore. The worker stores the
  listing of each check (`release_listing`, migration 0071); the release adapter lists releases
  and candidates alike and the channel is applied by the domain.
- Update history from the audit log (ADR 0067, step 2): the updates page lists requests,
  outcomes, cancellations and version changes from the global audit log with who did it
  (name, `system`, updater or host), and links to the audit page filtered on the subject
  *Updates*; the audit page gains a *subject* filter. The web process records the running
  version at start-up (`running_versions`, migration 0070) and audits a change nobody
  requested in the portal as `system.version` by actor `host`; the page says so.
- Version management in the portal (ADR 0067, step 1): the update channel and auto-apply
  are class C settings the superuser changes in the portal (topic Updates), read live by web
  and worker; `rc` and auto-apply are not usable in production. The installer's `--channel`
  flag is gone; which images the updater trusts follows the installer mode (test and lab
  also trust `main`), and `--version X.Y.Z-rc.N` pins a candidate outside production.
- Release candidates (ADR 0059, step 1): every merge to `main` becomes `vX.Y.Z-rc.N`, a
  signed image and a GitHub pre-release with SBOM, published after the ZAP baseline without
  waiting for the mutation test; `make release-plan` ignores candidates, and
  `python -m scripts.release_plan --candidate` prints the next one. A hand-pushed `rc` tag
  fails the tag check.
- Update channel per installation (ADR 0059, step 2): `UPDATE_CHANNEL=rc` offers release
  candidates `X.Y.Z-rc.N` next to releases (test and lab hosts; refused in production), and
  `UPDATE_AUTO_APPLY=true` lets the worker request a newer version itself (actor `system`,
  audited). `scripts/install.sh --channel rc` writes the channel and lets the updater trust
  images signed on `main`; the updates page shows the channel.

## 0.3.0 (2026-10-29)

### Features

- Closure content (ADR 0037, CL-1): optional heading per locale on a closure, a team's own
  contact block overriding the organisation default, and a copy of the block that applies taken
  when a closure starts; an update can take over the current block.
- Closure pages for citizens (ADR 0037, CL-2): while a team is closed its page shows the
  closure page (heading, period, notice, contact options) instead of the calendar; the banner of
  an upcoming closure links to that page at `/g/{slug}/closure`; the page of a booking on hold
  shows the contact options above its actions. `seed-closures` adds the examples that e2e checks
  (axe, Lighthouse, keyboard) in nl and en; refused in production.
- Release and versioning policy (ADR 0052): version rules from Conventional Commits,
  `make release-plan`, a release PR plus tag, CI refuses a tag off `main` or out of step with
  `pyproject.toml`; every image carries its commit (`APP_COMMIT`), shown on `/admin/updates`
  and by `python -m app.cli version`.
- Closure image (ADR 0037, CL-3): a running or planned closure can carry one PNG, JPEG or WebP
  image (at most 500 kB, type from the magic bytes, metadata such as EXIF and XMP stripped),
  with a text alternative in nl and en or marked decorative; shown on the closure page and
  served from our origin only while the closure is visible. Magic-byte recognition and the
  multipart upload reader are shared with the logo (`app/domain/images.py`,
  `app/admin/uploads.py`). Migration 0040. Manual topic for the closure screen.
- Closure scenarios (ADR 0037, CL-4): organisation scenarios (`organisation.closure`) and a
  team's own (`closure.manage`) with name, notices, headings, an optional contact block and an
  image that only admins see. "Close the team" and "close the organisation" start from a
  scenario (or empty) and copy it; later edits never change a running closure. Audited as
  `closure.start,scenario:N,bookings:N`. Migration 0047. Manual topic with examples to copy.
- Closure mails (ADR 0037, CL-5): the suspension and update mails carry the closure's heading,
  notice and copy of the contact options as a fixed block in the HTML and plain-text parts, and
  link to the closure page; the image is not mailed. Snapshot tests in nl and en.
- Embedding on host websites (ADR 0035, EM-1): allowed origins per organisation under
  Insluiten (`embedding.manage`, organisation admin and superuser), exact `https` origins,
  localhost only outside production; a group's own switch "Insluiten op websites" (off by
  default); audited with counts. ADR 0035 accepted.
- Widget API (ADR 0035, EM-2): `/api/v1/widget/{org}/{group}/` with the group, its form and
  texts, the month and the times of a day, and booking; CORS only for the organisation's
  allowed origins, `require_allowed_origin` on bookings, the standalone page's rate limits and
  honeypot, no token or personal data in answers.
- `<gov-booking>` Web Component (ADR 0035, EM-3/EM-4): vanilla JS under `widget/`, Shadow DOM,
  `--booking-*` tokens, `::part()`, ARIA grid with roving tabindex, focus to each step's
  heading, live region; bundled by esbuild in the image build and CI; served at
  `/widget/v1/gov-booking.js` with its SRI hash shown in the admin portal. Test host page on
  another origin with a strict CSP; axe, Lighthouse and a keyboard-only flow in nl and en.
- Documentation for host websites (ADR 0035, EM-5): `docs/widget.md` (allowing an origin,
  the code, attributes, CSP, versions and integrity, tokens and parts, privacy,
  troubleshooting) and a runbook section.
- admin: Make a new group's web address from its name (#56)
- admin: Staff directory with just-in-time records from OIDC (ADR 0046 step 1) (#61)
- admin: Live progress of updates, steps reported by the host updater (ADR 0048) (#63)
- admin: Maintenance mode switch and read-only enforcement (ADR 0038, MM-1, MM-2) (#65)
- audit: One event schema openplanner.event/1 (ADR 0040 part 2) (#70)
- audit: Ship audit events to a log platform, security events deduplicated (ADR 0034) (#72)
- audit: Filters per level, category, period and trace; CSV export (ADR 0040 part 3) (#79)
- settings: Settings registry, consistency test and settings CLI (ADR 0042 step 1) (#77)
- admin: Disable refused buttons during maintenance, from the matrix (ADR 0038, MM-2) (#66)
- audit: Diagnostic mode and retention per category (ADR 0040 part 4) (#84)
- admin: Navigation register, grouped side navigation and theme choice (ADR 0047 step 2) (#80)
- citizen: Maintenance page and machine-API write refusal (ADR 0038, MM-3) (#67)
- updater: Maintenance mode around backup, update and restore (ADR 0038, MM-4) (#68)
- cli: Lockout diagnostics, oidc-check and host closure commands (ADR 0042 step 8) (#73)
- admin: Group agenda with agents and shortfalls per slot (ADR 0044 step 1) (#81)
- admin: Custom roles within fixed bounds, role editor and change history (ADR 0039 step 3) (#71)
- admin: Planner moves, cancels and accepts shortfalls from the agenda (ADR 0044 step 2) (#92)
- admin: Ask citizens to choose a new time, one by one or per short slot (ADR 0044 step 3) (#98)
- worker: Report long maintenance; runbook, threat model, architecture (ADR 0038, MM-5) (#69)
- audit: Security events for attempts on public entry points (ADR 0053) (#97)
- settings: X_FILE for bootstrap secrets and app.cli init (ADR 0042 step 2) (#99)
- groups: Dispatch kind per group and own-queue change deadline (ADR 0043 step 1) (#88)
- settings: Platform settings with env lock, live reload and Advanced settings (ADR 0042 step 3) (#106)
- install: Host installer that installs, repairs and re-checks a host (ADR 0050) (#82)
- settings: Change class C settings in the portal and from the host (ADR 0042 step 4) (#108)
- queue: OpenPlanner's own queue with claim and release (ADR 0043 step 2) (#89)
- settings: Class D settings per organisation (ADR 0042 step 5) (#110)

### Security

- config: Refuse dev-only placeholder secrets in production (#103)

### Fixes

- e2e: Calendar tests no longer depend on the day of the month (#100)
- ci: Exempt authors with write access from the CLA box, also with a private membership (#104)
- migrations: Join the heads 0042 (dispatch kind) and 0048 (platform settings)
- migrations: Join the heads 0043 (own queue) and 0051 (organisation settings) (#112)

### Documentation

- adr: Dispatch option with own queue (0043) and planner agenda (0044) (#57)
- ADR 0042 lockout recovery without a login bypass (#58)
- adr: Dedicated Webex site and enforced meeting policy (0045) (#59)
- adr: Role and scope paired in one claim value (0049) (#64)
- adr: Admin information architecture and structure guard (0047) (#62)
- licence: EUPL-1.2 for every file, contributor licence agreement (ADR 0054) (#102)
- readme: Pitch OpenPlanner as a general Webex video-meeting scheduler (#105)

### Tooling and tests

- ci: Keep every dependency current, fail on deprecations (ADR 0051) (#94)
- tests: Run the test suite on every core with pytest-xdist (#101)
- e2e: Keep the direct-call group away from midnight (#78)
- ci: Run CI for the merge queue (#112)

## 0.2.0 (2026-10-28)

- Alternative contact options per organisation (ADR 0037, CB-1): up to six phone, email,
  website, chat and address options with labels and opening hours in nl/en, validated per
  kind, with a preview; the shared block for closure and maintenance pages.

## 0.1.0 (2026-10-27)

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
