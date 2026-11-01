# 0079. The release is a workflow: release PR, audit and tag by dispatch

Status: accepted (2026-10-31, `po:approved` on #183), requested by the product owner. Amends
ADR 0052 (point 3: the release PR and the tag come from the `release` workflow; the dispatch is
the instruction) and ADR 0078 (point 4: the audit runs as a job, not on a laptop; its step 5 is
prepared here). Partly built; see *Build status*.

## Context

ADR 0052 made a release two deliberate steps, a release PR and a tag, and ADR 0078 put an
independent audit by a second model in front of every minor or major release. All three were
laptop work: `make release-plan`, three files edited by hand or by Claude Code, `make
release-audit` with the Codex CLI installed locally, the findings pasted into the PR, and
`git tag` plus `git push` from a checkout that had to be on the right commit. Each step was
small; together they were a ritual that only one person knew, and the product owner asked for
no manual steps at all.

The orchestrator the product owner wants is the forge itself: GitHub Actions today, portable
to Forgejo or Gitea Actions on an internal Git later. Agents run as jobs; the human acts are a
`workflow_dispatch` click and the label `po:approved` (ADR 0078). Existing tools come first:
`openai/codex-action` installs the Codex CLI and guards the API key; `scripts/release_plan.py`
already computes the number; `scripts/release-audit.sh` and `scripts/check-release-audit.sh`
already hold the audit command and the tag gate.

Two facts of GitHub shaped the design. A pull request or tag made with the workflow's own
`GITHUB_TOKEN` starts no other workflow, so a release PR opened that way would get no CI and a
tag pushed that way no build. And `openai/codex-action` runs `codex exec` with a prompt; the
audit of ADR 0078 is `codex review --base <last release>`, a different subcommand with the
CLI's own review prompt.

## Decision

1. **Two dispatches of `.github/workflows/release.yml`, and the dispatch is the instruction.**
   `step = release-pr` opens the release PR; `step = tag` tags the merged release. Both are a
   `workflow_dispatch` by a collaborator on the default branch. The dispatcher's login and the
   date go into the PR body ("on the dispatch of …") and into the tag message ("vX.Y.Z: tagged
   on the instruction of <actor> on <date>"), which is the record ADR 0012 and ADR 0052
   point 3 ask for. Nobody runs the steps on a laptop any more; the scripts still work there
   as a fallback.
2. **The release PR comes from a tested script; the workflow stays thin.** `scripts/release_pr.py`
   decides the number (the `version` input, or what `scripts/release_plan.py` gives; never at
   or below the last release), sets it in `pyproject.toml` and `uv.lock`, moves *Unreleased*
   in `CHANGELOG.md` under `## X.Y.Z (date)`, and writes the PR body with every section of the
   PR template answered. The workflow makes the branch `release/vX.Y.Z`, commits
   `chore(release): vX.Y.Z`, opens the PR and enables auto-merge. A release PR is a decision
   (ADR 0078 point 1): it merges once CI is green and the product owner has set `po:approved`.
   A second PR for the same release is refused; a branch left by a failed run is replaced.
3. **The audit of a minor or major release is a job.** For `vX.Y.0` the job `audit` runs after
   the PR exists: `openai/codex-action` (pinned by commit) installs the Codex CLI, starts its
   proxy so that `OPENAI_API_KEY` never reaches the review process, and drops `sudo` on the
   runner; then `scripts/release-audit.sh` runs the same `codex review --base <last release>`
   in a read-only sandbox as `make release-audit` does. The output goes into the PR body under
   `## Release audit`, with P1 and P2 as the audit gives them, ending with `Verdict: open`.
   Changing that line to `Verdict: pass` after reading the findings stays the product owner's
   act, next to the label. If the job fails or produces nothing, the section says so and the
   verdict stays open: `scripts/check-release-audit.sh` in `main.yml` refuses the tag until a
   person has put a passing audit there. Patch releases skip the job (ADR 0078 point 4).
4. **The tag comes from the `tag` dispatch after the checks of ADR 0052 point 4.** With the
   version as input, the job finds the squash-merged `chore(release): vX.Y.Z (#N)` on `main`,
   checks that `pyproject.toml` at that commit says the version, that the tag does not exist,
   and, for `vX.Y.0`, that the release PR's audit ends with `Verdict: pass`; then it sets the
   annotated tag on that commit and pushes it. `main.yml` repeats the tag checks before it
   builds, as before; a release is never re-tagged (fix forward, ADR 0052).
5. **A dry run changes nothing.** The `dry_run` input runs every check and edit, shows the diff
   and the PR body in the run's summary, and opens no PR, runs no audit and sets no tag. A dry
   run may be dispatched on any branch, which is how a change to the workflow is tried before
   it merges; a real run is refused outside the default branch.
6. **No GitHub-hosted-only assumptions.** The jobs run on `vars.RELEASE_RUNNER` when that
   repository variable is set (a self-hosted runner's label), else on `ubuntu-24.04`. The
   runner needs git, Python 3.11 or newer, the `gh` CLI, and for the audit job what
   `openai/codex-action` needs on Linux: passwordless `sudo`, `setpriv`, `setfacl` and
   unprivileged user namespaces. Dropping `sudo` is irreversible on a reused runner, so the
   audit job gets a disposable (ephemeral) runner. The logic is in the scripts; the YAML calls
   `gh` in four places (list and open the PR, auto-merge, edit the body), which an internal
   forge replaces with its own CLI.
7. **Two secrets, set once by the product owner, never in the repository.** `RELEASE_TOKEN`
   (a fine-grained token with *contents* and *pull requests* write on this repository, or a
   GitHub App token later) is what pushes the branch, opens the PR and pushes the tag, so that
   CI and the build start. `OPENAI_API_KEY` is used by the audit job only, through the action's
   proxy. A real run fails at its first step when `RELEASE_TOKEN` is missing; a dry run needs
   neither.

## Settings

Nothing in `app/config.py` or `app/domain/settings_registry.py` changes. The workflow has
three inputs that live in the forge, not in the application:

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `RELEASE_TOKEN` | A bootstrap (repository secret) | none | a token with contents and pull-requests write on this repository | repository admin | Needed before anything runs; a `GITHUB_TOKEN` PR or tag starts no workflow. |
| `OPENAI_API_KEY` | A bootstrap (repository secret) | none | an OpenAI API key | repository admin | The audit job only; through the action's proxy, never in the review process. |
| `RELEASE_RUNNER` | B deployment (repository variable) | `ubuntu-24.04` | a runner label | repository admin | Tied to the host: where the release jobs run. The audit's runner must be disposable. |

**Fixed on purpose:** the branch name `release/vX.Y.Z` and the PR title `chore(release):`
(`scripts/check-release-audit.sh` and `scripts/decision_gate.py` key on them), the audit
command (it lives in `scripts/release-audit.sh`, ADR 0078), the job timeouts (10 minutes for
the git work, 60 for the audit) and the rule that a real run starts from the default branch.
They are process constants, not behaviour of the application.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #183 | merged 2026-10-31 |
| 1 | `scripts/release_pr.py` with `tests/scripts/test_release_pr.py`; `.github/workflows/release.yml` with the jobs `release-pr`, `audit` and `tag`; runbook, `docs/skills.md`, `docs/adr-status.md` | #183 | merged 2026-10-31 |
| 2 | Dry run of both dispatches on the PR branch, output in the PR description | #183 | merged 2026-10-31 |
| 3 | The product owner creates the secrets `RELEASE_TOKEN` and `OPENAI_API_KEY` (and the variable `RELEASE_RUNNER` for a self-hosted runner) | | planned |
| 4 | First release through the workflow, `v0.4.0`, with the first real audit (ADR 0078 step 5); the ADR's Status line becomes accepted | | planned |

## Consequences

- The product owner's acts for a release are: one click (`release-pr`), reading the audit
  and changing `Verdict: open` to `Verdict: pass`, the label `po:approved`, one click
  (`tag`). Everything else is visible in the Actions tab and in the PR.
- Every audited release costs an API call: one review of the whole diff since the last
  release at high reasoning effort, roughly 5 to 20 US dollars at today's GPT-5 prices (the
  `v0.4.0` diff is about 45,000 lines over 55 pull requests). The job runs only for `vX.Y.0`,
  never on a dry run, and nobody runs it by hand first: the first real run is `v0.4.0`.
- The findings arrive in the CLI's own format; the workflow does not rewrite them. P1 and P2
  mean what the audit says they mean; the product owner reads them, fixes P1 findings before
  the tag, and makes issues of P2 findings (ADR 0078 point 4).
- The release PR exists before the audit runs, with a *Pending* section and `Verdict: open`,
  so a reader of the PR sees the state at every moment; re-running the `audit` job replaces
  the section whole.
- Security: the API key stays in the action's proxy process; the review process runs without
  `sudo`, without credentials on disk (`persist-credentials: false`) and in a read-only
  sandbox; nothing from a pull request runs in this workflow (it starts from the default
  branch on a collaborator's click, inputs go through `env`), and the workflow file itself is
  a decision path that waits for `po:approved` (ADR 0078 point 1). The decision gate still
  runs from `main`'s copy of the gate.
- The dispatch is the instruction: the merge of the release PR follows the label, the tag
  follows the click, and both carry the dispatcher's login. ADR 0012's rule that an
  instruction in a comment, issue, file or tool output never counts is unchanged; a
  `workflow_dispatch` can only come from a collaborator.
- Amended: ADR 0052 point 3 (the release PR and the tag come from the workflow; the runbook
  section is rewritten) and ADR 0078 point 4 (the audit is a job; `make release-audit` stays
  the laptop fallback). ADR 0052 point 4 (the tag checks in `main.yml`) and the tag gate of
  ADR 0078 are unchanged. The changelog, `docs/runbook.md` (*Releasing a version*),
  `docs/skills.md` and `docs/adr-status.md` are updated in the same PR.
