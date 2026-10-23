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

