# The channel-independent contact-center task contract (build brief of ADR 0121 step 6)

Status: design of step 6 of ADR 0121 (*P6: channel-independent contact-center task contract, reconciliation and fencing*), written against `main` after step 5a (#766), with step 5b (#767) in the merge queue. ADR 0121 points 4, 5 and 7, the decision D3 of the product owner (the first complete demonstration is one flow without video on the own queue; the contact center follows) and ADR 0018, 0021, 0032, 0081 and 0082 are taken as given. Section 7 lists the choices this brief made itself, each with the default it builds.

Step 6 is cut in two:

- **6a (this brief, built now):** the contract. A task is described without naming a platform: what kind of interaction it is, which skills it requires, which booking it belongs to and how it is routed. Each platform adapter declares what it can carry, and the readiness checklist of a flow uses that declaration. Webex Contact Center is ported to the contract with unchanged payload bytes.
- **6b (section 11):** dispatching the work items of a *new* flow to the contact center: `DispatchKind.CONTACT_CENTER` in `BUILT_DISPATCH`, a provider task as a delivery attempt of a work item, reconciliation of a timed-out create (`reconciliation_required`, ADR 0121 point 7) and fencing between the own queue and the contact center (acceptance A21 and A22). It needs step 5b's execution path and so follows it.

## 1. What exists, and what the contract adds

The neutral task is already in the domain: `ContactCenterTask` (`app/domain/calls.py`) carries the booking reference, first name, start label, group name, meeting link, kind of call, the group's routing at its platform, the routing variables of ADR 0081 and the mapped skill codes of step 4b. The application holds one `ContactCenterDispatcher` (`app/domain/ports.py`) per `ContactCenterKind` and picks it by the group's routing; `app/adapters/wxcc.py` turns the task into a Create Task v2 body. What was missing for other platforms and other interaction kinds:

| Missing | Added in 6a |
|---|---|
| The task does not say what kind of interaction it is; every task is a video call by assumption | `ContactCenterTask.interaction: InteractionKind`, taken from the flow revision recorded on the booking (a booking without a flow is a video booking: the dispatch needs its meeting) |
| Nothing says which interaction kinds a platform can carry, or whether it can route on skills | `ContactCenterCapabilities(interaction_kinds, skills)` on each adapter's `ProviderDescriptor` |
| A flow can be configured for an interaction its service's platform cannot carry | Readiness reasons `contact_center_missing`, `interaction_not_carried` and `skills_not_carried` |
| A task the platform cannot carry would be sent anyway | `uncarried(capabilities, task)`: the domain refuses to build it (`integration_not_ready`), and the adapter refuses it again before a byte leaves (`not_ready`) |

`InteractionKind` moves from `app/domain/service_flows.py` to a module of its own, `app/domain/interaction.py`, so the contact-center module can name it without an import cycle (`service_flows` → `scheduling` → `contact_center`); `service_flows` imports it from there and every existing import keeps working.

## 2. The contract

```python
@dataclass(frozen=True)
class ContactCenterCapabilities:          # app/domain/contact_center.py
    interaction_kinds: frozenset[InteractionKind] = frozenset()
    skills: bool = False

@dataclass(frozen=True)
class ProviderDescriptor:                 # gains one field, defaulting to "carries nothing"
    ...
    capabilities: ContactCenterCapabilities = ContactCenterCapabilities()

@dataclass(frozen=True)
class ContactCenterTask:                  # app/domain/calls.py, gains one field
    reference, first_name, start_label, group_name, meeting_link, kind,
    routing, routing_value, variables, skills,
    interaction: InteractionKind = InteractionKind.VIDEO

def uncarried(capabilities, interaction, skills) -> str | None   # "interaction_kind" | "skill_codes" | None
```

- **Channel-neutral fields only.** The task names no platform field, endpoint, queue id or media type. Each adapter maps it to its API in one place (for Webex Contact Center, `task_body` in `app/adapters/wxcc.py`, which stays the one place that builds that payload).
- **The interaction kind comes from the flow.** `CallService.ensure_task` already reads the revision recorded on the booking for its skills (step 4b); it now reads the revision's `interaction_kind` in the same read. A booking without a flow, and every legacy flow, is `video`.
- **The privacy rule is unchanged.** The interaction kind is a value of a closed list, not personal data and not free text. It adds nothing to what may leave OpenPlanner (CLAUDE.md, *Privacy rules*): the payload still carries only the booking reference, first name, start label, group name, host join URL, kind of call, the routing variables of ADR 0081 and the mapped skill codes. A telephone interaction never adds the participant's phone number to a payload (`docs/service-flows-handover.md`, field contract); that needs its own contract change.
- **The safe default.** A descriptor that declares nothing carries nothing: every flow that dispatches to that platform is not ready and every task is refused. A new adapter has to state what it carries before anything reaches it.

## 3. What Webex Contact Center declares

| Capability | Declared | Why |
|---|---|---|
| `video` | yes | The work item carries the host join URL; this is what runs today (ADR 0018) |
| `telephone` | no | A call needs a destination, and the participant's phone number may not leave OpenPlanner; a callback design is its own decision (section 7, C2) |
| `in_person` | no | A visit at a counter is handled in the own queue with check-in (step 7); no contact-center flow takes arrivals |
| `none` | no | A work item without a meeting link is valid at the platform (acceptance A21), but the asset schema and the client's flow expect `meetingLink`; it comes with step 6b (section 7, C1) |
| skills | yes | Mapped codes in `openplannerSkills` (step 4b) |

The interaction kind is **not** sent to Webex Contact Center: with one kind carried it would always say `video`, and the payload of every existing flow stays byte for byte what it was. When a second kind is carried, a reserved flow variable (`openplannerInteraction`) is added in that PR, written after the routing variables like `openplannerGroup` (section 7, C3).

## 4. Readiness

The readiness check needs to know which platform the flow's service routes to. `capabilities_of(routing)` returns the declared capabilities of a *complete* routing of the service (`Group.contact_center`), else `None`; `readiness(..., carrier=...)` takes it. Only a non-legacy revision whose dispatch is the contact center reads it:

| Check | Reason | When | Text (nl / en, `admin.flows.reason.*`) |
|---|---|---|---|
| Dispatch | `contact_center_missing` | the service has no complete contact-center routing | the service is not connected to a contact center |
| Dispatch | `interaction_not_carried` | the platform does not carry the flow's interaction kind | the contact center of this service cannot take this way of contact |
| Skills | `skills_not_carried` | the flow or a subject requires skills and the platform cannot route on skills | the contact center cannot route on skills |

The dispatch reasons come after `kind_missing`, `dispatch_missing` and `action_not_allowed` and before `not_built_yet`, so the administrator sees what they can fix first; `skills_not_carried` comes before `skill_unmapped` (a code is useless where skills cannot travel). None of the three is in `FOLLOWING`: each is fixed by choosing another way of contact, another dispatch target, or a connection that carries it. A **legacy** revision skips them, as it skips the `BUILT_*` tests: a legacy flow follows its service's own settings, and its contact-center routing is judged by the group check of ADR 0082 (completeness, a real probe), so a configuration import of a service without routing is not refused because of its legacy wrapper.

The same carrier is passed by the portal (`flow_service._readiness_of`) and by the configuration document (`configuration_v1_flow_rules`, read from the group's `contact_center` field with the installation's descriptors), so the two judge alike.

While `BUILT_DISPATCH` holds only the own queue, the editor and the configuration document cannot choose the contact center for a new flow, so these reasons are visible only in tests until step 6b. They are built now so 6b cannot forget them, as `skill_unmapped` was in step 4b.

## 5. Dispatch

```
ensure_task(booking, group)
  routing ─► dispatcher of routing.kind (unchanged)
  one read: revision recorded on the booking ─► interaction kind, required skills ─► mapped codes
  contact_center_task(..., interaction, skills)
      uncarried(routing.descriptor.capabilities, interaction, skills) ─► DomainError integration_not_ready
  dispatcher.create_task(task)
      adapter: uncarried(DESCRIPTOR.capabilities, ...) ─► IntegrationError not_ready (not retryable)
```

The interaction kind comes from `interaction_of(revision, recorded)` next to `requirement_of`: video for a booking without a flow, and a refusal (`interaction_kind`) for a recorded flow whose revision is gone or names no kind, never a guess. Step 6b must move the `meeting_missing` wait of `ensure_task` behind the interaction kind: a non-video task has no meeting to wait for. The refusal in the domain is the one that counts; the adapter's check is a second line for a caller that builds a task by hand. A refused task is handled like the existing `skill_codes` refusal: the dispatch job fails for good and the fallback of ADR 0082 takes the booking into the own queue.

## 6. Proof that nothing changes for existing flows

- `tests/adapters/wxcc_task_bodies.json` was recorded from `main` before this change: the wire form (`httpx`'s `json=` encoding) of a scheduled and a direct booking, one with routing variables, one with skill codes, the probe of a group check and the cancellation message. `tests/adapters/test_wxcc_recorded_bodies.py` compares the current bodies byte for byte.
- The existing payload tests (`tests/adapters/test_wxcc.py`, `test_wxcc_routing_payload.py`, `test_wxcc_skill_codes.py`, `tests/domain/test_integration.py`) run unedited.
- Every legacy flow is `video` with a contact-center routing of Webex Contact Center, which carries video: no legacy readiness item changes, and a legacy revision skips the new reasons anyway.

## 7. Choices this brief made (for the product owner; built with the default)

| # | Question | Default built |
|---|---|---|
| C1 | Which interaction kinds does Webex Contact Center carry now? | `video` only, as today. `none` (a work item without a meeting link, A21) comes with step 6b, together with the asset-schema and sample-flow change it needs |
| C2 | May a telephone interaction go to the contact center? | Not now: it would need a callback destination, and the phone number may not leave OpenPlanner. Follow-up row 25, with its own privacy decision |
| C3 | Does the interaction kind travel in the Webex payload? | Not while one kind is carried (bytes unchanged); the reserved variable `openplannerInteraction` arrives with the second kind |
| C4 | *Voice* and *chat* | *Voice* is `telephone`. *Chat* is not an interaction kind of the closed table of ADR 0121 point 4; adding it changes that table and needs an ADR (follow-up row 26) |
| C5 | Is a contact-center flow on a service without a complete routing ready? | No: reason `contact_center_missing` |
| C6 | Are the capabilities a setting? | No. What an adapter can carry is a fact of its code, not a tunable value; a descriptor declares it as a constant |
| C7 | Do the new reasons apply to legacy flows? | No: a legacy flow is judged by the group check of ADR 0082, as it is today |
| C8 | Reconciliation and fencing | Step 6b, with the dispatch of new flows to the contact center (section intro) |

## 8. Settings

None. The capability declaration is a property of the adapter's code (C6); the readiness reasons have no threshold. Fixed on purpose: the closed list of interaction kinds (ADR 0121 point 4) and Webex Contact Center's own limits (unchanged).

## 9. Threat model

Added to `docs/threat-model.md`, flow *routing variables and the contact-center payload*:

| STRIDE | Threat | Mitigation |
|---|---|---|
| Information disclosure | A flow whose interaction is not a video call reaches a platform as a video work item, or a telephone flow adds the participant's phone number to the payload to make the call possible | The interaction kind travels in the neutral task; the domain and the adapter refuse a task whose kind the platform does not declare; no adapter adds a field for an interaction kind; Webex declares `video` only; the recorded bodies pin the payload |
| Tampering / denial of service | A new adapter is registered without saying what it carries, so tasks reach it that it cannot handle and agents get unusable work | The capability default carries nothing: every task is refused and every flow on it is not ready until the adapter declares its capabilities |

## 10. Pull requests

| PR | Content | Acceptance |
|---|---|---|
| 6a | `app/domain/interaction.py`, `ContactCenterCapabilities`, `capabilities_of`, `uncarried`, `ContactCenterTask.interaction`, the interaction kind in `CallService.ensure_task`, Webex's declaration and its second-line check, the three readiness reasons with nl and en texts, the carrier in the portal's and the configuration document's readiness, the recorded bodies, `docs/webhooks.md`, threat-model rows, this brief, ADR 0121 build status rows 6a and 6b | contract and capability side of A21; payload unchanged |
| 6b | Section 11: the readiness checklist in `app/domain/flow_readiness.py` (first commit); `DispatchKind.CONTACT_CENTER` and `CompletionPolicy.PROVIDER_EVENT` built, reason `completion_not_for_dispatch`; `none` on Webex with `openplannerInteraction`; `app/domain/contact_center_attempts.py`, `app/domain/request_dispatch.py`; `IntegrationError.ambiguous`; the origin of a task event; migration 0114; setting `contact_center_reconcile_minutes` | A21, A22 |

## 11. Step 6b: dispatching the work of a new flow to the contact center

Addendum written against `main` after step 6a (#770). Section 7's defaults C1, C3 and C8 are now built; the choices 6b makes itself are E1 to E10 below, each built with its default.

### 11.1 What a new contact-center flow is

- `DispatchKind.CONTACT_CENTER` joins `BUILT_DISPATCH` and `CompletionPolicy.PROVIDER_EVENT` joins `BUILT_COMPLETION`. A flow dispatched to the contact center completes by the platform's event, a flow on the own queue by the operator (**E2**): any other pair is the new readiness reason `completion_not_for_dispatch` (point *Completion*, fixable, nl and en text).
- Webex Contact Center now carries `none` as well as `video` (**E1**, choice C1): a work item without a meeting, `meetingLink` empty, and the reserved flow variable `openplannerInteraction` set to the interaction kind. The variable is written only for a kind other than `video`, after the routing variables like `openplannerGroup`, so every existing body stays byte for byte what it was (the recorded bodies of 6a) and a routing variable cannot stand in for it. `telephone` and `in_person` stay not carried (C2, follow-up rows 25 and 26). The asset schema needs no new key: `workItemData` keeps its six keys; the client's flow reads `openplannerInteraction` to tell a task without a meeting apart. The interaction kind is a value of a closed list, the *kind of call* of the outbound allow-list in CLAUDE.md; no personal field is added.
- `CallService.ensure_task` reads the interaction kind (and the skills) of the recorded revision before it waits for a meeting: only a video task waits for one (`meeting_missing`); `contact_center_task` asks for a meeting only for `video`.
- Every new flow still waits for the rollback gate (`ROLLBACK_SAFE` stays false): nothing in this step is reachable on a host; the tests open the gate.

### 11.2 One owner: the fence

The work of one request has at most one active work item: the partial unique index `uq_work_items_active` of migration 0113 is the fence, and a new column `work_items.channel` (`own_queue`, `contact_center`; migration 0114) says who holds it (**E3**).

| Who comes first | What the other one gets |
|---|---|
| An operator claims the booking in the own queue (`claimed_by`, an own-queue work item) | The dispatch job skips it: `own_queue` |
| The dispatch job opens a contact-center work item | A claim in the own queue is refused: `already_claimed`, as for a colleague's claim |
| A contact-center attempt was fenced (11.4) | The request is queued again and only the own queue may take it; the dispatch job does not try a second time |

A flow on the own queue is never dispatched: its bookings' dispatch job ends with `own_queue` whatever the service's group-level dispatch says, because the flow decides (ADR 0121 point 4). The task id is kept where it is today, on the booking (`contact_center_task`, `wxcc_task_id`, already a purged provider link), so the timeline, the call status, the cancellation's end-task job and the purge work unchanged and no new column links to the platform (**E4**).

### 11.3 The attempt

```
dispatch job (request work, flow dispatches to the contact center)
  lock: request of the booking (made now if none) ─► active item? ─► skip (owner says why)
        work item channel=contact_center, state dispatching ─► commit
  create_task(task with interaction kind, skills, no meeting for `none`)
     ok ─────────────► lock: task on the booking, item dispatched, request assigned
     refused / unreachable before sending ─► item failed, request queued; the job retries as
                        today; its final failure hands the booking to the fallback (ADR 0082)
     outcome unknown ─► item reconciliation_required; never retried (a retry could make a
                        second task); the request is not claimable while it lasts
```

The task is built before the attempt opens, so a task that cannot be built (an unmapped skill, a kind not carried) opens no attempt. Every write of an attempt is conditional on the state it was read in; a lost condition refuses the whole transaction rather than half writing it.

**Outcome unknown (E5)** is a transport error after the request may have reached the platform (a read timeout, a broken connection, a protocol error) or a `504`; `IntegrationError.ambiguous` says so, set by the authorised client. A connect error, a refusal (`4xx`) and another server error are *not sent* or *not made*: the attempt fails and the job's own retry applies.

### 11.4 Reconciliation and fencing (A22)

- A provider event for an unknown task whose `origin` is a booking reference (Create Task sends the reference as `origin.id`; the parser keeps only that id, never the name next to it) is matched to that booking (**E7**), only while the booking is confirmed and has no task, and only for the organisation of the webhook address that received the event. If its request's active attempt is `reconciliation_required`, the task is attached: kept on the booking, the attempt `dispatched` (no duplicate task: the late one *is* the task). An attempt still `dispatching` is never adopted: its create is still running and returns the task itself (a `task:new` can arrive before the create's answer). If one of the request's contact-center attempts was fenced within the last `contact_center_reconcile_minutes`, the late task is kept on the booking and ended at the platform by the existing end-task job (marked *cancelled* first, ADR 0021), so the agent sees it is not theirs; a later one is not matched at all.
- The reconciliation round (worker, with the request watchdog) fences every attempt still `dispatching` or `reconciliation_required` after `contact_center_reconcile_minutes` (**E6**, class C, default 10, bounds 2 to 120): the attempt ends `fenced`, the request is queued for the own queue and the fallback of ADR 0082 tells the team (`dispatch_failed`). From then on one authority holds the request: the own queue.
- The origin is the booking reference (`OP-` and the booking's id), unique within one installation only: two installations must not share one contact-center tenant (`docs/runbook.md`), or a late task of the one could be matched in the other. The parser keeps an origin only when it has the shape of a reference, so a caller's address on a task the client's own flows made is never held.
- Whether Webex Contact Center's task events carry the origin is not yet seen on the test host: until it is, a late task of a fenced attempt cannot be recognised and stays at the platform (open item; the reconciliation still fences, so OpenPlanner never has two owners).

### 11.5 Events on a contact-center attempt (A21)

| Platform event | Attempt | Request and booking |
|---|---|---|
| `task:connected` | `in_progress`, started now; occupies a unit of the booking's pool until start plus length plus wrap-up; an overrun start is recorded (`start_conflict`), never refused (the call is already running) | `in_progress` |
| `task:ended` | `completed`, its unit released once | `completed`; a confirmed booking `completed` |
| `task:failed` | `failed` | `queued` for the own queue (the timeline shows the failure) |

The booking timeline records the event as before. A request that is terminal or whose attempt is not the contact center's ignores the event (**E8**).

### 11.6 Choices of 6b (built with the default)

| # | Question | Default built |
|---|---|---|
| E1 | Which kinds does Webex Contact Center carry? | `video` and `none`; `none` with an empty `meetingLink` and `openplannerInteraction` |
| E2 | Which completion goes with which dispatch? | contact center ⇔ provider event, own queue ⇔ operator (`completion_not_for_dispatch`) |
| E3 | What is the fence? | One active work item per request (existing index) with its owner in `work_items.channel` |
| E4 | Where is the task id kept? | On the booking, as today; no new provider link column |
| E5 | Which failures are *outcome unknown*? | Transport errors after sending and `504`; never retried |
| E6 | How long before an unknown outcome is fenced? | `contact_center_reconcile_minutes`, class C, default 10, 2 to 120 |
| E7 | How is a late task recognised? | By the booking reference in the event's `origin`; open item until seen on the test host |
| E8 | What do platform events do? | connected = start, ended = complete (also without a connect: the platform says the work is done), failed = back to the own queue |
| E9 | A contact-center attempt that the watchdog expires | Expired as before; its task at the platform is not ended (follow-up row 27) |
| E10 | The own queue's row of work the contact center holds | Shown as open; a claim is refused `already_claimed` (follow-up row 28: show it as held by the contact center) |

### 11.7 Settings, migration, threat model

- Setting `contact_center_reconcile_minutes` (C, platform, default 10, bounds 2–120, superuser): a delay, not a protection, so C; the fence itself cannot be switched off.
- Migration 0114 (expand only): `work_items.channel` (NOT NULL, default `own_queue`, check), the state list widened by `dispatching`, `dispatched`, `reconciliation_required`, `fenced` and `failed`, and `uq_work_items_active` rebuilt over the new active states. The previous minor never writes them; it could not read a work item in a new state, and only a non-legacy flow writes one, which the rollback gate keeps shut (ADR 0122 decision 12). Downgrade refuses while a contact-center work item exists.
- Threat model rows: a duplicate task after a timeout; two owners of one request; a forged late event attaching a task to someone else's booking (the event is signed and verified as every WxCC event, and the origin only matches a booking whose request has an attempt in doubt or fenced).
