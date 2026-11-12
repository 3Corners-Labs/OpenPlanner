"""Skills in the own queue (ADR 0121 step 4): right and skill and assignable, at claim and start.

Acceptance A07–A10, A23 and A35 of ``docs/service-flows-handover.md`` on the in-memory store; the
same path against SQLite, through the pages, is ``tests/admin/test_queue_skill_routes.py``.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.authorization import Role
from app.domain.booking import MeetingRef
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import FlowRef
from app.domain.queue_service import QueueService
from app.domain.routing import RoutingChoice
from app.domain.scheduling import DispatchKind, Organisation, ScheduleRule
from app.domain.services import BookingService
from app.domain.skills import Skill
from app.domain.staff import Membership, StaffMember
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import (
    ADA,
    NOW,
    SLOT_0900,
    SLOT_0930,
    TEST_TOKEN_KEY,
    book_confirmed,
    make_group,
    window,
)
from tests.support.principals import principal
from tests.support.service_flows import legacy, revision

GERMAN, INTAKE = 10, 11
SKILLED = principal("s-1", "S", [Role.AGENT], groups=["demo"])
UNSKILLED = principal("s-2", "U", [Role.AGENT], groups=["demo"])
OUTSIDER = principal("s-3", "O", [Role.AGENT], groups=[("demo", "andere")])
SUPERUSER = principal("root", "R", [Role.SUPERUSER])
SKILL_FLOW = FlowRef(50, 1)
LEGACY_FLOW = FlowRef(51, 1)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State(
        organisation_slugs={1: "demo"}, organisations={1: Organisation(1, "Demo", "demo")}
    )
    for group_id, slug in ((1, "demo"), (2, "andere")):
        state.groups[group_id] = make_group(
            id=group_id,
            organisation_id=1,
            slug=slug,
            dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
            queue_free_pick_allowed=True,
            queue_visible_from_minutes=60,
        )
        state.rules[group_id] = [ScheduleRule(weekday=0, window=window(capacity=3))]
    state.skills[GERMAN] = Skill(GERMAN, 1, "german", {"nl": "Duits", "en": "German"})
    state.skills[INTAKE] = Skill(INTAKE, 1, "intake", {"nl": "Intake", "en": "Intake"})
    state.flow_revisions[(50, 1)] = revision(
        flow_id=50,
        required_skill_ids=frozenset({GERMAN}),
        subject_skill_ids={"renewal": frozenset({INTAKE})},
        published_at=NOW,
    )
    state.flow_revisions[(51, 1)] = legacy(flow_id=51, published_at=NOW)
    state.next_id = 100
    return InMemoryUnitOfWork(state)


@pytest.fixture
def queue(uow: InMemoryUnitOfWork, clock: FixedClock) -> QueueService:
    return QueueService(uow, clock, AdminService(uow, clock), b"k" * 32)


async def grant(uow: InMemoryUnitOfWork, subject: str, *skills: int) -> None:
    """Record ``subject`` as an agent of the organisation holding ``skills``."""
    async with uow.locked() as tx:
        if await tx.get_staff(subject) is None:
            await tx.save_staff(
                StaffMember(
                    subject, "-", frozenset(), frozenset({Membership("agent", "demo")}), *[NOW] * 3
                )
            )
        member = await tx.get_staff(subject)
    assert member is not None
    assert member.id is not None
    uow.state.skill_grants = {k: v for k, v in uow.state.skill_grants.items() if k[0] != member.id}
    for skill_id in skills:
        uow.state.skill_grants[(member.id, skill_id)] = (None, NOW)


async def book(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    flow: FlowRef | None,
    start: object = SLOT_0900,
    subject: str | None = None,
) -> int:
    """Book a confirmed appointment of the demo group made under ``flow`` about ``subject``."""
    service = BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0)
    receipt = await book_confirmed(
        service, group_slug="demo", start=start, contact=ADA, locale="nl"
    )
    booking_id = receipt.booking.id or 0
    uow.state.bookings[booking_id] = replace(
        uow.state.bookings[booking_id],
        flow=flow,
        routing=RoutingChoice(subject) if subject else None,
        meeting=MeetingRef("m", "https://video.example/j"),
    )
    return booking_id


def at_start(clock: FixedClock) -> None:
    clock.advance(SLOT_0900 - clock.now() + timedelta(minutes=1))


async def refused(attempt: object) -> ErrorCode:
    with pytest.raises(DomainError) as excinfo:
        await attempt  # type: ignore[misc]
    return excinfo.value.code


async def test_work_without_skills_is_claimed_by_an_agent_who_holds_none(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """Nothing changes for flows without skills: no flow, and a legacy wrapper (A03)."""
    first = await book(uow, clock, None)
    second = await book(uow, clock, LEGACY_FLOW, SLOT_0930)
    at_start(clock)

    view = await queue.view(UNSKILLED, "demo")
    assert [r.missing for r in view.rows] == [(), ()]
    assert view.next_booking_id == first
    assert (await queue.claim_next(UNSKILLED, "demo")).booking_id == first
    assert (await queue.claim(UNSKILLED, "demo", second)).booking_id == second
    assert (await queue.claimed(UNSKILLED, "demo", second)).join_url == "https://video.example/j"
    assert not uow.state.skill_grants


async def test_a_right_without_the_skill_cannot_claim_or_start(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A08: the row is visible and named, but neither claim works; nothing is written."""
    booking_id = await book(uow, clock, SKILL_FLOW)
    await grant(uow, "s-2", INTAKE)
    at_start(clock)

    view = await queue.view(UNSKILLED, "demo")

    assert [r.booking_id for r in view.rows] == [booking_id]
    assert [s.code for s in view.rows[0].missing] == ["german"]
    assert not view.rows[0].eligible
    assert view.next_booking_id is None
    assert await refused(queue.claim(UNSKILLED, "demo", booking_id)) is ErrorCode.NOT_ELIGIBLE
    assert await refused(queue.claim_next(UNSKILLED, "demo")) is ErrorCode.NOT_ELIGIBLE
    assert uow.state.bookings[booking_id].claim is None
    assert not [e for e in uow.state.audit if e.detail == "queue.claimed"]


