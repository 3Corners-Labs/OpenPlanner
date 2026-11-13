"""The demonstration fixture of the services-and-flows train (ADR 0121 step 10, gate item G08).

The fixture of ``docs/service-flows-handover.md``: a service with the subjects *Contract* and
*Aanvraag / Request*, a counted pool of two units that guarantees ``general-advice`` and a
pool of one unit of its own:

    persoonlijk-gesprek  in person   shared pool   30 min   active, explicit check-in
    documentcontrole     none        own pool      20 min   active

The fixture of the brief also has a video flow, *Samen bespreken / Discuss together*, on the
shared pool. A new flow cannot use video yet (``BUILT_KINDS``; even its draft is refused), so it
joins the fixture with the step that builds video for a new flow (ADR 0121 follow-up row 36).

Both pools are staffed on weekdays: the shared one 09:00 to 12:00, the own one 13:00 to 16:00,
Amsterdam time, for appointments and work without one.

Written through the ``Transaction`` port, as an image with the rollback gate open would write
it, so one fixture serves the SQL adapter, the fake and, through ``seed --demonstration``, a
development database (gate items G06, G07 and G09). Publishing and activation are written
directly, not through the readiness checklist, on purpose: the checklist refuses every new flow
while ``ROLLBACK_SAFE`` is false, and the flow resolver ignores these flows while it is.
Idempotent: what exists is kept. Configuration only, never a participant.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time

from app.domain.authorization import Scope
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.ports import Transaction
from app.domain.routing import GroupRouting, Subject
from app.domain.scheduling import Group, Offer, ScheduleRule, TimeWindow
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ServiceFlow,
)
from app.domain.skills import Skill

EVERYTHING = Scope(everything=True)
TIMEZONE = "Europe/Amsterdam"
PUBLISHED = datetime(2026, 1, 1, tzinfo=UTC)
"""When the fixture's revisions say they were published: a fixed moment, so runs agree."""

GENERAL = "general-advice"
ENGLISH = "language-en"
SKILLS = ((GENERAL, "Algemeen advies", "General advice"), (ENGLISH, "Engels", "English"))
SUBJECTS = (("contract", "Contract", "Contract"), ("aanvraag", "Aanvraag", "Request"))
SHARED_POOL = "personal-advice"
OWN_POOL = "document-review"
IN_PERSON_FLOW = "persoonlijk-gesprek"
REVIEW_FLOW = "documentcontrole"


@dataclass(frozen=True)
class _Flow:
    """One flow of the fixture."""

    code: str
    nl: str
    en: str
    kind: InteractionKind
    action: HandlingAction
    shared: bool
    minutes: int
    active: bool


FLOWS = (
    _Flow(
        IN_PERSON_FLOW,
        "Persoonlijk gesprek",
        "Meet in person",
        InteractionKind.IN_PERSON,
        HandlingAction.VIEW_REQUEST,
        True,
        30,
        True,
    ),
    _Flow(
        REVIEW_FLOW,
        "Documentcontrole",
        "Document review",
        InteractionKind.NONE,
        HandlingAction.VIEW_REQUEST,
        False,
        20,
        True,
    ),
)
"""The flows of the fixture, both active."""


@dataclass(frozen=True)
class Demonstration:
    """The ids of what the fixture stored.

    Attributes:
        group_id: The service.
        shared_pool: The pool of two units the in-person flow draws on (and later the video
            flow).
        own_pool: The pool of one unit of the document review.
        flows: The flow ids by code.
    """

    group_id: int
    shared_pool: int
    own_pool: int
    flows: dict[str, int]


async def seed_demonstration(tx: Transaction, group: Group, publisher: int) -> Demonstration:
    """Store the fixture on ``group`` and its organisation; return the ids.

    Args:
        tx: A locked transaction.
        group: The service the flows belong to; its subjects become the two above.
        publisher: The staff id the published revisions are stamped with.
    """
    organisation = group.organisation_id
    skills = {s.code: s for s in await tx.list_skills(EVERYTHING, organisation)}
    for code, nl, en in SKILLS:
        if code not in skills:
            skill = Skill(None, organisation, code, {"nl": nl, "en": en})
            skills[code] = await tx.save_skill(EVERYTHING, skill)
    general = skills[GENERAL].id or 0
    shared = await _pool(tx, organisation, SHARED_POOL, "Persoonlijk advies", 2, 9, {general})
    own = await _pool(tx, organisation, OWN_POOL, "Documentcontrole", 1, 13, set())
    subjects = tuple(Subject.create(code, {"nl": nl, "en": en}) for code, nl, en in SUBJECTS)
    await tx.save_group_routing(group.id, GroupRouting(subjects=subjects))
    flows = {f.code: f.id or 0 for f in await tx.list_service_flows(EVERYTHING, group.id)}
    for wanted in FLOWS:
        if wanted.code in flows:
            continue
        pool = shared if wanted.shared else own
        required = frozenset({general}) if wanted.shared else frozenset()
        draft = _revision(wanted, pool, required)
        new = ServiceFlow(None, group.id, wanted.code, FlowState.DRAFT, frozenset(), None, draft)
        flow_id = (await tx.save_draft(EVERYTHING, new)).id or 0
        if wanted.active:
            await tx.publish_draft(EVERYTHING, flow_id, by=publisher, at=PUBLISHED)
            await tx.set_flow_state(EVERYTHING, flow_id, FlowState.ACTIVE)
        flows[wanted.code] = flow_id
    return Demonstration(group.id, shared, own, {f.code: flows[f.code] for f in FLOWS})


async def _pool(
    tx: Transaction,
    organisation: int,
    code: str,
    name: str,
    units: int,
    opens: int,
    guaranteed: set[int],
) -> int:
    """Return the counted pool ``code``, stored and staffed on weekdays the first time."""
    pools = {p.code: p for p in await tx.list_capacity_pools(EVERYTHING, organisation) if p.code}
    if code in pools:
        return pools[code].id or 0
    pool = CapacityPool(
        None, organisation, name, PoolMode.COUNTED, frozenset(guaranteed), code=code
    )
    pool_id = (await tx.save_capacity_pool(EVERYTHING, pool)).id or 0
    await tx.set_pool_timezone(EVERYTHING, pool_id, TIMEZONE)
    window = TimeWindow(time(opens), time(opens + 3), units, None, Offer.BOTH)
    for weekday in range(5):
        await tx.add_pool_rule(EVERYTHING, pool_id, ScheduleRule(weekday, window))
    return pool_id


def _revision(wanted: _Flow, pool: int, required: frozenset[int]) -> FlowRevision:
    """Return the first revision of a flow of the fixture, on the own queue."""
    return FlowRevision(
        flow_id=None,
        revision=1,
        labels={"nl": wanted.nl, "en": wanted.en},
        intake_modes=frozenset({IntakeMode.APPOINTMENT}),
        interaction_kind=wanted.kind,
        duration_minutes=wanted.minutes,
        capacity_pool_id=pool,
        required_skill_ids=required,
        subject_skill_ids={},
        dispatch=DispatchKind.OWN_QUEUE,
        handling_action=wanted.action,
        completion=CompletionPolicy.OPERATOR,
    )
