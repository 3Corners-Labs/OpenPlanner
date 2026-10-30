# 0051. Dependency currency: every module tracked, deprecations fail the build

Status: accepted (2026-10-29), requested by the product owner. Amends ADR 0006 (supply chain:
how updates arrive) and the "Weekly: Renovate" line of CLAUDE.md. Built in the repository, checked on 2026-10-30; the Renovate app and the branch protection are settings outside the code (status was *proposed*; `docs/adr-status.md`).

## Context

GitHub Actions showed "Node.js 20 is deprecated" on every job of every run. The product owner
asked for a process that keeps *all* modules current during the build, not a one-off fix for
Node.js.

The cause was not the actions themselves but the process around them:

1. `renovate.json` existed, but Renovate never ran on the repository: no Dependency Dashboard,
   no Renovate PR ever. A config that nothing runs looks exactly like one with nothing to do.
2. Several versions were invisible to Renovate even if it had run: `UV_VERSION` and the gitleaks
   tag in workflows, `jscpd@…` and the shellcheck image in the Makefile, the Playwright image.
3. Major updates waited for a click on the dashboard. Moving off Node 20 meant majors for
   almost every action (checkout v4 → v7, setup-uv v6 → v10, …), so they would have waited.
4. Deprecation warnings are annotations; nobody reads them, and they never fail a build.

## Decision

1. **Renovate is the only update mechanism** for every pinned version: Python (`pyproject.toml`,
   `uv.lock` with weekly lock-file maintenance), npm (`e2e/`), Docker images (Dockerfiles,
   compose, `container:`, digests pinned), GitHub Actions (pinned to a commit SHA with a
   `# vX.Y.Z` comment), runner images, and every other version through a
   `# renovate: datasource=… depName=…` line directly above it (custom manager in
   `renovate.json`). The Renovate GitHub app must be installed on the repository.
2. **Nothing pinned without a tracker.** `make pins` (`scripts/check_pins.py`) fails on an action
   not pinned as `<sha> # vX.Y.Z` and on a version that no Renovate manager reads, in a
   workflow, the Makefile, a shell script (the host installer and updater among them) or a git
   hook. A minimum version that must not follow the latest release (such as the installer's
   Compose floor) says so with `# not a pin: <reason>`. A version needed in two places has one
   source (`check-proxy.sh` reads the Pebble image from its compose overlay). It also runs `renovate-config-validator --strict`. Part of `make check`
   and of the CI job `dependencies`.
3. **Deprecations fail the build.** The last job of `ci.yml` and of `main.yml`
   (`no deprecation warnings`, `scripts/check-deprecations.sh`) reads the annotations of every
   job in the run and fails on any that mentions a deprecation, whatever action, runtime, input
   or command causes it. Python deprecations already fail the tests (`filterwarnings = error`).
   The check is a required status check on `main`.
4. **Renovate must stay alive.** `.github/workflows/freshness.yml` runs every Tuesday and fails
   when the Dependency Dashboard was not refreshed in the last 8 days or an update PR has been
   open for more than 14 days.
5. **Setup carries the process.** `make setup` takes a fresh clone to a machine that runs
   `make check`: locked Python environment, e2e tooling, `.env`, and a `pre-push` hook
   (`.githooks/`) that runs the pin check. Every tool it needs is pinned and tracked; shellcheck
   comes from PyPI (`shellcheck-py`) so no Docker is needed for it. The host installer (ADR 0050)
   already brings host packages to their newest version on every run; its scripts fall under
   point 2 like any other.
6. **Majors are opened as their own PR**, labelled `major`, never parked behind a dashboard
   approval. Minor, patch and digest updates of actions are grouped in one weekly PR. Tools
   that must match are grouped: Playwright npm package and image, uv in CI and in the image,
   Caddy builder and runtime.
7. **Fresh, not reckless.** A release must be 3 days old before Renovate proposes it (a hijacked
   release is usually pulled within that time); security updates skip that wait and are opened
   at any time. Every update is still a PR with green CI and human approval (ADR 0012); no
   automerge.
8. **Stack pins stay where the stack puts them.** Python stays on 3.12 (`allowedVersions` in
   `renovate.json`); moving to a newer minor needs an ADR, as CLAUDE.md requires.

## Settings

None in the application: this decision lives in CI and Renovate, not in `app/`.

**Fixed on purpose** (in `renovate.json` and `freshness.yml`, changed by PR like any CI rule):
minimum release age 3 days, dashboard age 8 days (weekly run plus a day's slack), update-PR age
14 days (two weekly cycles), schedule "before 6am on monday" Europe/Amsterdam.

## Consequences

- One-off: every action moved to its latest release on Node 24; uv 0.13.6, gitleaks 8.30.1,
  shellcheck 0.11.0, jscpd 5.4.2, lighthouse 13.6.0, Pebble 2.10.1. jscpd 5 reads `exitCode` as "fail on any
  clone"; `.jscpd.json` drops it so the 1% `threshold` stays the gate, as before.
- The repository owner must install the Renovate app and add `no deprecation warnings` as a
  required check; until then `freshness` fails every week, which is intended.
- License exception (CLAUDE.md: AGPL/GPL needs an ADR): `renovate` is AGPL-3.0. It runs only
  as a CI/developer tool (`npx renovate-config-validator`) and as the hosted app; it is never
  a project dependency, never linked, never in the image. This ADR is that exception.
- The host installer of ADR 0050 (open PR at the time of writing) needs `# not a pin:` above
  `COMPOSE_MIN`; whichever of the two lands second adds it.
- A new pinned version in a workflow or the Makefile needs a `# renovate:` line, or CI fails.
- A deprecation in an upstream action blocks merges until that action is updated or replaced.
  If upstream has no fix yet, the way out is a PR that replaces the action, not an allow-list.
- `docs/dependencies.md`, `CONTRIBUTING.md`, `SECURITY.md` and CLAUDE.md describe the process.
