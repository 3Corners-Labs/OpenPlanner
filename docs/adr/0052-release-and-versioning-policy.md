# 0052. Releases, version numbers and knowing what runs

Status: accepted (2026-10-29), requested by the product owner. Builds on ADR 0006 (signed
images), ADR 0012 (actions on explicit instruction) and ADR 0029 (updates from the portal).
Amended by ADR 0059 (point 1: CI tags a release candidate `vX.Y.Z-rc.N` on every merge)
and ADR 0078 (point 3: a minor or major release carries an independent audit first).

## Context

Every merge to `main` builds, signs and pushes an image (`main`, `sha-<commit>`), but an
installation is only offered an update when a `v*.*.*` tag becomes a GitHub release
(ADR 0029 §1). Until now the tags were set by hand (`v0.1.0`, `v0.2.0`) without written rules:
nobody could say when a change is MAJOR, MINOR or PATCH, `pyproject.toml` still said `0.1.0`
after `v0.2.0`, and the portal showed only the version. A fix merged to `main` is not live
until a release contains it, and with security fixes coming that gap has to be visible and
short. The product owner asked for one clear procedure, without steps that happen "by magic".

## Decision

1. **A version exists only as a tag.** `vMAJOR.MINOR.PATCH` (Semantic Versioning 2.0.0) on a
   commit of `main`. A merge to `main` is no release; its image is for testing and is never
   offered to an installation (ADR 0029 §1). There are no pre-release tags (`-rc.1`) and no
   build metadata (`+…`): a Docker tag cannot hold `+`, and the commit already identifies the
   build (point 5).
2. **The number follows the commits since the last tag** (Conventional Commits):
   - **MAJOR** (from 1.0.0): a breaking change, marked `!` after the type or
     `BREAKING CHANGE:` in the body. Breaking means an operator or integrator must act:
     `/api/v1` or the machine API changes incompatibly (that needs an ADR anyway), an
     environment variable is removed or renamed or gets a stricter meaning, the compose file or
     the host updater's contract (`app.cli` commands, `updater.env`) changes, a migration cannot
     be undone by the previous version, or a setting's bounds narrow so that stored values
     become invalid.
   - **MINOR**: at least one `feat`. New screens, settings with a default, reversible
     migrations.
   - **PATCH**: everything else: `fix`, `security`, `chore(deps)`, `docs`, `test`, `refactor`.
   - **Before 1.0.0** a breaking change raises MINOR, not MAJOR (SemVer §4). 1.0.0 is the first
     production release, decided by the product owner.
   `make release-plan` prints the last tag, the commits since then and the number these rules
   give. It changes nothing.
3. **A release is a release PR plus a tag, both on the product owner's instruction.**
   The release PR (`chore(release): vX.Y.Z`) sets `project.version` in `pyproject.toml`
   (and `uv.lock`) and moves *Unreleased* in `CHANGELOG.md` under `## X.Y.Z (date)`. After it is
   squash-merged with green CI, the annotated tag `vX.Y.Z` is set on that merge commit and
   pushed. The tag message records who gave the instruction and when (ADR 0012). Nobody
   creates the GitHub release by hand: `main.yml` does that after the image is signed.
   `docs/runbook.md`, *Releasing a version*, lists the commands.
4. **CI refuses a wrong tag.** On a tag, `main.yml` checks, before it builds or pushes
   anything, that the tagged commit is on `main` and that `pyproject.toml` says the same
   version. If either fails, no image, no release and no update offer is made; delete the tag
   and set it again on the right commit.
5. **Every image knows its commit.** `main.yml` bakes `APP_COMMIT` (the full git SHA) into every
   image, next to `APP_VERSION` on releases. `/admin/updates` shows the first 12 characters,
   linked to the commit on GitHub when `UPDATE_REPOSITORY` is set, and `python -m app.cli
   version` prints both on the host. Whether a fix runs on an installation is then one
   question to git: `git tag --contains <fix commit>` lists the releases that have it; the
   portal says which release (and commit) runs.
6. **Security fixes are released at once.** A `security:` or security-relevant `fix:` commit is
   released on the same working day it is merged, as the next number by point 2 (a PATCH when
   only fixes are waiting, else MINOR with whatever features are on `main`). Only the latest
   release is supported (`SECURITY.md`); there are no maintenance branches before 1.0.0.
   Fixing an older line (`release/1.4`) after 1.0.0 needs its own ADR. Its release notes say
   that it is a security release, without details that help an attacker before installations
   have updated.
7. **Release regularly.** At least after each finished work package, so that `main` and the
   live version do not drift apart; `make release-plan` shows the drift.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `APP_COMMIT` | B deployment (build) | empty | 7–40 lower-case hex, or empty | none (baked into the image) | Set by `main.yml`; describes the image itself, like `APP_VERSION` (also B), so it cannot come from the database. |

**Fixed on purpose:** the version rules of point 2, the 12-character short commit on the page,
the same-day rule for security releases and "only the latest release is supported". They are
process rules, not tunable behaviour of the application.

## Consequences

- One procedure with two deliberate steps (release PR, tag) and one automatic chain after the
  tag: ZAP and mutation tests, image with version and commit, signature, SBOM, GitHub release,
  update banner within an hour. Each step is visible in the Actions tab.
- A tag on the wrong commit or with a mismatching `pyproject.toml` fails loudly instead of
  producing a release that says something else than it contains.
- `make release-plan` relies on commit messages; a breaking change merged without `!` would be
  numbered too low. The PR review questions and the squash title are where that is caught.
- New: `scripts/release_plan.py` (no dependency; standard `tomllib` and `git`), the
  `version` CLI command, the `APP_COMMIT` build argument, the check in `main.yml` and the
  runbook section. `pyproject.toml` is aligned with the last release (`0.2.0`).
