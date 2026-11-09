# 0120. `pull_request_target` only for the decision gate, allowed by an event policy; the backport starts from the merge

Status: accepted (2026-11-09, `po:approved` on #699), requested by the product owner. Partly
built: step 1 (the backport on `push`, the event policy in the guardrail baseline, the audit of
gate runs that GitHub did not start, the runbook) is in the pull request that adds this ADR;
step 2, the product owner's setting in GitHub, is due before 2026-12-02. Keeps ADR 0078
point 3, ADR 0088 and ADR 0104 as they are (the gate still runs from the base); extends
ADR 0109 (the baseline records the event policy); amends ADR 0116 point 3 (a `backport:X.Y`
label set after the merge needs a dispatch).

## Context

On 2026-11-02 GitHub announced that from **2 December 2026** GitHub Actions blocks the event
`pull_request_target` by default in every public repository, "unless an applicable Actions
event policy explicitly allows them" (GitHub changelog of 2026-11-02; the documentation page
*Securely using pull_request_target*, section *Default policy*). Until then the default runs in
evaluate mode, except for two brownouts on 9 and 16 November, 16:00–18:00 UTC. Private and
internal repositories are not affected. This repository is public since v0.4.0.

On 9 November from 16:04 UTC every `pull_request_target` run ended in `startup_failure`
without a job: the required check *decision gate* and the `backport` workflow, on every
branch. `gh run view` only says the run "likely failed because of a workflow file issue"; the
reason is the annotation on the run page, which names the brownout and asks an administrator
to allow the event in an Actions event policy. `merge_group`, `pull_request` and `push` runs
were not affected. GitHub does not re-run such a run: `gh run rerun` and *re-run failed jobs*
both answer "This workflow run cannot be retried" (HTTP 403, tried on run 42666968866 at
18:02 UTC, after the window). Gate runs started by new events after 18:00 ran normally.
After 2 December the gate would never report, and nothing could merge into `main` or a
maintenance line: both rulesets require *decision gate*.

Two workflows use the event:

- `decision-gate.yml` (ADR 0078 point 3, ADR 0088, ADR 0104). It runs on `pull_request_target`
  so that its workflow file and its script come from the base, never from the pull request it
  judges. In the merge queue GitHub takes the workflow from the group's head, which contains
  the pull request (ADR 0104 point 7). So the run on the pull request is the only run that a
  pull request cannot change.
- `backport.yml` (ADR 0116 point 3). It runs on `pull_request_target` (`closed`, `labeled`)
  for a merged pull request with a `backport:X.Y` label, and holds the release app's key
  (environment `release`). No maintenance line exists today.

What an event policy is, from GitHub's documentation and API, read on 2026-11-09:

- *Workflow execution protections* (generally available since 2026-09-17) are *Actions
  policies* at enterprise, organisation and repository level, under *Settings → Actions →
  Policies*, and through the REST API `…/actions/policies`. A policy has a name, an
  enforcement status (`active`, `evaluate` or `disabled`; *Evaluate* is for Enterprise Cloud
  only, and this organisation is on GitHub Team), conditions and rules. At repository level
  the one condition is `workflow_path`, a list of file paths or patterns to `include` and to
  `exclude`: this is *workflow file targeting*. The rules are `restrict_action_events` (a list
  of `allowed_events`, among them `pull_request_target` and `merge_group`) and
  `restrict_actions_actors`.
- Organisation owners and repository administrators create them. GitHub's permission tables
  list every call of the policy API, reading included, under *Administration: write* for a
  GitHub App and a fine-grained token; reading the Actions permissions themselves needs only
  *read*. An administrator's own `gh` login reads the policies.
- On 2026-11-09 the repository has none. `GET /repos/3Corners-Labs/OpenPlanner/actions/policies`,
  which includes the policies of the organisation and the enterprise that apply to the
  repository, returns `total_count: 0`.

Options weighed:

- **(a) An event policy** that allows `pull_request_target` for exactly the workflow files
  that need it. The gate stays as built; the price is a setting outside the code that must be
  watched.
- **(b) The pull-request side of the gate off `pull_request_target`.**
  - `pull_request` runs the pull request's own copy of the workflow: a pull request could
    change or drop the gate that judges it, and the queue's run (also the pull request's
    copy) would not stop that. Rejected: it removes the reason the gate exists.
  - An unprivileged `pull_request` workflow plus a `workflow_run` job from `main` that writes
    the *decision gate* result through the Checks API. The judge comes from `main` again, but
    `workflow_run` is the other privileged trigger of GitHub's own warnings, its payload names
    no pull request for a fork's pull request (the job has to find it), and it takes a second
    workflow, check-run bookkeeping and a token with write rights that runs after a workflow
    the pull request controls. Rejected: more moving parts for the same trust, and nothing
    says GitHub will not restrict `workflow_run` next.
  - A scheduled sweep that sets the result on every open pull request. It runs `main`'s copy,
    but answers minutes late (GitHub delays schedules under load) and adds a second kind of
    *decision gate* result. Rejected.
  - The merge queue alone. Its run takes the workflow from the pull request (ADR 0104
    point 7). Rejected for the same reason as `pull_request`.
