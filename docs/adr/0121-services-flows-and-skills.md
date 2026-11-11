# 0121. Services, named flows and skills: what is offered, how it is handled, who may do it

Status: accepted, partly built (2026-11-10), requested by the product owner. Extends ADR 0047 (admin structure), ADR 0081 (subjects and routing attributes), ADR 0032 (contact-center connections) and ADR 0077 (terminology); builds on ADR 0116 (expand/contract).

## Context

A *group* bundles responsibilities that differ: what the organisation offers, how a request is handled, which expertise it needs, which capacity is reserved for it and where the work is distributed. The first client's wish list shows the strain: some work needs no video, some is an immediate request or an arrival at a physical place, one team's people serve several offerings, and some offerings are independent of each other. A physical counter must not become a product type; administrators name things themselves.

The word *group* also confuses administrators of a contact center, who know groups as teams of agents.

## Decision

1. **Vocabulary.** *Dienst / Service* is what an organisation offers (the current group, same identity). *Flow / Flow* is a named, validated way of handling a request. *Onderwerp / Subject* is what a request is about (ADR 0081, unchanged). *Vaardigheid / Skill* is a required or held expertise. *Capaciteitspool / Capacity pool* is reserved planning supply. In the domain: `ServiceOffering`, `ServiceFlow`, `Subject`, `Skill`, `SkillRequirement`, `CapacityPool`, `ServiceRequest`, `WorkItem`, `IntakeEndpoint`, `DispatchTarget`. `ServiceOffering` keeps the business entity apart from application classes such as `BookingService`. An OpenPlanner flow is not a contact-center flow: integration screens say *Contact-center flow* and the field is `contact_center_flow_ref`.
2. **Structure.** Organisation → ServiceOffering → ServiceFlow. Subjects stay scoped to the service (a `SubjectFlow` binds them to flows, with extra skill requirements). Skills and capacity pools belong to the organisation. Locations and workstations are optional organisation resources with free labels. Sharing a pool grants no access to another service's requests.
3. **No behaviour from names.** Service and flow names are administrator-controlled localised labels (Dutch and English required when publishing). IDs and codes are stable. No branch may select behaviour from a name; a test uses arbitrary names.
4. **A flow is configuration, not code.** An immutable published `FlowRevision` holds intake modes (`appointment`, `on_demand`), `interaction_kind` (`video`, `telephone`, `in_person`, `none`), duration, one capacity pool, required skills (ALL_OF), subject bindings, dispatch target, check-in, completion and participant-data policies and the handling action. The combinations of interaction kind, readiness and first handling action are a closed table (video → media presence → open video; telephone → automatic → call; in person → explicit check-in → view request or assign workstation; none → automatic → view request). A request stores the revision it was made under. `ChannelKind` keeps meaning message delivery (email, SMS); `channel_preference` of ADR 0081 stays a routing preference.
5. **Skills are enforced.** Requirement, capability and authority are three different relations: a flow or SubjectFlow requires skills, staff and pool units hold them, the authorisation matrix grants access. Claiming is allowed only when authorised and eligible and assignable, checked inside the locked claim and again at treatment start. Skills never grant visibility and are never inferred from names, roles or queues. A count-based pool promises that every counted unit has all its guaranteed skills. External routing receives only explicitly mapped codes; an unmapped requirement is an incomplete configuration, never dropped.
6. **Capacity is interval accounting in one pool.** A reservation consumes the pool across its whole interval; check-in reuses the reservation; waiting work consumes admission capacity, not treatment capacity; immediate treatment may not consume protected reservations; assignments have a bounded lease; overruns stay visible and block conflicting starts. One pool per flow; many flows may share a pool within one organisation. No automatic cross-pool overflow in the first release.
7. **Request lifecycle.** `ServiceRequest` is the central identity with an optional reservation (the existing booking), an optional participant-data record and at most one active work item; a provider task is a delivery attempt. Reservation, attendance, handling, media and personal-data lifecycle are separate states. Video and email are optional per flow; missing data is not "deleted data". A bounded watchdog (24 h after the reservation end or the accepted intake) expires unstarted work and marks started work `recovery_required` without releasing its capacity; the personal-data deletion deadline is the watchdog time plus 24 hours. A timed-out external create is `reconciliation_required` and not locally claimable until fenced.
8. **Compatibility.** `groups.id`, `organisation_id`, slug, `idp_key`, `/g/{slug}`, `/admin/groups/...`, `group.*` permissions, `group_admin`, entitlement strings, `openplannerGroup`, job and event names and the configuration package stay as they are; only the label changes first. A single source of truth during the transition: `ServiceOffering` is a view of `Group`, not a second table. The backfill creates explicit legacy flow wrappers, infers no skills, shares no pools and keeps the legacy direct and scheduled capacity semantics apart. An old client without a flow uses the configured default; an explicit unknown, foreign or archived flow is refused.
9. **Activation and rollback.** The model lands additively. Normal flows are not activated until skill enforcement, capacity accounting, a completion path, bounded retention and a rollback proof exist. The previous supported image must be tested on the expanded database after the new image has written new records and jobs, including booking and claiming after the rollback; otherwise the new behaviour is not ready (ADR 0116).
10. **Build order** is the table below. Out of scope for the first delivery: arbitrary workflow programming, cross-pool optimisation, proficiency or OR skills, workforce-calendar integration, printer drivers, a device fleet manager, a new contact-center platform adapter.