async def test_skills_without_the_right_give_no_access(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A07: holding every skill is no right to another service's queue."""
    await book(uow, clock, SKILL_FLOW)
    await grant(uow, "s-3", GERMAN, INTAKE)
    at_start(clock)

    assert await refused(queue.view(OUTSIDER, "demo")) is ErrorCode.NOT_PERMITTED
    assert await refused(queue.claim_next(OUTSIDER, "demo")) is ErrorCode.NOT_PERMITTED


async def test_the_subject_adds_its_extra_skills(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A06: the same flow about another subject needs the subject's skills as well."""
    plain = await book(uow, clock, SKILL_FLOW)
    renewal = await book(uow, clock, SKILL_FLOW, SLOT_0930, subject="renewal")
    await grant(uow, "s-1", GERMAN)
    at_start(clock)

    view = await queue.view(SKILLED, "demo")

    assert {r.booking_id: [s.code for s in r.missing] for r in view.rows} == {
        plain: [],
        renewal: ["intake"],
    }
    assert await refused(queue.claim(SKILLED, "demo", renewal)) is ErrorCode.NOT_ELIGIBLE
    assert (await queue.claim(SKILLED, "demo", plain)).booking_id == plain


async def test_pick_up_next_skips_work_the_agent_cannot_take(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """The first open row needs a skill the agent lacks; the next one fits and is claimed."""
    needs_skill = await book(uow, clock, SKILL_FLOW)
    open_to_all = await book(uow, clock, None, SLOT_0930)
    at_start(clock)

    assert (await queue.view(UNSKILLED, "demo")).next_booking_id == open_to_all
    assert (await queue.claim_next(UNSKILLED, "demo")).booking_id == open_to_all
    assert uow.state.bookings[needs_skill].claim is None
    assert await refused(queue.claim_next(UNSKILLED, "demo")) is ErrorCode.NOT_ELIGIBLE


async def test_a_skill_revoked_after_the_listing_is_refused_by_the_locked_claim(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A09: the page offered the claim; the grant went before the click."""
    booking_id = await book(uow, clock, SKILL_FLOW)
    await grant(uow, "s-1", GERMAN)
    at_start(clock)
    assert (await queue.view(SKILLED, "demo")).rows[0].eligible

    await grant(uow, "s-1")

    assert await refused(queue.claim(SKILLED, "demo", booking_id)) is ErrorCode.NOT_ELIGIBLE
    assert uow.state.bookings[booking_id].claim is None


async def test_a_skill_revoked_after_the_claim_stops_the_start(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A35: opening the claimed booking judges the skills again; the claim can be handed back."""
    booking_id = await book(uow, clock, SKILL_FLOW)
    await grant(uow, "s-1", GERMAN)
    at_start(clock)
    await queue.claim(SKILLED, "demo", booking_id)

    await grant(uow, "s-1")

    assert await refused(queue.claimed(SKILLED, "demo", booking_id)) is ErrorCode.NOT_ELIGIBLE
    row = (await queue.view(SKILLED, "demo")).rows[0]
    assert (row.mine, row.eligible) == (True, False)
    await queue.release(SKILLED, "demo", booking_id)
    assert uow.state.bookings[booking_id].claim is None


async def test_the_booking_keeps_the_requirement_of_its_revision(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A23: a newer revision without the skill does not open a booking made under the old one."""
    booking_id = await book(uow, clock, SKILL_FLOW)
    uow.state.flow_revisions[(50, 2)] = replace(
        uow.state.flow_revisions[(50, 1)], revision=2, required_skill_ids=frozenset()
    )
    at_start(clock)

    assert await refused(queue.claim(UNSKILLED, "demo", booking_id)) is ErrorCode.NOT_ELIGIBLE


async def test_two_eligible_agents_claim_one_booking_once(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A10: eligibility adds no second way past the conditional claim."""
    booking_id = await book(uow, clock, SKILL_FLOW)
    await grant(uow, "s-1", GERMAN)
    await grant(uow, "s-2", GERMAN)
    at_start(clock)

    assert (await queue.claim_next(SKILLED, "demo")).booking_id == booking_id
    assert await refused(queue.claim(UNSKILLED, "demo", booking_id)) is ErrorCode.ALREADY_CLAIMED
    assert await refused(queue.claim_next(UNSKILLED, "demo")) is ErrorCode.QUEUE_EMPTY


async def test_a_superuser_holds_no_skills_in_an_organisation(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """Skills are never inferred from a role: the superuser sees the row but cannot take it."""
    booking_id = await book(uow, clock, SKILL_FLOW)
    at_start(clock)

    assert [s.code for s in (await queue.view(SUPERUSER, "demo")).rows[0].missing] == ["german"]
    assert await refused(queue.claim(SUPERUSER, "demo", booking_id)) is ErrorCode.NOT_ELIGIBLE


async def test_an_archived_skill_still_binds_accepted_work(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A28: archiving blocks new use, never weakens a booking that requires the skill."""
    booking_id = await book(uow, clock, SKILL_FLOW)
    uow.state.skills[GERMAN] = replace(uow.state.skills[GERMAN], archived_at=NOW)
    await grant(uow, "s-1", GERMAN)
    at_start(clock)

    assert [s.code for s in (await queue.view(UNSKILLED, "demo")).rows[0].missing] == ["german"]
    assert (await queue.claim(SKILLED, "demo", booking_id)).booking_id == booking_id