- **(c) The backport on `push` to `main`** (or on `workflow_run`) instead of `closed`. The
  merge is a push to `main`; the pull request's labels can be read through the API.

## Decision

1. **The gate keeps `pull_request_target`, allowed for its own workflow file only (option a).**
   One Actions event policy at repository level, named `decision gate`, enforcement `active`,
   targets exactly `.github/workflows/decision-gate.yml` (`include` that one path, `exclude`
   empty) and allows exactly the events that file uses: `pull_request_target` and
   `merge_group`. An event rule lists the events allowed for the files it targets, so the
   queue's event is on the list: without it the policy would stop the gate in the merge queue
   (ADR 0088). No actor rule: whose label counts is the gate's own judgement (ADR 0104). The
   policy sits at repository level, the narrowest place, where the repository administrator
   sets it. The gate's workflow and script do not change.
2. **The backport leaves `pull_request_target` (option c).** `backport.yml` starts from the
   `push` to `main` that a merge makes, and from `workflow_dispatch` with a pull request's
   number. A first job without secrets lists the pull requests of the push with the decision
   gate's own walk (`decision_gate.py --list-pull-requests` over the first-parent commits; a
   commit that names none fails the run, as in the gate), and `scripts/release_line.py
   backports` reads each pull request once and lists one cherry-pick per line its labels name
   (`backport:X.Y`, the rule of ADR 0116), for a pull request merged into `main`. Only then
   does the job with the release app's key run, once per entry (a matrix, so one failing
   backport stops no other), taking number, merge commit, title and line from the entry
   without reading anything again.
   A label set after the merge no longer starts the workflow: whoever sets it runs
   `gh workflow run backport.yml -f pull_request=N`. So the policy covers one file, and the
   workflow that holds the release app's key no longer starts from an event of a pull request.
   `workflow_run` was not chosen: it would wait for another workflow and is a privileged
   trigger itself.
3. **Only the gate uses `pull_request_target`.** A test fails when another workflow file uses
   the event, and when the events of `decision-gate.yml` differ from the events the baseline's
   policy allows. While that holds, a wider policy changes nothing, and a new trigger on the
   gate changes the baseline too; both are decisions (ADR 0078 point 1).
4. **The baseline records the policy.** `.github/guardrails.json` gets the section
   `actions_policies` with the policy of point 1 (where it is set, enforcement, conditions,
   rules). `scripts/guardrail-fetch.sh` reads `GET /repos/{repo}/actions/policies`, which
   includes the policies of the organisation and the enterprise that apply to the repository,
   and `scripts/guardrail_audit.py` compares it like any other section: a new, changed or
   removed policy is drift.
5. **The audit app stays read-only.** It cannot read Actions policies (point 4 needs
   *Administration: write*), and it keeps *read* (ADR 0109 point 1): a key that can change the
   rulesets is a greater risk than the hourly read is worth. The baseline lists
   `actions_policies` under `known_unseen`. When the app's read is refused, the report shows
   the section under *Not checked, by design* instead of keeping the issue open; a file that is
   there but cannot be parsed is unreadable, never unseen by design. An administrator's own
   login reads and compares it with `--full`, which lets nothing count as unseen (runbook, and
   the release checklist). When the app can read it, because GitHub changes the permission,
   the hourly run compares it.
6. **The hourly audit notices a gate that GitHub did not start.** Every run of
   `decision-gate.yml` since the day before that ended in `startup_failure` is a finding of
   its own, read with the workflow's own token (`actions: read`; the audit app has no Actions
   permission). It reads the latest 100 runs, one call an hour: on a busy day that is a few
   hours, enough to show a refusal within the hour, since a missing policy refuses every new
   run; older refusals can leave the report before the day is over. That shows the change that matters, a policy that was removed, switched off,
   narrowed or given an actor rule, within the hour from 2 December, and during the brownout
   of 16 November if step 2 is not done by then. It also shows a gate file in the queue that
   GitHub cannot start.
7. **A run that GitHub never started is replaced, not re-run.** The runbook says how to read
   the cause from the annotation and how to start the gate again: a new event on the pull
   request once the cause is gone (an edit of its description, or the next push), and in the
   merge queue, queueing the pull request again.