The detail (flow field contract, acceptance matrix A01–A36, fixture *Persoonlijk advies / Personal advice*) is in `docs/service-flows-handover.md`; that document is the build brief, this ADR is the decision.

11. **Choices of the product owner (2026-11-10).** Personal data of a request without an appointment lives in a new request-level record, built in step 5 together with the change of the "PII only on Booking" rule. Skills, skill grants and capacity pools are managed under new organisation-level permissions `skills.manage` and `capacity.manage`. The first complete demonstration is one flow without video on the own queue; video flows, the contact center and immediate intake follow.

## Settings

None in step 1: it changes labels only. The later steps add the following, each registered in `app/domain/settings_registry.py` with class, default and bounds when its step is built (proposed starting values, not existing settings):

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `treatment_buffer_minutes` | D organisation | 0 | 0–120 | `capacity.manage` (point 11) | per pool or flow policy |
| `assignment_start_lease_minutes` | D organisation | 5 | 1–30 | `capacity.manage` (point 11) | expiry only before treatment starts |
| `request_unresolved_hours` | D organisation | 24 | 1–168 | `capacity.manage` (point 11) | the watchdog |

**Fixed on purpose:** the personal-data deletion 24 hours after a terminal state and after the watchdog deadline (a privacy protection, not tunable, ADR 0102); the closed table of interaction kinds and readiness policies; ALL_OF skill matching.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR and handover document | #712 | merged |
| 1 | P1: *Group* is shown as *Dienst / Service* in both catalogues; technical identities unchanged | #712 | merged |
| 2a | Domain rules (skills, capacity pools, service flows and the readiness checklist) and the permissions `skills.manage` and `capacity.manage` | #734 | merged |
| 2b | Tables, migration and backfill of legacy pools and flows; persistence port and adapter | #738 | merged |
| 2b-ii | Skills, pools and flows in the configuration package (export and import) | #PRNUM | merged |
| 2c | Screens: skill catalogue, capacity pools, skill grants on staff | | planned |
| 2d | Screens: flows tab and flow editor (draft and validate) | | planned |
| 2e | A service created later, by the portal or by a configuration import, and a service that starts offering the direct call get their legacy pool and default flows from one domain rule; an idempotent repair covers services created by an older image during a rollback | | planned |
| 3 | P3: one flow resolver for page, widget, API and admin; interval accounting; new-pool admission | | planned |
| 4 | P4: skill enforcement in the locked claim; pool guarantees; external skill mapping | | planned |
| 5 | P5: generic request execution without video, optional contact data, watchdog and deletion | | planned |
| 6 | P6: channel-independent contact-center task contract, reconciliation and fencing | | planned |
| 7 | P7: immediate intake and check-in on the same pool | | planned |
| 8 | P8: compatibility matrix, upgrade and rollback proof, documentation | | planned |
| 9 | Manual topics in `docs/manual/{nl,en}` say *Dienst / Service* (at the next major release, product owner 2026-11-10) | | follow-up |
| 10 | The full demonstration fixture: a video flow and an in-person flow on one shared pool, with a third independent flow | | follow-up |
| 11 | Internal names follow the vocabulary (`Group` to `ServiceOffering` in code, URLs, permissions), by expand/contract | | follow-up |
| 12 | Skills and pools delegated below the organisation admin (per service or per pool) | | follow-up |
| 13 | Named staff allocations with interval exclusivity, so one person is never counted in two pools | | follow-up |
| 14 | Overflow between capacity pools | | follow-up |
| 15 | Skills with a level, alternatives (OR) or relaxation over time | | follow-up |
| 16 | Skill synchronisation with the contact center, in both directions | | follow-up |
| 17 | A subject catalogue shared by the services of an organisation | | follow-up |
| 18 | Joint planning of a person and a workstation or room | | follow-up |
| 19 | Workforce-calendar integration | | follow-up |
| 20 | Ticket printing and management of intake devices | | follow-up |
| 21 | A further contact-center platform adapter on the channel-independent task contract | | follow-up |
| 22 | The role-name rule of ADR 0077 also refuses the previous shipped names (*Groepsbeheerder*, *Group admin*) and checks stored names after a catalogue change | | follow-up |

## Consequences

- The backlog is the *Build status* table: every `planned` and `follow-up` row becomes an issue labelled `adr:0121` (`scripts/adr_issues.py`), so a postponed option is not lost. The option *every request is a Booking* was rejected, not postponed, and has no row.
- Administrators and staff see *service* where they saw *group* (portal, reports, staff alerts); no participant-facing text, widget text or message changed in step 1, they name the service by its own name. The manual topics keep the old word until the next major release (product owner, 2026-11-10). Texts an organisation overrode keep their wording and need a manual pass by that organisation.
- The URLs and identifiers keep the old name for now; reading the code, `Group` and *service* mean the same thing until a later, separate clean-up.
- Every later step must prove against the acceptance matrix before a flow may be activated; until then the repository behaves as before.
- Open for the product owner: whether `docs/service-flows-handover.md` stays in the repository after step 8, and the Dutch term *Dienst* for the first client's own vocabulary (organisations may rename display nouns later through ADR 0077 style terms).
