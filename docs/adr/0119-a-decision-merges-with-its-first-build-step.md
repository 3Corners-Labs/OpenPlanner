# 0119. A decision merges with its first build step, and the build status cannot go stale

Status: accepted (2026-11-08), decided by the product owner in the conversation of 2026-11-08
("voor de hele train heb je po:approved"). Partly built: steps 0 and 1 in the pull request that
adds this ADR; step 2 is the version picker's own pull request. Amends ADR 0059 point 7 (the states of a *Build status* row), ADR 0078 (what the decision
gate checks, and point 6, the drift report) and ADR 0001 (an ADR is not merged on its own).

## Context

On 2026-11-08 the product owner looked for the reporting screen in `v0.5.1-rc.1`. That candidate
was the merge of ADR 0117 alone: a design with fourteen planned steps and no code. Its one line
in the version picker read like the feature. A count that day showed 51 of 118 ADRs with open
work, seven of them with nothing built at all.

The tools to see this existed, and still nobody acted on them:

- ADR 0078 point 6 opens one issue per open build step. Those issues are passive: 105 were open
  across 44 ADRs, and some belonged to rows that had since been split or renamed.
- The weekly drift report (issue *ADR drift*) had about 70 lines. Most were rows still saying
  `in PR #N` for a pull request that had merged weeks before: a pull request wrote `in PR`, and
  nothing ever turned it into `merged`. The real findings drowned in that noise.
- `docs/adr-status.md` is kept by hand. Its test only checked that it agreed with each ADR's
  Status line, not with the ADR's *Build status* table. Four ADRs were listed BUILT with open
  steps, and one was listed PARTIAL with every step merged.
- Nothing stopped a design from merging without any build, and nothing slowed new designs
  while old ones waited.

## Decision

1. **A new ADR merges together with its first build step.** The decision gate
   (`scripts/decision_gate.py`) refuses a pull request that adds an ADR unless the first row
   after step 0 in that ADR's *Build status* table says `merged`, `built` or `live`, which means
   the same pull request builds it. An ADR whose Status line says *policy* has nothing to build
   and is exempt. The block stands even with `po:approved`: a design without its build is
   exactly what the label must not let through.
2. **A brake on unbuilt decisions.** The gate refuses a pull request that adds an ADR while
   three or more ADRs are listed NOT BUILT in `docs/adr-status.md` (`UNBUILT_LIMIT`). First
   build, or have the product owner withdraw or supersede, then design more. The limit is a
   constant in the gate on purpose: changing it is itself a decision.
3. **A row never waits for a pull request on `main`.** The pull request that builds a step
   writes its row as `merged` with its own number. That becomes true the moment it merges, and
   a closed pull request never reaches `main`. `tests/test_adr_status.py` fails on any `in PR`
   row, so the drift that filled the report cannot come back.
4. **The overview is tested against the tables.** The same test fails when a row of
   `docs/adr-status.md` says BUILT while its ADR has a `planned` or `follow-up` step, or NOT
   BUILT while a step other than 0 is merged. The weekly report keeps only what needs GitHub
   (issues, pull-request states). Every pull request now checks the rest.
5. **Two new words.** The category SUPERSEDED (an ADR whose decision another ADR took over;
   its Status line says so) and the row state `superseded (ADR NNNN)`. Such a row closes its
   issue like a done row.
6. **Orphaned issues are closed.** When a step is split or renamed (row 4 becomes 4a and 4b),
   the sync of `scripts/adr_issues.py` closes the old issue with a comment instead of leaving it
   open forever.
7. **Visible where the product owner looks.** The release PR lists which ADRs became BUILT
   since the last release and how many still have open work (`scripts/release_pr.py`). The
   version picker marks a candidate that is design only; that part is built in its own pull
   request.
8. **One-time reconciliation.** In this pull request all 51 stale `in PR` rows become `merged`
   (every pull request they name is merged; checked on 2026-11-08). 0074, 0080, 0100 and 0115
   become PARTIAL. 0085, 0078, 0079 and 0108 become BUILT: the audited release `v0.4.0`
   (#511) and the anonymous pull of the public images are the evidence. 0062 and the Pages
   part of 0111 become SUPERSEDED.

## Settings

None: these are rules of the repository, not of the product.

**Fixed on purpose:** `UNBUILT_LIMIT = 3` and the list of states and categories. They live in
the gate and the test, and changing them is a decision (ADR 0078).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR accepted | #NNN | merged |
| 1 | Gate blocks (points 1 and 2), tests on the overview and rows (points 3 to 5), orphan sync (point 6), release PR list (point 7), reconciliation (point 8), template and CLAUDE.md | #NNN | merged |
| 2 | Version picker marks a design-only candidate (point 7) | | planned |

## Consequences

- No design reaches `main` without its first step. A large ADR still lands step by step after
  that, but it is never only a document.
- While three or more ADRs have nothing built, new ADRs wait. On 2026-11-08 there are six
  (0032, 0036, 0045, 0081, 0099 and 0117). Some are being built in the build train started that
  day, so the brake loosens as they land.
- `docs/adr-status.md` and the *Build status* tables cannot disagree on `main` any more.
  "Checked against the code" still means a person or a release audit looked; the date says when.
- A pull request writes `merged` before it has merged. The gate and the queue make that true,
  and a reviewer should read it as "merged when this lands".
