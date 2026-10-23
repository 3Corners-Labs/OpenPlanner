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

