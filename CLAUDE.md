# CLAUDE.md — working agreements for this repository

This file is read by Claude Code at the start of every session. It states how we work, not what we build; the what is in `docs/HANDOVER.md` and `docs/adr/`.

## Project in one line
Lightweight group-booking service for a government contact center: embeddable widget, admin UI with IdP roles, Webex video links, Webex Contact Center task dispatch via Webex Connect, strict PII retention.

## Language
- Code, identifiers, comments, commit messages, ADRs, README: **English**.
- User-facing text (widget, emails, SMS, admin UI): multilingual by design, **Dutch default**, English required, more locales possible. All text goes through the self-contained `app/i18n/` module (ADR 0004): stable dotted keys (`booking.form.email.label`), gettext catalogues per locale, overrides editable in the admin UI. Never hard-code user-facing strings; always `_()`. New keys require both `nl` and `en` entries in the same PR. Locale: `?lang=` → `lang` cookie → `Accept-Language` → organisation default → `nl`.
- `docs/HANDOVER.md` is Dutch (stakeholder document); do not translate it.

## Stack (do not introduce alternatives without an ADR)
- Python 3.12, FastAPI, SQLAlchemy 2.x (async), Alembic, Pydantic v2.
- Database: SQLite in WAL mode (`DATABASE_URL=sqlite+aiosqlite:///data/booking.db`). Postgres is not a target (decided 2026-10-23); changing that needs an ADR. No raw SQL outside `app/adapters/db/`.
- Jobs: own `jobs` table + worker loop in `app/jobs/`. No Celery, Redis, APScheduler.
- Admin UI: Jinja2 + HTMX, server-rendered. No SPA framework.
- Widget: one Web Component in vanilla JS under `widget/`, bundled with esbuild in CI only. No React/Vue. Shadow DOM; all colours, spacing, radius and font read from `--booking-*` CSS custom properties with NL Design System defaults; `font-family: inherit`; interactive elements exposed via `part="..."`. The host site styles the widget only through those tokens and parts; document them in `docs/widget.md`. No inline styles, no `!important`.
- HTTP client: `httpx`. Logging: `structlog` (JSON). Settings: `pydantic-settings` from env only; no config files with secrets.
- Containers: `web`, `worker`. One `docker-compose.yml` for dev and prod shape.

## Architecture rules
- `app/domain/` has **no** imports from FastAPI, SQLAlchemy, httpx, or any adapter. Pure Python, fully unit-testable. Domain errors carry codes, never user-facing text.
- Layers (import-linter): `app.main` → `app.api | app.admin | app.jobs` (never importing each other) → `app.web` (shared HTML plumbing: locale, templates, `render()`) → `app.adapters` → `app.domain`. `app.i18n` is independent of all of them. Every HTML response goes through `app.web.templates.render`.
- Modular by design (ADR 0005): one home per concern; extract shared behaviour at its second use. Every module, class and public function in `app/`, `migrations/` and `scripts/` has a Google-style docstring stating its responsibility.
- Every external system is an adapter behind a Protocol in `app/domain/ports.py`: `VideoProvider`, `ContactCenterDispatcher` (WxCC Tasks API, ADR 0007), `MessageSender` (one adapter per `ChannelKind`: email, sms, later whatsapp; ADR 0009), `IdentityProvider`, `Clock`. Persistence goes through the `Transaction` Protocol in the same file; its adapter lives in `app/adapters/db/`. Tests use fakes; contract tests use recorded fixtures.
- Message templates are fixed layouts with editable text blocks from `app/i18n` (catalogue → organisation override → `group_text`), `{placeholder}`s from an allow-list only. Never let admins edit Jinja or HTML (ADR 0009).
- Authorization is one dependency: `require_scope(org_id=None, group_id=None)`. Every admin query filters by the scope object. Never bypass it "for superuser"; superuser is a scope that matches everything.
- All time stored as UTC `datetime` with tzinfo; conversion to the group's timezone happens only at the edges (rendering, parsing).
- A time-based job stores `pinned_start`; on run it re-reads the booking and exits silently if status != confirmed or start != pinned_start. Cancelling or rescheduling a booking deletes its pending jobs in the same transaction.
- Slot creation and booking happen in one transaction with a write lock (`BEGIN IMMEDIATE` on SQLite, `SELECT ... FOR UPDATE` on Postgres). Capacity is snapshotted on the slot at creation.
- No PII in logs, exceptions, job payloads, audit events, or URLs. The structlog processor in `app/logging.py` masks `name`, `email`, `phone`, `note`; a test asserts this on a fake log stream.
- Management tokens (ADR 0011): HMAC-SHA256 of a 32-byte random per-booking salt under `MANAGEMENT_TOKEN_KEY` (env only); store the salt and `sha256(token)`, never the token. Compare with `hmac.compare_digest`. Never put a token in a job payload or log.

