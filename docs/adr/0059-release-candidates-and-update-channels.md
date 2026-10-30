# 0059. Release candidates on every merge, and an update channel per installation

Status: accepted (2026-10-30), requested by the product owner. Amends ADR 0052 (point 1: no
pre-release tags) and ADR 0029 (§1: an image of `main` is never offered to an installation).
Extends ADR 0050 (installer modes) and ADR 0058 (what runs where in CI). Amended the same day
by ADR 0067 (points 4 and 6: channel and auto-apply are class C portal settings; the updater's
identity follows the installer mode, not the channel).

## Context

On 2026-10-30 the product owner found that from an idea to a result they could try live took
three days, and that many decisions existed only as ADRs. Their own test moments were missing.
Two causes:

1. **A merge is invisible.** ADR 0052 made a release a deliberate pair of actions (release PR,
   tag). That is right for production, but it meant that nothing merged in between could be
   seen running anywhere. v0.3.0 showed the other side: the release PR was merged and the tag
   was forgotten, so the test host stayed on 0.2.0 for a day without anybody noticing.
2. **The image waits for the slowest check.** `main.yml` builds the image only after the
   mutation test (8 of its 10 minutes). The build and signature themselves take one minute.

The test host (docker01, installer mode `lab`) already has everything else: a host updater
that pulls, verifies, backs up, switches and rolls back (ADR 0029), and live progress on
`/admin/updates` (ADR 0048). What it lacks is something to follow other than a release.

The same loop matters for Claude Code: a change should be seen working on the test host before
the next chunk of work is started on top of it.

## Decision

