# Security policy

## Reporting a vulnerability

Please do not open a public issue. Report vulnerabilities privately to the security contact
of the operating organisation (to be published in `/.well-known/security.txt` before go-live;
see open point O10 in `docs/HANDOVER.md`). Include steps to reproduce and the affected version.
We acknowledge within 5 working days.

## Supported versions

Until the first production release (1.0.0) only the latest released version receives security
fixes. From 1.0.0 the newest minor and the one before it do (ADR 0116); a longer window is an
extended maintenance line, agreed per installation. Security fixes are never behind a payment:
they are on `main` and on every supported line, and free for everyone.
Security fixes are released on the day they are merged (ADR 0052); `/admin/updates` shows the
running version and commit, and `git tag --contains <commit>` tells which releases hold a fix.

## Evidence for an audit

Where each measure behind the ASVS 5.0.0 level 2 audit of 1 November 2026 sits, and what is still open, is in `docs/asvs.md`. The checklists for what only the client's identity provider, host, Cisco or data controller can show are in `docs/assurance/`. Neither is a certificate.

## What CI checks

Every pull request: ruff (incl. bandit rules), mypy `--strict`, import-linter, bandit,
semgrep (`p/python`, `p/owasp-top-ten`, `p/secrets` and repo rules), pip-audit,
license allow-list, gitleaks, shellcheck (host scripts), hadolint, trivy (image CVEs and misconfiguration), schemathesis
against the OpenAPI spec, CycloneDX SBOM scanned with grype, Alembic up/down.

Also on every pull request: copy-paste detection (jscpd), `npm audit` on the test tooling,
Playwright + axe-core (WCAG 2.2 AA) + Lighthouse on every public page.

On merge to `main`: OWASP ZAP baseline scan, mutation testing on the domain (≥ 80%), image
pushed to GHCR, signed keyless with cosign and SBOM attested (`docs/runbook.md` shows how to
verify). A minor or major release (`vX.Y.0`) is also pushed to public packages
(`ghcr.io/<owner>/<repo>-public` and `-proxy-public`), so anyone can verify it without an
account (ADR 0108); candidates and patch releases stay private.

Weekly: Renovate dependency updates; security updates are opened immediately. Every pinned
version is tracked by Renovate (`make pins`), deprecation warnings fail CI, and a weekly
`freshness` workflow fails when Renovate stops running (ADR 0051).

The image runs as a non-root user (uid 10001) with a read-only root filesystem; only the data
volume is writable.
