# 0104. The approval label counts only from an administrator, and a foreign push takes it back

Status: proposed (2026-11-02), requested by the product owner. Amends ADR 0078 (point 3: what
makes `po:approved` an approval) and ADR 0088 (the merge-group run reads the same evidence).

## Context

ADR 0078 lets build work merge on green and holds a decision until the product owner sets the
label `po:approved`. ADR 0088 runs the same gate in the merge queue. On 2026-11-02 the
repository moved from classic branch protection to a ruleset on `main` with no bypass, not
even for administrators, and *decision gate* became a required check (ADR 0078 step 3,
ADR 0088 step 2). From that moment the label is the only thing between a decision and `main`.

The gate checks only that the label is present. Two gaps remain:

1. Anyone with triage or write access can set a label. The repository has a collaborator with
   write access besides the product owner, and the Renovate app opens pull requests; either
   could approve a decision by labelling it.
2. The label survives a push. A decision the product owner approved can change after the
   approval, by anyone who can push to the branch, and still merge as approved.

The obvious GitHub answer, a required review from a code owner, does not fit: Claude Code
opens pull requests under the product owner's account (ADR 0012), and GitHub never lets the
author of a pull request approve it. A required code-owner review would stop every decision
for good.

## Decision

1. The gate counts `po:approved` as an approval only when the latest `labeled` or `unlabeled`
   event for that label on the pull request is `labeled`, by a user who is an administrator of
   the repository at the time of the check. Any other labeller, or a label that was removed
   last, counts as no approval; the output names who set it.
2. The workflow reads the label events (`GET /repos/{repo}/issues/{n}/events`) and the
   administrators (`GET /repos/{repo}/collaborators`, `permissions.admin`) through the API with
   the job's own token; `scripts/decision_gate.py` decides from those two files and runs no
   network call itself. A missing or unreadable file fails the check (fail closed).
3. A push to a pull request (`synchronize`) by a user who is not an administrator takes the
   label off, in the same run, and that run treats the pull request as not approved. A push by
   an administrator, which includes Claude Code working under the product owner's account and
   a merge of `main` into the branch, keeps the approval.
4. The merge-queue run (ADR 0088) applies point 1 to every pull request in the group, with the
   events of that pull request.
5. A label set right before the run may not yet appear in the events; on a `labeled` event for
   `po:approved` the workflow reads the events again, at most five times, three seconds apart.
6. No required code-owner review while pull requests are opened under the product owner's own
   account. When Claude Code gets its own identity (a GitHub App of the organisation), a
   follow-up ADR may replace the label by a code-owner review.

## Settings

None. The label name, the administrator rule and the retry count are part of the gate, whose
every change is a decision (ADR 0078, *Settings*); they are not tunable on purpose.

**Fixed on purpose:** five retries three seconds apart: long enough for GitHub's event
listing to catch up, short enough that a real absence fails within the job's timeout.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #NNN | in PR |
| 1 | `approval()` and `--events`, `--admins`, `--withdrawn`, `--pull N=FILE` in `scripts/decision_gate.py`; the workflow reads events and administrators and takes the label off after a foreign push; tests; ADR 0078 and 0088 tables, CONTRIBUTING.md, threat model | #NNN | in PR |

## Consequences

- A decision cannot be approved by a collaborator who is not an administrator, nor by an app.
- The product owner approves again after someone else pushes to an approved decision; pushes
  by Claude Code under the product owner's account do not need a new approval, so the trust
  boundary is the account, not the person behind it.
- The `decision gate` job needs `pull-requests: write` to take the label off. It still runs
  only the base's code (`pull_request_target`), so a pull request cannot use that right.
- Residual risk: an administrator account remains a single point of trust; protecting it
  (two-factor authentication for every member, to be required at organisation level) is
  outside this ADR.
