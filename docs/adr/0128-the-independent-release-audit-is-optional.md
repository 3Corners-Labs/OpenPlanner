# 0128. The independent release audit is optional

Status: accepted (2026-11-14), decided by the product owner in the conversation of 2026-11-14.
Built in the pull request that adds it (#PRNUM); see *Build status*. Supersedes ADR 0078 point 4
in part (a minor or major release no longer needs an audit, and no tag waits for a verdict) and
ADR 0079 point 3 (the `audit` job) with the audit parts of its points 4, 6 and 7 and its
setting `OPENAI_API_KEY`. Amends ADR 0078 point 1 (the audit scripts are no longer a gate, so no
longer a decision path).

## Context

ADR 0078 point 4 put an independent audit by a second model in front of every minor or major
release: the OpenAI Codex CLI reviews everything since the last release, the findings go into
the release PR under `## Release audit`, and `main.yml` refuses a tag `vX.Y.0` whose release PR
does not end that section with `Verdict: pass`. ADR 0079 made the review a job of the `release`
workflow, with `openai/codex-action` and a repository secret `OPENAI_API_KEY`.

That makes a release of this project depend on one outside vendor's account, tooling and price
list: without a working key, a running action and a person who edits the verdict, no minor
release can be tagged. The audit found real problems in `v0.4.0` (the tests that cite
"Release audit v0.4.0" are its legacy), but the product owner does not want it as a structural
step. In their words (2026-11-14): the OpenAI audit is not a structural flow, it will be picked
up now and then, so it may leave the dependency and the release audit.

What stands between `main` and a release without it: the gates of CLAUDE.md on every pull
request, the full lane in the merge queue (ADR 0058), the `security-review` skill and the
review questions on every pull request, the release PR as a decision that waits for the
product owner's label (ADR 0078 point 3), the tag checks of ADR 0052 point 4, the upgrade
matrix and its fixtures (ADR 0116), and the external penetration test before go-live and yearly
(O10).

Alternatives considered:

- **Keep the job behind a dispatch input, off by default.** Rejected: the workflow would still
  carry `openai/codex-action`, its pin, its runner needs (passwordless `sudo`, an ephemeral
  runner) and a secret, for a step that runs now and then. A laptop command does the same
  without a dependency of the forge.
- **Keep the tag check but let it pass without a section.** Rejected: a check that always
  passes is noise, and `Verdict: pass` would look like a gate that it no longer is.

## Decision

1. **The audit by a second model is optional and occasional.** No release, minor, major or
   patch, waits for it. Nothing in CI, the merge queue or the `release` workflow runs it, asks
   for it or reads a verdict.
2. **The tool stays, outside the forge.** `make release-audit` (`scripts/release-audit.sh`)
   keeps the review command: the Codex CLI's `codex review` against the last release, in a
   read-only sandbox. Running it needs the Codex CLI and an account of whoever runs it; neither
   is a dependency of the product, of CI or of the release. When it is run, the findings may be
   pasted into the release PR under `## Release audit`; findings that block the release are
   fixed in their own pull requests first, the rest become issues.
3. **What goes.** The `audit` job of `.github/workflows/release.yml` with `openai/codex-action`;
   the secret `OPENAI_API_KEY` (the repository admin deletes it); the check step "a minor or
   major release carries its audit" in the `tag` job and in the `publish` job of `main.yml`,
   with `scripts/check-release-audit.sh` and the `pull-requests: read` permission those steps
   needed; the pending audit section and `Verdict: open` in the release PR body and the
   `audit` subcommand of `scripts/release_pr.py`; the sentence in the public release notes that
   points to an audit.
4. **The audit scripts are no longer a decision path.** They were on the list of ADR 0078
   point 1 because they were a gate. `scripts/release-audit.sh` is now a tool;
   `scripts/check-release-audit.sh` is deleted. Removing a gate is itself a decision, which is
   why this pull request waits for `po:approved`.
5. **Unchanged:** every other release gate: the release PR as a decision with its label, the
   release plan (ADR 0052 point 2), the changelog and `publiccode.yml` edits, the tag checks of
   ADR 0052 point 4 in `release.yml` and `main.yml`, the upgrade matrix and its fixture
   (ADR 0116), release candidates (ADR 0059), the public images (ADR 0108) and the external
   penetration test (O10).

## Settings

None; a protection is not switched off by a setting, it is removed by this ADR. The repository
secret `OPENAI_API_KEY` of ADR 0079 (class A, repository admin) is retired.

**Fixed on purpose:** the review command in `scripts/release-audit.sh` (unchanged from ADR 0078).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | #PRNUM | merged |
| 1 | The `audit` job and `openai/codex-action` out of `release.yml`; the audit check out of the `tag` job and `main.yml`; `scripts/check-release-audit.sh` deleted; `scripts/release_pr.py` without the audit section and subcommand; the public release notes claim no audit; `scripts/decision_gate.py` without the audit scripts; `tests/scripts/test_release_audit.py::test_no_workflow_runs_the_audit_or_waits_for_it`; CLAUDE.md, runbook, release checklist, `docs/skills.md`, threat model, architecture document and the Status lines of ADR 0078 and 0079 | #PRNUM | merged |

## Consequences

- A minor or major release no longer depends on an OpenAI account, a working key or a person
  editing a verdict line. The product owner's acts for a release are the two dispatches and the
  label.
- The four-eyes remark of ADR 0078 (a second reviewer before every minor release) no longer
  holds by default. The independent human reviewer and the penetration test of open point O10
  are still needed before go-live; a second model's review is an extra when someone asks for it.
- The release workflow is smaller and needs no `sudo`, no ephemeral runner and no secret
  beyond the release app (ADR 0107), which helps the move to an internal forge (ADR 0079
  point 6).
- `v0.4.0` and `v0.5.0` keep their audit sections in their release PRs; the tests that cite
  "Release audit v0.4.0" stay, as they test real findings.
- To update in this PR: CLAUDE.md (*Git and process*), `docs/runbook.md` (*Releasing a
  version*), `docs/release-checklist.md`, `docs/skills.md`, `docs/threat-model.md`,
  `docs/applicatie-architectuur.md`, `docs/adr-status.md` and the Status lines of ADR 0078 and
  0079.