## Delivery surface
- Default: a standalone page per group at `/g/{slug}` in the organisation's branding; the client's website links to it after its own decision tree. We are the end station; there is no routing step in this service.
- Optional: the same UI as an embeddable Web Component for sites that want it inline.
- Booking form asks only email, name and (per group optional) phone. Never BSN, never DigiD, no free-text field unless a group explicitly enables one.

## Code style and quality gates (CI fails on any of these; branch protection makes CI mandatory, no `[skip ci]`)
Per PR:
- `ruff format` and `ruff check` (config in `pyproject.toml`; rules: E, F, I, B, UP, S, N, ASYNC, PT, D with Google convention).
- `jscpd` copy-paste detection on `app/`, `tests/` (and `widget/`), threshold 1%.
- `mypy --strict` on `app/`.
- `import-linter`: `app/domain` imports nothing from `app/adapters`, `app/api`, `app/admin`, `app/jobs`, nor any framework.
- `pytest` with coverage ≥ 90% on `app/domain/` and `app/i18n/`, ≥ 75% overall. Tests mirror the source tree. Includes the authorisation matrix (every role × every admin endpoint × own/other group) and the abuse cases from `docs/threat-model.md`.
- `bandit -r app/`; `semgrep` with `p/python`, `p/owasp-top-ten`, `p/secrets` and the repo rules in `.semgrep/` (no `print`, no PII field names inside log calls).
- `pip-audit`, `uv lock --check`, `pip-licenses --allow-only` against the allowed list (exceptions by ADR; certifi: ADR 0003); `npm audit` for the `e2e/` tooling.
- `gitleaks`.
- `hadolint` on the Dockerfile; `trivy` on the built image (CVE + misconfiguration); image runs as non-root with a read-only filesystem.
- `schemathesis` against the generated OpenAPI spec.
- Playwright: keyboard-only booking flow (no mouse), `axe-core` zero violations (WCAG 2.2 AA), Lighthouse accessibility ≥ 95, for every page in `e2e/pages.json` in every locale.
- Alembic migration applied up and down on a fresh database.
- CycloneDX SBOM generated and scanned with `grype`.
On merge to `main`:
- OWASP ZAP baseline scan against the started container.
- `mutmut` on `app/domain/` and `app/i18n/`, mutation score ≥ 80%.
- Image pushed to GHCR and signed keyless with `cosign`, SBOM attested; deployment verifies the signature (ADR 0006).
Weekly: Renovate updates (security updates immediately), `trivy` on the running image, ZAP full scan against staging. Daily: alert if `purge_pii` backlog > 0.

## Accessibility rules (WCAG 2.2 AA; axe is necessary, not sufficient)
- All ARIA labels, roles and live regions live **inside** the shadow root; nothing relies on the host page.
- Calendar grid: `role="grid"`, arrow-key navigation with roving `tabindex`, visible focus ring (3:1 contrast), Escape closes overlays, focus moves to the next step's heading after each transition.
- `aria-live="polite"` region announces availability changes, errors and the booking result.
- No time limits on forms (2.2.1); no CAPTCHA puzzles (3.3.8); reschedule pre-fills known data (3.3.7); targets ≥ 24×24 px (2.5.8); honour `prefers-reduced-motion` and `prefers-contrast`.
- Every control has a visible label; error messages are linked via `aria-describedby`; colour is never the only signal.
- Emails: semantic HTML, text alternative for the logo, plain-text part always included.
- Before go-live: manual pass with NVDA + Firefox and VoiceOver + Safari (macOS and iOS), documented in `docs/a11y-audit.md`.
- `openapi` spec generated and diffed; breaking changes to `/api/v1` need an ADR.
- Dependencies pinned with hashes (`uv lock`); Renovate opens update PRs weekly; security updates immediately.
- SBOM (CycloneDX) produced on every release and attached to the GitHub/GitLab release.