1. **Every merge to `main` produces a release candidate.** `main.yml` computes
   `X.Y.Z-rc.N`: `X.Y.Z` is the version the rules of ADR 0052 give for the commits since the
   last release (or `pyproject.toml`'s version when a release PR already raised it), and `N` is
   one above the highest existing `rc` tag of that version. It builds and signs the image
   `ghcr.io/<owner>/<repo>:X.Y.Z-rc.N` with `APP_VERSION=X.Y.Z-rc.N` and `APP_COMMIT`, then
   sets the annotated tag `vX.Y.Z-rc.N` on the merge commit and publishes a GitHub
   **pre-release** with the SBOM and generated notes. Only CI makes `rc` tags: a hand-pushed
   one fails the tag check of ADR 0052 point 4. The candidate job runs one at a time
   (concurrency group), so two merges never claim the same number. A candidate whose build
   fails leaves no tag and no pre-release.
2. **The candidate does not wait for the mutation test.** The candidate job needs only the
   ZAP baseline (one minute): the merge queue already ran the full lane on the same commit
   (ADR 0058: container checks, matrix, Playwright). Mutation testing measures test quality,
   not the image, and keeps running on every push to `main`; a failure still fails the run
   and is visible. Release tags keep the full gate (ZAP **and** mutation before anything is
   pushed), as ADR 0052 says.
3. **A release stays what ADR 0052 made it.** Release PR, tag `vX.Y.Z` on the product
   owner's instruction, full gate, GitHub release. `make release-plan` ignores `rc` tags when
   it looks for the last release. The release candidates of a released version may be deleted
   afterwards (tags, pre-releases and images); installations have moved on to the release.
4. **An installation subscribes to a channel.** `UPDATE_CHANNEL=release` (default) offers only
   releases, as today. `UPDATE_CHANNEL=rc` offers the newest of releases and pre-releases
   together, so an installation on `rc` also moves to the final release when it comes. The
   worker lists the repository's releases instead of asking `/releases/latest`; drafts are
   never offered. The channel is a deployment setting (class B): it describes what the host is
   for, like the installer mode, and the application refuses `rc` when `ENVIRONMENT=prod`.
   The installer (`--channel`) refuses it in mode `production` and writes it for `test` and
   `lab`; the updater's signing identity on `rc` accepts images signed by `main.yml` on
   `main` as well as on a release tag. On `release` the identity stays tags-only.
5. **Versions order as SemVer 2.0.0 says.** `0.4.0-rc.3 < 0.4.0` and `0.4.0-rc.3 > 0.3.0`;
   `rc` numbers order numerically. `Version` carries an optional candidate number; `main`,
   local builds and anything else remain "not a version" and are never offered an update.
6. **Optional: the installation applies updates itself.** With `UPDATE_AUTO_APPLY=true`
   (class B, default off) the worker turns a newer version into an update request as soon as
   the check finds one and no request is open, audited as actor `system` with detail
   `update.auto_request`. The host updater carries it out as any request, with the same
   backup, health check and rollback. Refused when `ENVIRONMENT=prod`: production updates
   stay a superuser's decision (ADR 0029). docker01 runs `rc` with auto-apply, so every
   merge is live there within minutes.
7. **Every ADR shows what is built.** From this ADR on, an ADR carries a *Build status*
   section: one line per step with its PR and state (planned, in PR #N, merged, live on the
   test host). Claude Code updates it in the PR that moves a step, and a status answer names
   what runs on the test host, not what is decided on paper. Existing ADRs get the section
   when they are next touched; `docs/adr-status.md` holds the one-off inventory of
   2026-10-30 until then.
8. **A change is finished when it is seen running.** After a merge, Claude Code checks the
   change on the test host (the candidate that carries it) and reports what it saw. CI green
   is not the end of a step.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `UPDATE_CHANNEL` | B deployment | `release` | `release`, `rc` | none (env) | What the host is for, like the installer mode; `rc` refused with `ENVIRONMENT=prod`. Written by `scripts/install.sh --channel`. |
| `UPDATE_AUTO_APPLY` | B deployment | `false` | bool | none (env) | Apply a newer version without a superuser's click; refused with `ENVIRONMENT=prod`. |

Both are B because the portal must not be able to move an installation onto pre-release code
or unattended updates: that is a decision about the host, taken by the operator who also
chose its mode. `APP_VERSION` (B, baked into the image) now also holds `X.Y.Z-rc.N`.

**Fixed on purpose:** the `rc` numbering and the pre-release flag (process rules, as in ADR
0052); the hourly check interval (`CHECK_INTERVAL`, ADR 0029) also applies to the `rc`
channel: the candidate is live within the hour, or within a minute after "Update now".

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | `main.yml`: candidate version, image, tag, pre-release; publish without waiting for mutation; `release_plan --candidate`; runbook | #131 | merged 2026-10-30 |
| 2 | `Version` with `rc`, channel `rc` in the release adapter, `UPDATE_CHANNEL` and `UPDATE_AUTO_APPLY`, auto request in the worker, updater and installer `--channel`, `/admin/updates` shows the channel | #132 | merged 2026-10-30 |
<<<<<<< HEAD
| 3 | docker01 on `rc` with auto-apply; first candidate seen live | | done 2026-10-30: docker01 runs 0.4.0-rc.5, channel and auto-apply shown; the switch needed the installer because 0.3.0 predates this ADR (hence ADR 0067) |
=======
| 3 | docker01 on `rc` with auto-apply; first candidate seen live | #140 | live on the test host 2026-10-30: `v0.4.0-rc.5` (runbook, *Test host on the rc channel*) |
>>>>>>> origin/main

## Consequences

- From merge to running on the test host: about three minutes with auto-apply, instead of a
  release that had to be remembered. The product owner and Claude Code see every step live.
- Many tags: one per merge. They are cheap, and `git tag --contains <commit>` keeps working
  for the question "which candidate or release has this fix". Candidates of a released
  version may be pruned.
- The Actions tab shows a cancelled candidate job when three merges follow within a minute:
  the concurrency group keeps only the newest waiting run, whose candidate carries all of
  them. ZAP and mutation still run for every commit.
- An `rc` image skipped only the mutation test; it passed the merge queue's full lane and the
  ZAP baseline. The release gate is unchanged. The updater's identity on `rc` installations
  also accepts `refs/heads/main`; on `release` it does not.
- `docs/runbook.md` (*Release candidates*), `docs/install.md` (channel), `docs/settings.md`
  (generated), `.env.example`, `scripts/updater/updater.env.example` and the manual topic of
  the updates page change. The threat model gains the abuse case "a hand-made `rc` tag or a
  foreign pre-release offered to an installation" (refused by the tag check and the
  release-page check).
- ADR 0052 point 1 ("there are no pre-release tags") is amended: there are, and only CI makes
  them. ADR 0029 §1 is amended: an installation on channel `rc` is offered candidates.
