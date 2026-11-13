# The channel-independent contact-center task contract (build brief of ADR 0121 step 6)

Status: design of step 6 of ADR 0121 (*P6: channel-independent contact-center task contract, reconciliation and fencing*), written against `main` after step 5a (#766), with step 5b (#767) in the merge queue. ADR 0121 points 4, 5 and 7, the decision D3 of the product owner (the first complete demonstration is one flow without video on the own queue; the contact center follows) and ADR 0018, 0021, 0032, 0081 and 0082 are taken as given. Section 7 lists the choices this brief made itself, each with the default it builds.

Step 6 is cut in two:

- **6a (this brief, built now):** the contract. A task is described without naming a platform: what kind of interaction it is, which skills it requires, which booking it belongs to and how it is routed. Each platform adapter declares what it can carry, and the readiness checklist of a flow uses that declaration. Webex Contact Center is ported to the contract with unchanged payload bytes.
- **6b (follow-up row):** dispatching the work items of a *new* flow to the contact center: `DispatchKind.CONTACT_CENTER` in `BUILT_DISPATCH`, a provider task as a delivery attempt of a work item, reconciliation of a timed-out create (`reconciliation_required`, ADR 0121 point 7) and fencing between the own queue and the contact center (acceptance A21 and A22). It needs step 5b's execution path and so follows it.

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
| 6b | `DispatchKind.CONTACT_CENTER` in `BUILT_DISPATCH`; a provider task as a delivery attempt of a work item; `none` on Webex with the asset-schema change; reconciliation of a timed-out create and fencing between the own queue and the contact center | A21, A22 |
