# Skill enforcement, pool guarantees and external skill codes (build brief of ADR 0121 step 4)

Status: design of step 4 of ADR 0121 (*P4: skill enforcement in the locked claim; pool guarantees; external skill mapping*), written against `main` after ADR 0122 step 5 (#761). The decisions of the product owner in ADR 0121 point 5 and in `docs/service-flows-handover.md` (choice S3, acceptance A07–A10, A13, A23, A35, A36) are taken as given; section 7 lists the few choices this brief made itself, each with the default it builds.

## 1. The three relations, and where each is judged

| Relation | Holder | Where it lives | Judged by |
|---|---|---|---|
| Requirement | a flow revision (ALL_OF) plus the chosen subject's extra skills (`SubjectFlow`) | `flow_revision_skills`, `subject_flow_skills` of the revision a booking was made under | `requirement_of(revision, subject)` in `app/domain/service_flows.py` |
| Capability | a staff member (`staff_skills`, only while a member of the skill's organisation) | `Transaction.list_staff_skills` | `eligibility(required, held)` in `app/domain/skills.py` |
| Authority | the authorisation matrix (`queue.work` on the service) | `app/domain/authorization.py` | `AdminService.authorize_group`, unchanged |

Claiming is allowed only when **authorised ∧ eligible ∧ assignable** (assignable: active, not claimed, in this queue; the existing checks). The three are never mixed: a skill grants no visibility and no right, a right supplies no skill, and nothing is inferred from a name, a role or a queue.

### The pure function

```python
@dataclass(frozen=True)
class Eligible: ...

@dataclass(frozen=True)
class Missing:
    skill_ids: frozenset[int]   # never empty

Eligibility = Eligible | Missing

def eligibility(required, held) -> Eligibility
```

`eligible()` and `missing_skills()` (step 2a) stay; `eligibility` is the one answer the queue, the claim and the start use. An empty requirement is always `Eligible`: that is the whole proof that nothing changes for a flow without skills (section 6).

### The requirement of a booking

`requirement_of(revision, subject)` returns `revision.required_skill_ids ∪ revision.subject_skill_ids.get(subject, ∅)`; no revision (a booking without a flow, `flow_id` NULL) requires nothing. The revision is the one **recorded on the booking** (`Booking.flow`), immutable once published, so an edit of the flow after booking never changes what the booking requires (A23, stored meaning kept), while the operator's skills are read fresh at every check (A23, eligibility rechecked). A subject the revision does not bind (the subject was dropped from a later draft, or the booking has none) adds nothing.

### One reader inside a transaction

`app/domain/work_eligibility.py` holds `WorkEligibility`, bound to one open transaction, one organisation and one principal. It reads a revision once per `(flow, revision)` (`Transaction.flow_revision`, the read without a scope that exists for a resolved flow) and the principal's held skills at most once, and only when some requirement is not empty: a queue of bookings without skills reads no skill at all. It finds the staff member by the login's subject (`get_staff`); someone without a staff record or without a membership of the organisation, a superuser included, holds no skills there (the adapter's existing rule). Missing skills are named with the skill objects (code and labels, archived ones included) so the page can show labels; the domain never chooses a label.

## 2. The claim path and the treatment start

```
claim_next(principal, slug)              claim(principal, slug, booking_id)
  authorize queue.work (unchanged)         authorize queue.work, free pick (unchanged)
  BEGIN IMMEDIATE ───────────────┐         BEGIN IMMEDIATE ───────────────┐
  entries ─► first ELIGIBLE      │         booking, fallback, claimed?    │
     open entry (pick order)     │         eligibility(booking) ─► Missing: not_eligible
  none open: queue_empty         │         claim_booking (conditional)    │
  open but none eligible:        │         audit queue.claimed            │
     not_eligible                │         COMMIT ────────────────────────┘
  claim_booking, audit           │
  COMMIT ────────────────────────┘
```

- The held skills are read **inside** the locked transaction (`BEGIN IMMEDIATE`), after the write lock is taken, so a grant revoked between listing and claiming is seen (A09), and of two concurrent claims the conditional update still lets exactly one win (A10, unchanged).
- *Pick up next* skips the open entries the agent cannot take and claims the first one it can, in the existing pick order. When open work remains but none of it fits, the answer is `not_eligible` (not `queue_empty`, which would lie).
- **Treatment start.** In the own queue today, treatment starts when the claiming agent opens the claimed booking: that page (and its polled status fragment) is where the meeting link and the agent's view of the request appear. `QueueService.claimed` therefore runs the same eligibility check before it returns anything; a revoked skill gives `not_eligible` and no link (A35). The claim is not released by that read (a GET never writes); the row stays *mine* and offers *Release* instead of *Open*, and a supervisor can release it as today. Step 5's explicit start action (generic request execution) calls the same check.
- New error code `not_eligible`, shown on the queue list like the other refusals of a claim (409), with the text "You do not have every skill this work requires." No skill is named in the error, so nothing is told that the row does not already show.
- Audit: a refused claim is not an authorisation denial (the matrix allowed it) and is not audited, exactly like `already_claimed` today; a successful claim keeps its `queue.claimed` event.

## 3. The queue view (choice S3)

Work the agent may see but not pick up **stays in its place** in the queue (the queue length and order are the same for every agent of the service), with:

- no *Claim* button, and a chip *Not for you: missing skill* followed by the missing skills' labels in the page's language (a skill label is organisation configuration, not personal data; every viewer of the queue holds `queue.work` on the service, and nobody else sees the page);
- *Pick up next* points at the first open row the viewer can take; the button is shown when there is one;
- a row the viewer holds but no longer qualifies for shows *Release* instead of *Open*.

The overview of queues (`summaries`) and the wait watch keep counting all waiting work: visibility and counts follow rights, not skills. The poll key of the live list includes the missing labels, so a grant or revocation shows within one refresh.

## 4. Pool guarantees

A count-based pool promises that each counted unit holds **every** guaranteed skill; the guarantee is never the union of two people's skills (A13). Until named staff allocations exist (follow-up 13), the portal cannot check the promise against people; it enforces the half it can check:

1. **A flow's requirements are a subset of its pool's guarantees**, for the flow alone and for the flow plus each subject's extra skills. This is the readiness check *Skills* (`pool_does_not_guarantee`, built in step 2a), which refuses publishing and activation.
2. **A pool cannot stop guaranteeing a skill a flow still requires** (`skill_in_use`, built in step 2c, `skills_required_on_pool`).
3. **At the claim and the start, the person must hold the requirement of the booking.** The pool's other guarantees are a planning promise and are *not* demanded of the claimant: requiring them would make a skill a right to see more work, and it would tie an agent to one pool, which ADR 0121 does not decide.

Step 4 adds no new pool rule; it adds tests that pin rules 1 and 2 as part of the step's acceptance (A13, A36) and the claim rule 3. A notice on the pool page that compares the counted units with the number of members holding every guaranteed skill is a follow-up (section 7, D3).

## 5. External skill codes (step 4b)

### What is mapped

An organisation maps each of its skills to one **provider skill code**: the string its contact-center flow tests (for Webex Contact Center, a value the flow compares in a condition or passes to *Queue To Agent* as a skill requirement). One code per skill per organisation; the organisation has at most one contact-center connection (its own or, for one minor, the installation's, ADR 0032 point 11), so "per connection" and "per organisation" are the same key, and the mapping survives replacing the connection's credentials.

| Column (expand only) | Type | Notes |
|---|---|---|
| `skills.contact_center_code` | `String(64)`, nullable | the code grammar of a routing value of ADR 0081 (`[A-Za-z0-9_.:-]{1,64}`); unique per organisation where set (partial unique index); no PII, no secret |

A column on the skill rather than a table of its own: there is exactly one code per skill and per organisation, the code lives and is archived with its skill, and the configuration package carries it as one optional field of the skill (A29) without a new kind. Migration 0112 (next free number on `main` when written; checked again before the PR) adds the nullable column and the index; nothing is backfilled, because no skill was ever mapped. Expand/contract (ADR 0116): the previous minor neither reads nor writes the column, and a skill it saves keeps its code.

### Where it lives (ADR 0047)

The code is a property of the skill, so it lives where the skill lives: organisation → Overview → sub-tab *Skills and capacity* → the skill's own page (`/admin/organisations/{org}/skills/{skill_id}`), one more field *Contact-center code* next to the labels, saved by the same form under `skills.manage`; the catalogue table shows it in a column. No new screen, tab or sub-page (ADR 0047: one home per concern, and the screen that has the skill already has the right owner).

A sub-page of the contact-center card on *Channels* was considered and rejected: *Channels* is read and changed under `integrations.manage`, which is locked to the superuser, so the organisation admin, who keeps the catalogue, could never reach it, and a page there under `skills.manage` would be a second home for skill configuration. The connection's credentials stay where they are.

### The payload

`ContactCenterTask` gains `skills: tuple[str, ...]`: the mapped codes of the booking's requirement, sorted. `app/domain/contact_center_dispatch.py` (the domain side) builds it with `mapped_skill_codes(required, codes)`, which **refuses** (`integration_not_ready`, field `skill_codes`) when one required skill has no code: an unmapped requirement is an incomplete configuration, never dropped. `app/adapters/wxcc.py` (still the one place that builds the payload) writes them as the flow variable `openplannerSkills`, comma-separated, next to `openplannerGroup`, both written last so a routing variable of ADR 0081 can never overwrite them. An empty requirement sends no key at all, so the payload of every legacy flow is byte-for-byte what it is today. A code is no PII and no free text: it comes from the organisation's own catalogue, like the mapped codes of ADR 0081, and the outbound allow-list of CLAUDE.md ("the routing variables of ADR 0081") is extended by "the mapped skill codes" in the same PR (a CLAUDE.md change makes 4b a decision PR).

### Readiness

The readiness check *Skills* gains the reason `skill_unmapped`: a revision whose dispatch is the contact center and which requires (for the flow or a subject) a skill without a code cannot be published or activated; the page names the skills. Contact-center dispatch of a new flow is itself not built until step 6 (`BUILT_DISPATCH`), so until then the reason can only appear next to `not_built_yet`; it is built now so step 6 cannot forget it.

### Conflicts with ADR 0081

- A routing variable named `openplannerSkills`: the payload writes OpenPlanner's key last (as for `openplannerGroup`), so the variable cannot stand in for the skills. Adding the name to `RESERVED_VARIABLES` would make a stored mapping of that name unreadable (the constructor validates on load), which expand/contract forbids; the mapping form refuses it from step 4b on, and a stored one keeps loading.
- Two skills with the same code: refused by the unique key (`slug_taken`, field `code`).
- The total of `openplannerSkills` stays under WxCC's 512-byte value limit: at most 15 skills required by one booking times 64 bytes would exceed it, so the mapping adapter checks the joined length per task and refuses (`integration_not_ready`, `skill_codes`) above 512 bytes; a limit of the platform, a constant, not a setting.

## 6. Nothing changes for flows without skills (proof)

- Every legacy wrapper is created with no skills (`legacy_revision`, step 2b) and a booking made before flows has no `flow_id`: `requirement_of` is empty, `eligibility` is `Eligible`, no skill is read, and the claim, the start and the queue rows behave as before.
- The existing tests of the queue (`tests/domain/test_queue_service.py`, `tests/admin/test_queue_routes.py`, `tests/adapters/db/test_queue_queries.py`) and of the contact-center payload (`tests/adapters/test_wxcc*.py`) run unedited.
- New tests state it directly: a legacy-flow booking and a flowless booking are claimed by an agent with no skills at all, in the fake and against SQLite; the task body of a booking without requirements has no `openplannerSkills` key.

## 7. Choices this brief made (for the product owner; built with the default)

| # | Question | Default built |
|---|---|---|
| D1 | Must the claimant also hold the pool's other guaranteed skills, beyond the booking's requirement? | No: only the booking's requirement (section 4 point 3) |
| D2 | Is an ineligible row moved to the end of the queue? | No: it stays in its place, marked, so the queue is the same for every agent |
| D3 | A notice when a pool counts more units than members hold all its guaranteed skills | Follow-up, not in step 4 (needs staffing peaks against grants; it is a planning aid, not enforcement) |
| D4 | Who maps skill codes, and where | `skills.manage` (organisation admin), as a field of the skill's own page under *Skills and capacity*, not under *Channels* (superuser only) |
| D5 | How codes reach the platform | One reserved flow variable `openplannerSkills`, comma-separated, sorted |
| D6 | A superuser without a membership claims work that requires skills | Refused: a superuser holds no skills in an organisation (rule of step 2b, unchanged) |

## 8. Pull requests

| PR | Content | Acceptance |
|---|---|---|
| 4a | `eligibility` and `requirement_of`; `WorkEligibility`; the checks in `claim_next`, `claim` and `claimed`; error `not_eligible`; queue rows with missing skills and the *Release* of an ineligible own claim; tests for A07–A10, A13, A23, A35, A36 and the unchanged skill-less paths; threat model rows; this brief; ADR 0121 build status rows 4a and 4b | A07, A08, A09, A10, A13, A23, A35, A36 (claim side) |
| 4b | Migration 0112 (`skills.contact_center_code`), the code on `Skill` and in the skills adapter, the field on the skill's page and the column in the catalogue, `mapped_skill_codes`, `ContactCenterTask.skills`, `openplannerSkills` in `wxcc.py`, the refused variable name in the ADR 0081 mapping form, readiness reason `skill_unmapped`, configuration package (the code travels with its skill, A29), `docs/webhooks.md`, CLAUDE.md allow-list sentence | A21 (skill side), A29 |
| 4c | The staff detail's table "what this person may pick up, and why" (screen 5 of the handover) | A07, A08 (explained) |

4b is stacked on 4a's branch once 4a is open: both change the ADR's build status rows, `docs/adr-status.md` and `app/domain/skills.py`, so a branch off `main` would conflict at once. Its pull request targets 4a's branch without auto-merge until 4a has merged and GitHub has retargeted it to `main`. No setting is added by 4a or 4b: eligibility has no threshold, and the 512-byte limit and the code grammar are the platform's fixed rules.
