# 0088. The decision gate judges the merge group too

Status: proposed (2026-10-31), requested by the product owner. Amends ADR 0078 (point 3: where
the check *decision gate* runs and what it reads). Partly built; see *Build status*.

## Context

ADR 0078 point 3 makes the required check *decision gate* the mechanism that keeps a decision
out of `main` until the product owner sets the label `po:approved`. The check ran on
`pull_request_target` only. `merge_group` was listed in the workflow so that the check would
*report* in the merge queue, and the job was skipped there on purpose: GitHub counts a skipped
required check as passed, and the queue has no pull request object and no label to read.

That left a hole, and on 2026-10-31 a decision went through it. PR #266 built step 1 of
ADR 0085 and was stacked on the branch of PR #263, the ADR itself, which carried `po:approved`.
When #263 was closed, #266's diff against `main` held the new file `docs/adr/0085-…md`. The
gate on the pull request said so and failed (22:02). Auto-merge still put the pull request in
the queue (22:11), the gate's `merge_group` run was skipped, which passed, and the ADR landed
on `main` through a build pull request that nobody had labelled (22:21).

Two things made that possible, and this ADR closes one of them:

- **The check in the queue was a skip, not a judgement.** Whatever the state of the check on
  the pull request (failed, stale after a change of base, or simply not required yet), the
  queue's own run of the required check is what GitHub consults last before the fast-forward
  to `main`. It said nothing.
- **The check is not yet required.** ADR 0078 step 3 (the product owner adds *decision gate*
  to the required checks of `main`) is still planned; the branch protection of `main` lists
  eight required checks and *decision gate* is not among them. A check that is not required
  cannot stop auto-merge, however red it is. That is a GitHub setting, not code; it stays
  with the product owner and is repeated in this ADR's *Build status* so it is not forgotten
  again.

What the queue offers: the `merge_group` event carries `base_sha` (the tip of `main` the group
is built on), `head_sha` (the tip of the queue branch) and `head_commit`, but no pull request
object. The queue branch holds one commit per pull request in the group, in order: the squash
commit that will land on `main`, whose subject ends in `(#N)`. The `GITHUB_TOKEN` of the run
may read the pull requests' labels through the API when the workflow asks for
`pull-requests: read`.

## Decision

1. **The gate runs in the merge queue, and judges there.** `.github/workflows/decision-gate.yml`
   runs `scripts/decision_gate.py` on `merge_group` as well as on `pull_request_target`. On
   `merge_group` the job checks out the group's base (`main` at `base_sha`), fetches the queue
   branch's head as git objects only, lists the pull requests in the group from the first-parent
   commits between base and head (`--list-pull-requests`: the number from `(#N)` at the end of
   a squash commit's subject, or from `Merge pull request #N` of a merge commit), reads each
   pull request's labels through the API with the run's read-only token, and then judges every
   pull request **by its own commit against its parent** (`--merge-group --pull N=labels`).
   The labels of one pull request never cover another in the same group: an approved decision
   batched with an unapproved one fails the group, and the queue sends the second one back.
2. **The gate fails closed.** A commit on the queue branch that names no pull request, or a
   pull request whose labels could not be read, fails the check. Build work is unaffected: a
   group of build pull requests passes without any label, as before.
3. **The check on the pull request stays, as the early signal.** `pull_request_target` keeps
   running from `main`'s copy of the gate on every change of the pull request, now also on
   `edited` (a change of the base branch changes the diff, which is how a stacked pull request
   becomes a decision when its base is closed). Its red is what tells the product owner that a
   label is wanted; the queue's run is what refuses the merge.
4. **The script stays the one place of the logic.** The pull request numbers, the per-commit
   judgement and the label rule are in `scripts/decision_gate.py` with their tests in
   `tests/scripts/test_decision_gate.py`; the workflow only passes shas and labels. A test
   also reads the workflow and fails when the job is skipped on `merge_group` again, when
   `--merge-group` is gone, or when the workflow no longer asks for `pull-requests: read`.
5. **Unchanged:** what makes a pull request a decision (ADR 0078 point 1), the label and who
   sets it (point 3), auto-merge on every pull request (point 2), the release audit (point 4).
   The workflow file that runs on `merge_group` comes from the queue branch, as GitHub has it;
   a pull request that changes that workflow is a decision on the pull request (point 1), and
   once *decision gate* is required it cannot be queued without the label. Both halves are
   needed: this ADR's code and ADR 0078 step 3's setting.

## Settings

None. This decision changes one workflow, one script and its tests; nothing in `app/config.py`
or `app/domain/settings_registry.py` changes.

**Fixed on purpose:** the two subject shapes that name a pull request (`(#N)` and
`Merge pull request #N`) are GitHub's, not ours; the gate recognises both and refuses anything
else rather than guessing.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #278 | in PR |
| 1 | `--list-pull-requests` and `--merge-group` in `scripts/decision_gate.py`, the `merge_group` job in `decision-gate.yml` with `pull-requests: read`, `edited` on `pull_request_target`, tests, ADR 0078's table, CONTRIBUTING.md and the runbook | #278 | in PR |
| 2 | The product owner adds *decision gate* to the required checks of `main` (ADR 0078 step 3; without it neither run stops a merge) | | planned |

## Consequences

- A decision cannot reach `main` through the queue without the label, whatever the state of
  the check on the pull request, once the check is required. The incident of #266 would have
  failed in the queue with the ADR file named in the job's output.
- The queue's run reads labels through the API, so the job holds the run's `GITHUB_TOKEN`
  with `pull-requests: read` and `contents: read`. It still executes nothing from the queue
  branch: the script comes from the checked-out base, and the queued commits are fetched as
  objects and diffed. The workflow file itself is the one limit, stated under point 5.
- Each pull request in a group is judged by its own squash commit, not by the group's diff
  against `main`, so a build pull request queued together with an approved decision passes,
  and the output names the pull request and the file for every refusal.
- A change of the base branch of a pull request now reruns the gate (`edited`); the cost is
  one more five-minute job on a title edit.
- ADR 0078 gets a row for this amendment in its *Build status* table and a note in its
  Status line; its decision text is not rewritten (ADRs are never rewritten).
