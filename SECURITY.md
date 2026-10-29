# Security policy

## Reporting a vulnerability

Please do not open a public issue. Report vulnerabilities privately to the security contact
of the operating organisation (to be published in `/.well-known/security.txt` before go-live;
see open point O10 in `docs/HANDOVER.md`). Include steps to reproduce and the affected version.
We acknowledge within 5 working days.

## Supported versions

Only the latest released version receives security fixes until the first production release.

## What CI checks

Every pull request: ruff (incl. bandit rules), mypy `--strict`, import-linter, bandit,
semgrep (`p/python`, `p/owasp-top-ten`, `p/secrets` and repo rules), pip-audit,
license allow-list, gitleaks, shellcheck (host scripts), hadolint, trivy (image CVEs and misconfiguration), schemathesis
against the OpenAPI spec, CycloneDX SBOM scanned with grype, Alembic up/down.

Also on every pull request: copy-paste detection (jscpd), `npm audit` on the test tooling,
Playwright + axe-core (WCAG 2.2 AA) + Lighthouse on every public page.

On merge to `main`: OWASP ZAP baseline scan, mutation testing on the domain (≥ 80%), image
pushed to GHCR, signed keyless with cosign and SBOM attested (`docs/runbook.md` shows how to
verify).

Weekly: Renovate dependency updates; security updates are opened immediately. Every pinned
version is tracked by Renovate (`make pins`), deprecation warnings fail CI, and a weekly
`freshness` workflow fails when Renovate stops running (ADR 0051).

The image runs as a non-root user (uid 10001) with a read-only root filesystem; only the data
volume is writable.