8. **The product owner's step (step 2).** It can be done before or after this ADR merges; until
   both are done, the gap shows (the issue for step 2, ADR 0078 point 6). In this order:
   1. Create the policy, with the API from a shell where `gh` is logged in as an administrator:

      ```bash
      gh api -X POST repos/3Corners-Labs/OpenPlanner/actions/policies \
        -H "X-GitHub-Api-Version: 2026-03-10" --input - <<'JSON'
      {
        "name": "decision gate",
        "enforcement": "active",
        "conditions": {
          "workflow_path": {"include": [".github/workflows/decision-gate.yml"], "exclude": []}
        },
        "rules": [
          {"type": "restrict_action_events",
           "parameters": {"allowed_events": ["pull_request_target", "merge_group"]}}
        ]
      }
      JSON
      ```

      Or in the browser: the repository's *Settings* → in the left sidebar under *Actions*,
      *Policies* → create a new policy → name `decision gate` → enforcement status *Active*
      → target the workflow file `.github/workflows/decision-gate.yml` and nothing else → add
      the rule *Restrict events* with exactly `pull_request_target` and `merge_group` → no
      *Restrict actors*, no *Require lockfile* → save.
   2. Read it back. The answer must show exactly this one policy, with `source_type`
      `Repository`:

      ```bash
      gh api repos/3Corners-Labs/OpenPlanner/actions/policies \
        --jq '.policies[] | {id, name, source_type, enforcement, conditions, rules}'
      ```

   3. Check that the gate still starts on both sides; the policy is active at once, so a wrong
      list of events shows on the next run. The next event on any open pull request and the
      next merge-queue entry each give a `decision gate` run with jobs, not `startup_failure`:

      ```bash
      gh run list --repo 3Corners-Labs/OpenPlanner --workflow decision-gate.yml --limit 10 \
        --json databaseId,event,conclusion,createdAt
      ```

      If either ends in `startup_failure`, delete the policy
      (`gh api -X DELETE repos/3Corners-Labs/OpenPlanner/actions/policies/<id>`), which
      returns to today's state until 2 December, and report it.
   4. Compare with the baseline, with the administrator's own login: once this ADR has merged,
      `scripts/guardrail-fetch.sh "$TMPDIR/live" 3Corners-Labs/OpenPlanner` and
      `python3 -m scripts.guardrail_audit --live "$TMPDIR/live" --full` report nothing about
      `actions_policies`. (Before step 2 this read shows the policy as missing, and the eight
      runs that the brownout of 9 November refused, as it did on 2026-11-09.)
   5. During the brownout of 16 November, 16:00–18:00 UTC, gate runs on pull requests start as
      usual: the default no longer applies to the gate. Step 2's row then becomes `built` with
      that date.

## Settings

None. This decision changes two workflows, the guardrail baseline, the audit scripts and the
runbook; nothing in `app/config.py` or `app/domain/settings_registry.py` changes.

**Fixed on purpose:** the policy's name, its single target and its two events (they follow the
gate's workflow file, and a test ties them together); the window of point 6, since the day
before, as for the bypasses of ADR 0109, read from the latest 100 runs. They live in
`.github/guardrails.json`, the fetch and the audit script, and changing them is a decision
(ADR 0078).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #699 | merged |
| 1 | `backport.yml` on `push` to `main` and `workflow_dispatch`, with a job without secrets that lists the pushed pull requests with `decision_gate.py --list-pull-requests` and runs `scripts/release_line.py backports`, and the job with the key as a matrix of its entries (point 2; the subcommand `backport-lines` is gone); `actions_policies` in `.github/guardrails.json` and `known_unseen`, its read in `scripts/guardrail-fetch.sh` and its comparison in `scripts/guardrail_audit.py` with `--full` for an administrator (points 4 and 5); the gate's runs that GitHub did not start as a finding, read with the workflow's token (`guardrail-audit.yml` with `actions: read`, point 6); tests for points 2 to 6; runbook (*The decision gate did not start*, *Guardrail drift*, *Maintenance lines*), CONTRIBUTING.md, release checklist, threat model, the Status lines of ADR 0109 and 0116 | #699 | merged |
| 2 | The product owner creates the event policy `decision gate`, reads it back and checks that the gate starts (point 8); confirmed by the brownout of 2026-11-16 | GitHub setting | planned |

## Consequences

- After 2 December pull requests keep merging, provided step 2 is done: the gate is the one
  workflow that GitHub lets run on `pull_request_target`, and a test keeps it the only one
  that uses it.
- The exception is a setting, not code. Removing or narrowing it stops every merge, a failure
  that is loud and closed; widening it changes nothing while no other workflow uses the
  event. The hourly audit sees the first and not the setting itself; an administrator's own
  audit sees both.
- A `backport:X.Y` label set after the merge needs `gh workflow run backport.yml -f
  pull_request=N`; before the merge it works as before. The workflow that holds the release
  app's key starts only from a merge to `main` or from a dispatch by someone with write
  access, and backports only pull requests merged into `main`. A push whose commit names no
  pull request fails the run instead of skipping a backport quietly.
- A brownout or a missing policy cannot be fixed by re-running: the run has no job. A new
  event on the pull request starts a new run (runbook).
- Not known from GitHub's documentation: whether a policy without an actor rule changes who
  may start the gate on a fork's pull request. The first fork pull request after step 2 shows
  it; if GitHub refuses it, that is drift of this decision and needs a follow-up.
