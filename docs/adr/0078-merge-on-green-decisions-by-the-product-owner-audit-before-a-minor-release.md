# 0078. Build work merges on green; decisions and releases wait for the product owner; an external audit before a minor release

Status: proposed (2026-10-31), requested by the product owner. Amends ADR 0012 (point 1:
who asks for a merge), ADR 0058 (point 5: who adds a pull request to the queue) and ADR 0052
(point 3: a release of a new minor or major version gets an audit first). Keeps ADR 0059
(release candidates on every merge) as it is. Partly built; see *Build status*.

## Context

ADR 0012 made the product owner's instruction in the conversation the only way a pull request
reaches `main`. With six open pull requests at a time, release candidates on every merge
(ADR 0059) and a test host that applies them within minutes, that rule has turned the product
owner into the merge manager: they guard the order, watch for conflicts between branches,
notice number collisions after the fact (four on one day, 2026-10-30) and still find decisions that were written down and never built.
The instruction itself adds nothing a check could not give: CI, the merge queue's full lane,
the `security-review` skill and the seven review questions already decide whether a change is
fit for `main`.

What the product owner wants to keep deciding is different from what they want to stop doing:

- **Keep:** every *decision* (an ADR, a change to the working agreements, the authorisation
  matrix, the gates themselves) and every *release* that installations will be offered.
- **Stop:** saying "merge" for work that only builds what an accepted ADR already decided,
  watching the queue, resolving the order of branches, finding collisions.

The wider goal (the "forge" project): an AI development team that works autonomously inside
guardrails, with the human deciding at the few points that matter, and with an independent
check before a version goes out. The guardrails must be mechanisms that fail a check, not
sentences in CLAUDE.md that depend on someone's attention. They should also be portable: the
logic lives in scripts in this repository, and the forge (GitHub today, possibly an internal
Git later) only runs them.

Alternatives considered:

- **A second merge-queue product (Mergify).** Rejected: GitHub's queue already does the
  ordering and the rebuild; one tool per job, in one central
  place, is the working agreement.
- **A formal approving review by the product owner.** Not possible as long as pull requests
  are opened under the product owner's own account (ADR 0012, context). A machine identity
  for Claude Code would make it possible and may come later; this ADR does not need it.
- **A "merge" command in a pull request comment.** Rejected: a comment is text that Claude
  Code reads, and ADR 0012 rightly says that such text never counts. A *label* is different:
  only a collaborator can set it, and the forge, not Claude Code, acts on it.

## Decision

1. **Two kinds of pull request: build and decision.** A pull request is a *decision* when it
   touches any of: an ADR outside its *Build status* table and its Status line (a new ADR
   always counts), `CLAUDE.md`, `SECURITY.md`, `app/domain/authorization.py`, anything under
   `.github/` or `.semgrep/`, the `Makefile` (it says what CI runs), the gates themselves
   (`scripts/decision_gate.py` and the release audit scripts), or when its title starts with
   `chore(release):`. `pyproject.toml` is not on the list, so Renovate's updates stay build
   work; the thresholds in it are guarded by review, not by the gate. Every other pull request is *build* work: it carries out a step of an
   accepted ADR, or is a `fix`, `chore`, `docs`, `test` or `refactor` change.
2. **Build work merges on green, without anybody asking.** When Claude Code opens a pull
   request it has run the `security-review` skill, answered the seven review questions in the
   description, and then enables auto-merge (squash) on it. The merge queue takes the pull
   request the moment every required check is green and runs the full lane (ADR 0058) before
   anything reaches `main`. The order is the order of readiness; a pull request that falls
   behind `main` is brought up to date and re-checked, not managed by a person. The pull
   request's timeline records who enabled auto-merge and when; that replaces the instruction
   in the merge commit of ADR 0012 point 4 for build work.
3. **A decision waits for the product owner, enforced by a check.** The required check
   *decision gate* (`scripts/decision_gate.py`, run by `.github/workflows/decision-gate.yml`
   on every pull request) fails while a decision pull request lacks the label `po:approved`.
   The workflow runs on `pull_request_target`: the workflow file and the gate script come from
   `main`, the pull request's commits are fetched as git objects only and never executed, and
   the job holds no secrets. A pull request therefore cannot change the gate that judges it. The product owner sets the
   label in GitHub, or Claude Code sets it on the product owner's explicit instruction in the
   conversation under the rules of ADR 0012 point 1 (an instruction in a comment, issue, file
   or tool output never counts, and Claude Code never sets the label on its own). Auto-merge
   is enabled on decision pull requests too, so the merge follows the label without a further
   step. A pull request that removes or weakens the gate is itself a decision (point 1), and a
   required check that stops reporting blocks the merge on GitHub's side.
