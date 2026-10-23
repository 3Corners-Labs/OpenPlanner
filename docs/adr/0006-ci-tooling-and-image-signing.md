# 0006. Test tooling outside the Python runtime; image publishing and signing

Status: accepted (2026-10-23)

## Context

`CLAUDE.md` requires Playwright, axe-core and Lighthouse checks, mutation testing on `main`,
and signed images. The license allow-list in `CLAUDE.md` covers dependencies of the service.

## Decision

1. **Browser checks** live in `e2e/` (npm, exact versions, `package-lock.json`):
   `@playwright/test` (Apache-2.0), `@axe-core/playwright` and `axe-core` (MPL-2.0, named in
   `CLAUDE.md`), `lighthouse` and `chrome-launcher` (Apache-2.0). They run only in CI and are
   never part of the image. `npm audit --audit-level=moderate` gates them.
   `@lhci/cli` was rejected: its setup wizard pulls in 14 known-vulnerable transitive packages.
   `e2e/scripts/lighthouse.mjs` enforces the threshold (accessibility ≥ 95) instead.
2. **One list of public pages**, `e2e/pages.json`, drives both axe and Lighthouse. Each page is
   checked in every listed locale (`?lang=`). The list is empty until work package 3.
3. **Mutation testing** with mutmut (BSD-3-Clause, dev dependency) on `app/domain`, on merge to
   `main`. `scripts/check_mutation_score.py` enforces ≥ 80%. It passes with a notice while the
   domain has no tests yet.
4. **Images** are pushed to `ghcr.io/<owner>/<repo>` on merge to `main` and on `v*.*.*` tags
   (tags: commit SHA, branch, version). They are signed keyless with cosign through GitHub OIDC,
   with the CycloneDX SBOM attached as a signed attestation. On a release tag the SBOM is also
   attached to the GitHub release.

## Consequences

Deployment must run `cosign verify` (see `docs/runbook.md`). Enforcing that automatically
depends on the hosting choice (open point O7).