## Security baseline
- Rate limiting on public endpoints (`slowapi`): 10 bookings/IP/hour, 60 availability reads/IP/minute. Honeypot field on the booking form. Optional Cloudflare Turnstile behind a feature flag; never Google reCAPTCHA.
- CSP, HSTS, `X-Frame-Options` (admin), CORS allow-list from settings. Widget endpoints allow only configured origins.
- Calls to WxCC use OAuth tokens of a service app (ADR 0007). Every inbound webhook from WxCC is verified (signature or token, per WxCC's mechanism); reject if |now − ts| > 300 s.
- Bootstrap secrets (settings encryption key, management token key, OIDC client secret, session secret) via env/secret store only; `.env.example` has placeholders. Integration secrets (SMTP, WxCC, messaging) are entered in the admin UI, stored AES-256-GCM encrypted, write-only, audited (ADR 0008). `gitleaks` blocks commits.
- OIDC (admin portal only; citizens never log in): `authlib`, PKCE, `state` and `nonce` checked, session cookie `Secure; HttpOnly; SameSite=Lax`, 8 h lifetime.
- Dependencies: no package without an active maintainer and a license in {MIT, BSD, Apache-2.0, PSF}. AGPL/GPL packages need an ADR.
- Threat model lives in `docs/threat-model.md` (STRIDE per flow) together with an abuse-case list per public endpoint (slot exhaustion by bots, token guessing, rescheduling into another group's slot, PII through error messages). Every abuse case has a test. Update both when a flow changes.
- External penetration test before go-live and yearly; `SECURITY.md` and `/.well-known/security.txt` describe responsible disclosure.

## Privacy rules
- PII fields: `name, email, phone, note, guest_join_url, host_join_url`. Only on `Booking`. Nowhere else.
- `purge_pii` job runs at `slot.end + 24h` or `cancelled_at + 24h`; sets fields to NULL and `pii_purged_at`. A nightly sweep re-checks for stragglers and alerts if any booking older than 48 h still has PII.
- Outbound payloads to Webex Contact Center contain only first name and the host join URL.
- Data flow diagram in `docs/dataflow.md` is the input for the DPIA; keep it current.

## Git and process
- Trunk-based: short-lived branches, PR required, one approving **human** review, CI green. Squash merge. Claude Code opens PRs and never merges them.
- Every PR must answer, in its description, the five review questions:
  1. Which endpoint or job is new or changed, and is `require_scope` on it?
  2. Which input comes from outside, and where is it validated?
  3. Can PII reach a log, error message, URL or external payload here?
  4. Which test would fail if this change were wrong?
  5. Is a dependency added, and why?
- If a `security-review` skill exists in `.claude/skills/`, run it before opening the PR and paste its findings into the description.
- Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `security:`). `CHANGELOG.md` updated from commits on release.
- One ADR per decision in `docs/adr/NNNN-title.md` (status, context, decision, consequences). Numbered, never rewritten; superseded ADRs are marked.
- Every PR description: what, why, how tested, screenshots for UI, i18n checked (nl/en), migration reversible (yes/no).
- Migrations: Alembic, always reversible, never edit a merged migration.
- Releases tagged `vMAJOR.MINOR.PATCH`; image tagged with version and git SHA.

## Documentation set (keep all present and current)
- `README.md` — what, quick start (`docker compose up`), where to look next.
- `CONTRIBUTING.md` — dev setup, running tests, PR checklist.
- `SECURITY.md` — how to report, supported versions, scanning in CI.
- `docs/HANDOVER.md` — stakeholder design (Dutch).
- `docs/architecture.md` — architecture with Mermaid diagrams; update the diagrams in the same PR as the change they show.
- `docs/adr/` — decisions.
- `docs/runbook.md` — deploy, rollback, backup/restore, rotating secrets, what to do when Connect is down.
- `docs/dataflow.md`, `docs/threat-model.md` — privacy and security inputs.
- `docs/api.md` — generated from OpenAPI; `docs/webhooks.md` — payloads to/from Webex Connect.
- `docs/widget.md` — how a website embeds the widget, CSP requirements, attributes.

## How to work in this repo (for Claude Code)
- Read `docs/HANDOVER.md` §3 (domain) and §5 (flows) before touching domain code.
- Start every task by writing or updating the failing test. Domain first, adapter second, HTTP last.
- Do not add a dependency without saying why in the PR and checking its license and maintenance.
- Do not stub external calls inside `app/`; put fakes in `tests/fakes/`.
- When a requirement is ambiguous, write the assumption at the top of the PR description and continue; do not block.
- Never log, print or include in an error message any of the PII fields.
- Run `make check` (ruff, mypy, import-linter, pytest, bandit, pip-audit, lock, jscpd) before proposing a PR.
- Name the skill you are about to use; `docs/skills.md` maps skills to work packages. Never use skills that merge or deploy.

## Open decisions (see HANDOVER §11)
O1 video provider token/expiry · O2 unclaimed-task behaviour · O3 IdP claim names · O4 agents in multiple groups · O5 branding per organisation · O6 WxCC licence scope · O7 hosting and backup owner · O8 video-call accessibility (captions, sign-language interpreter) · O9 "start now" without a slot · O10 who reviews, who pentests.