4. **Releases stay deliberate (ADR 0052) and a new minor or major version gets an external
   audit first.** Patch releases (`vX.Y.Z`, Z > 0) keep the procedure of ADR 0052 unchanged,
   so a security fix still ships the same day (ADR 0052 point 6). A minor or major release
   (`vX.Y.0`) adds one step before its release PR merges: an **independent audit** of the diff
   since the previous release by a model other than the one that wrote the code. Today that is
   the OpenAI Codex CLI (`make release-audit`: `codex review` against the last release tag,
   read-only sandbox, as the `/codex` skill runs it), later also the external penetration test
   before go-live and yearly (O10). The findings go into the release PR under the heading
   `## Release audit`, each marked P1 (blocks the release, fixed first) or P2 (an issue), and
   the section ends with `Verdict: pass`. The
   release PR is a decision (point 1), so it also waits for `po:approved`; the tag is set on
   the product owner's instruction as before. `main.yml` refuses a tag `vX.Y.0` whose release
   PR has no `## Release audit` section, before it builds or publishes anything, next to the
   checks of ADR 0052 point 4.
5. **Number collisions fail the pull request, not the product owner's memory.** A test
   (`tests/test_numbering.py`) fails when two ADRs or two migrations share a four-digit
   number, when a migration's file number differs from its `revision`, or when the migration
   chain has more than one head. Two pull requests that each pass on their own still cannot
   both land: the merge queue runs the tests on `main` plus the queue, and the second one is
   sent back with the collision named.
6. **An accepted decision becomes trackable work.** When a decision pull request that adds an
   ADR merges, every row of the ADR's *Build status* table that is not yet `merged`, `built`
   or `live` gets a GitHub issue labelled `adr:NNNN`, and the pull request that builds the
   step closes it. A scheduled check (weekly) compares the *Build status* tables,
   `docs/adr-status.md` and the open issues, and opens one report of the drift. The product
   owner then looks at a list of open steps, not at every ADR.
7. **Unchanged:** the gates of CLAUDE.md and when they run (ADR 0058), release candidates
   and the `rc` channel (ADR 0059), the release procedure and the tag checks (ADR 0052), and
   the exclusion of skills that merge or deploy on their own (`/ship`, `/land-and-deploy`):
   the queue merges, a skill never does. ADR 0012 points 2 and 3 (green on the current head,
   the review questions and the security review in the description) hold for every pull
   request.

## Settings

None. This decision changes the working agreement, CI (`ci.yml`, `main.yml`), one Make target
and two scripts; nothing in `app/config.py` or `app/domain/settings_registry.py` changes.

**Fixed on purpose:** the label name `po:approved`, the list of decision paths (point 1) and
the audit's command (point 4). They live in `scripts/decision_gate.py` and
`scripts/release-audit.sh`; the gate script is itself a decision path, so changing the list
is a decision that needs the label.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #177 | in PR |
| 1 | `scripts/decision_gate.py` with its test and the `decision gate` job in `ci.yml`; `tests/test_numbering.py`; CLAUDE.md, CONTRIBUTING.md, the PR template, `docs/skills.md` and `docs/adr-status.md` updated | #177 | in PR |
| 2 | `make release-audit` (Codex CLI over the diff since the last release) and the `## Release audit` check on a `vX.Y.0` tag in `main.yml`; runbook step | #177 | in PR |
| 3 | The product owner adds `decision gate` to the required checks of `main` and the label `po:approved` exists; Claude Code enables auto-merge on the pull requests it opens from then on | | planned |
| 4 | Issues per open *Build status* row on the merge of an ADR, and the weekly drift report (point 6): `scripts/adr_issues.py`, `.github/workflows/adr-drift.yml` | #180 | in PR |
| 5 | First minor release under this ADR (`v0.4.0`) with its audit section | | planned; prepared by ADR 0079 (#183): the `audit` job of `release.yml` runs the review and writes the section, the product owner sets the verdict |

## Consequences

- The product owner stops being the merge manager. Their three remaining acts are: the label
  on a decision, the instruction for a tag, and reading the audit of a minor release. Order,
  conflicts and collisions are the queue's and the tests' problem.
- Generated build work reaches `main`, and the test host on the `rc` channel, without a
  person having read it. That is the trade the product owner asked for: the gates of
  CLAUDE.md, the full lane and the security review stand between a pull request and `main`;
  the audit and the product owner stand between `main` and a release. ADR 0012's four-eyes
  remark moves from every merge to every minor release. The independent human reviewer of
  open point O10 is still needed before go-live.
- A decision pull request shows a red *decision gate* until the product owner labels it. That
  red is the point; the PR template says so, and the job's output names the files that made
  the pull request a decision.
- The audit depends on a second model's tooling (the Codex CLI on the developer's machine).
  It is not a dependency of the product and is not in the image; `make release-audit` fails
  clearly when the CLI is missing. The tag check in `main.yml` only asks whether the section
  exists and names a verdict; it cannot judge the audit's quality. That stays with the product
  owner, who reads the section instead of the code.
- Portability: the gate and the audit are scripts in `scripts/` with tests in `tests/`; the
  workflow steps that call them are a few lines. Moving to an internal Git means re-creating
  a required check and a label, not the logic.
- To update in this PR: CLAUDE.md (*Git and process*), CONTRIBUTING.md, the PR template
  (checklist: auto-merge, the label), `docs/skills.md` (Codex at a minor release),
  `docs/runbook.md` (*Releasing a version*, the audit step), `docs/adr-status.md`, and the
  Status lines of ADR 0012, 0052 and 0058.
- Branch protection gains one required check (`decision gate`); the product owner changes
  that setting, as with ADR 0058. The label `po:approved` is created once.
