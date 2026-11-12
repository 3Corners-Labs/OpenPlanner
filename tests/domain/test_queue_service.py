"""Working the own queue: claim next, claim a row, release, the host link (ADR 0043)."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Principal, Role
from app.domain.booking import BookingStatus, MeetingRef
from app.domain.calls import CallStatus
from app.domain.errors import DomainError, ErrorCode
from app.domain.queue_service import QueueService
from app.domain.scheduling import DispatchKind, Organisation, ScheduleRule
from app.domain.services import BookingService
from app.domain.staff import Membership, StaffMember
from app.domain.tokens import agent_pseudonym
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.cross_group import booking_id_methods, refused_as_unknown
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

KEY = b"p" * 32
AGENT = principal("a-1", "A", frozenset({Role.AGENT}), frozenset(), frozenset({"demo"}))
COLLEAGUE = principal("a-2", "B", frozenset({Role.AGENT}), frozenset(), frozenset({"demo"}))
GROUP_ADMIN = principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))
OTHER_TEAM = principal("a-3", "C", frozenset({Role.AGENT}), frozenset(), [("demo", "andere")])
AUDITOR = principal("u-1", "U", frozenset({Role.AUDITOR}), frozenset({"demo"}), frozenset())
QUEUE = DispatchKind.OPENPLANNER_QUEUE


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    groups = {"demo": QUEUE, "andere": QUEUE, "wxcc": DispatchKind.CONTACT_CENTER}
    state = State(
        organisation_slugs={1: "demo"}, organisations={1: Organisation(1, "Demo", "demo")}
    )
    for group_id, (slug, kind) in enumerate(groups.items(), start=1):
        state.groups[group_id] = make_group(
            id=group_id, organisation_id=1, slug=slug, dispatch_kind=kind
        )
        state.rules[group_id] = [ScheduleRule(weekday=0, window=window(capacity=2))]
    state.next_id = 100
    return InMemoryUnitOfWork(state)


@pytest.fixture
def queue(uow: InMemoryUnitOfWork, clock: FixedClock) -> QueueService:
    return QueueService(uow, clock, AdminService(uow, clock), KEY)


async def _book(
    uow: InMemoryUnitOfWork, clock: FixedClock, start: datetime = SLOT_0900, slug: str = "demo"
) -> int:
    bookings = BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0)
    receipt = await book_confirmed(bookings, group_slug=slug, start=start, contact=ADA, locale="nl")
    booking_id = receipt.booking.id or 0
    booking = uow.state.bookings[booking_id]
    uow.state.bookings[booking_id] = replace(booking, meeting=MeetingRef("m", "https://webex/j"))
    return booking_id


async def test_agents_see_the_visible_bookings_with_first_names_only(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    first = await _book(uow, clock)
    await _book(uow, clock, SLOT_0930)
    clock.advance(SLOT_0900 - timedelta(minutes=10) - clock.now())

    view = await queue.view(AGENT, "demo")

    assert [row.booking_id for row in view.rows] == [first]
    assert view.rows[0].first_name == "Ada"
    assert view.next_booking_id == first
    assert not hasattr(view.rows[0], "email")


async def test_pick_up_next_claims_the_participant_waiting_in_the_lobby(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    first = await _book(uow, clock)
    second = await _book(uow, clock, SLOT_0930)
    uow.state.call_status[second] = CallStatus(second, participants=1)
    clock.advance(SLOT_0930 - clock.now())

    claimed = await queue.claim_next(AGENT, "demo")

    assert claimed.booking_id == second
    assert claimed.join_url == "https://webex/j"
    booking = uow.state.bookings[second]
    assert booking.claim is not None
    assert booking.claim.claimed_by == agent_pseudonym(KEY, "a-1")
    assert uow.state.bookings[first].claim is None
    event = uow.state.audit[-1]
    assert (event.action, event.detail, event.group, event.booking_id) == (
        "queue.work",
        "queue.claimed",
        "demo",
        second,
    )


async def test_two_agents_never_claim_the_same_booking(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await _book(uow, clock)
    clock.advance(SLOT_0900 - clock.now())

    assert (await queue.claim_next(AGENT, "demo")).booking_id == booking_id
    with pytest.raises(DomainError) as excinfo:
        await queue.claim_next(COLLEAGUE, "demo")

    assert excinfo.value.code is ErrorCode.QUEUE_EMPTY


async def test_a_specific_row_needs_free_pick(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await _book(uow, clock)
    later = await _book(uow, clock, SLOT_0930)
    clock.advance(SLOT_0900 - clock.now())

    with pytest.raises(DomainError) as excinfo:
        await queue.claim(AGENT, "demo", later)
    assert excinfo.value.code is ErrorCode.FREE_PICK_NOT_ALLOWED

    uow.state.groups[1] = replace(uow.state.groups[1], queue_free_pick_allowed=True)
    assert (await queue.claim(AGENT, "demo", later)).booking_id == later
    with pytest.raises(DomainError) as excinfo:
        await queue.claim(COLLEAGUE, "demo", later)
    assert excinfo.value.code is ErrorCode.ALREADY_CLAIMED


async def test_the_join_link_is_for_the_claiming_agent_only(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await _book(uow, clock)
    clock.advance(SLOT_0900 - clock.now())
    await queue.claim_next(AGENT, "demo")

    mine = await queue.claimed(AGENT, "demo", booking_id)

    assert mine.join_url == "https://webex/j"
    with pytest.raises(DomainError) as excinfo:
        await queue.claimed(COLLEAGUE, "demo", booking_id)
    assert excinfo.value.code is ErrorCode.NOT_CLAIMED


async def test_release_by_the_agent_or_a_group_admin_only(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    booking_id = await _book(uow, clock)
    clock.advance(SLOT_0900 - clock.now())
    await queue.claim_next(AGENT, "demo")

    with pytest.raises(DomainError) as excinfo:
        await queue.release(COLLEAGUE, "demo", booking_id)
    assert excinfo.value.code is ErrorCode.NOT_CLAIMED
    await queue.release(GROUP_ADMIN, "demo", booking_id)

    assert uow.state.bookings[booking_id].claim is None
    assert uow.state.audit[-1].detail == "queue.released"
    assert (await queue.claim_next(COLLEAGUE, "demo")).booking_id == booking_id
    await queue.release(COLLEAGUE, "demo", booking_id)
    assert uow.state.bookings[booking_id].claim is None


async def test_an_unknown_booking_does_not_exist_here(
    queue: QueueService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.groups[2] = replace(uow.state.groups[2], queue_free_pick_allowed=True)

    with pytest.raises(DomainError) as excinfo:
        await queue.claim(OTHER_TEAM, "andere", 999)
    assert excinfo.value.code is ErrorCode.BOOKING_NOT_FOUND


ON_ONE_BOOKING: dict[str, Callable[[QueueService, int], Awaitable[object]]] = {
    "claim": lambda s, b: s.claim(GROUP_ADMIN, "demo", b),
    "claimed": lambda s, b: s.claimed(GROUP_ADMIN, "demo", b),
    "release": lambda s, b: s.release(GROUP_ADMIN, "demo", b),
}
"""Every use case on one booking, by the group admin of ``demo`` through their own group."""


def test_every_use_case_on_one_booking_is_tried_through_another_group() -> None:
    assert set(ON_ONE_BOOKING) == booking_id_methods(QueueService)


@pytest.mark.parametrize("state", ["unclaimed", "claimed", "cancelled"])
@pytest.mark.parametrize("action", list(ON_ONE_BOOKING))
async def test_another_groups_booking_is_not_found_through_this_group(
    action: str, state: str, queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """A group admin, who may release any claim of their group, finds no other group's booking.

    The booking is in ``andere``, a sibling group of ``demo``. Unclaimed or claimed, a use case
    would claim, show or release it, or answer another code, so a missing group check fails.
    Cancelled, the queue's own rule answers ``booking_not_active``, so a group check made after
    that rule fails too.
    """
    uow.state.groups[1] = replace(uow.state.groups[1], queue_free_pick_allowed=True)
    other = await _book(uow, clock, slug="andere")
    clock.advance(SLOT_0900 - clock.now())
    if state == "claimed":
        await queue.claim_next(OTHER_TEAM, "andere")
    if state == "cancelled":
        booking = uow.state.bookings[other]
        uow.state.bookings[other] = replace(booking, status=BookingStatus.CANCELLED)

    await refused_as_unknown(uow, other, ON_ONE_BOOKING[action](queue, other))


@pytest.mark.parametrize("principal", [OTHER_TEAM, AUDITOR])
async def test_others_are_refused_and_audited(
    queue: QueueService, uow: InMemoryUnitOfWork, principal: Principal
) -> None:
    with pytest.raises(DomainError) as excinfo:
        await queue.view(principal, "demo")

    assert excinfo.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.audit[-1].outcome is Outcome.DENIED


async def test_a_contact_center_group_has_no_own_queue(queue: QueueService) -> None:
    admin = principal("s", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())

    with pytest.raises(DomainError) as excinfo:
        await queue.view(admin, "wxcc")

    assert excinfo.value.code is ErrorCode.QUEUE_NOT_ENABLED


async def test_not_claimed_in_time_is_flagged(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await _book(uow, clock)
    clock.advance(SLOT_0900 + timedelta(minutes=11) - clock.now())

    view = await queue.view(AGENT, "demo")

    assert view.rows[0].overdue


def _staff(
    subject: str,
    roles: frozenset[Role],
    seen: datetime,
    *memberships: Membership,
    **changes: object,
) -> StaffMember:
    member = StaffMember(subject, subject, roles, frozenset(memberships), seen, seen, seen)
    return replace(member, **changes)  # type: ignore[arg-type]


async def test_the_summaries_cover_the_own_queues_in_reach_with_waiting_and_staff(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    waiting = await _book(uow, clock)
    await _book(uow, clock, SLOT_0930)
    await _book(uow, clock, SLOT_0900, "andere")
    clock.advance(SLOT_0900 - timedelta(minutes=10) - clock.now())
    uow.state.call_status[waiting] = CallStatus(
        waiting, participants=1, updated_at=clock.now() - timedelta(minutes=4)
    )
    demo_team, now = Membership("agent", "demo", "demo"), clock.now()
    uow.state.staff = {
        "a-1": _staff("a-1", frozenset({Role.AGENT}), now, demo_team),
        "a-9": _staff("a-9", frozenset({Role.AGENT}), now, demo_team, last_logout_at=now),
        "u-1": _staff("u-1", frozenset({Role.AUDITOR}), now, Membership("auditor", "demo")),
        "a-3": _staff("a-3", frozenset({Role.AGENT}), now, Membership("agent", "demo", "andere")),
        "a-0": _staff("a-0", frozenset({Role.AGENT}), now - timedelta(hours=9), demo_team),
    }

    summaries = await queue.summaries(GROUP_ADMIN, timedelta(hours=8))

    assert [s.summary.group.slug for s in summaries] == ["demo"]
    assert summaries[0].waiting == 1
    assert summaries[0].longest_wait == timedelta(minutes=4)
    assert summaries[0].staff_available == 1


async def test_the_summaries_count_the_bookings_not_picked_up_in_time(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """ADR 0043 point 8: the overview shows how many nobody claimed in time."""
    await _book(uow, clock)
    await _book(uow, clock, SLOT_0930)
    clock.advance(SLOT_0900 + timedelta(minutes=11) - clock.now())

    [summary] = await queue.summaries(GROUP_ADMIN, timedelta(hours=8))

    assert summary.not_picked_up == 1


async def test_the_summaries_leave_out_contact_center_groups_and_groups_outside_reach(
    queue: QueueService, uow: InMemoryUnitOfWork
) -> None:
    everything = await queue.summaries(
        principal("s", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset()),
        timedelta(hours=8),
    )

    assert sorted(s.summary.group.slug for s in everything) == ["andere", "demo"]
    assert await queue.summaries(AUDITOR, timedelta(hours=8)) == []
    own = await queue.summaries(OTHER_TEAM, timedelta(hours=8))
    assert [s.summary.group.slug for s in own] == ["andere"]


async def test_claims_made_before_a_token_key_rotation_still_read_as_mine(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """ADR 0094 point 4: agent pseudonyms are compared under both keys."""
    old_key, new_key = b"o" * 32, b"n" * 32
    before = QueueService(uow, clock, AdminService(uow, clock), old_key)
    booking_id = await _book(uow, clock)
    clock.advance(SLOT_0900 - timedelta(minutes=10) - clock.now())
    await before.claim_next(AGENT, "demo")

    rotated = QueueService(uow, clock, AdminService(uow, clock), new_key, old_key)
    forgotten = QueueService(uow, clock, AdminService(uow, clock), new_key)

    assert [r.mine for r in (await rotated.view(AGENT, "demo")).rows] == [True]
    assert [r.mine for r in (await forgotten.view(AGENT, "demo")).rows] == [False]
    assert uow.state.bookings[booking_id].claim is not None


async def test_a_fallback_queue_hands_out_only_the_bookings_that_fell_back(
    queue: QueueService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    """ADR 0082 point 12: a booking still at the contact center is never claimable by id."""
    from app.domain.scheduling import DispatchFallback

    agent = principal("a-9", "W", frozenset({Role.AGENT}), frozenset(), [("demo", "wxcc")])
    uow.state.groups[3] = replace(
        uow.state.groups[3],
        dispatch_fallback=DispatchFallback.OWN_QUEUE,
        queue_free_pick_allowed=True,
    )
    at_contact_center = await _book(uow, clock, slug="wxcc")
    fell_back = await _book(uow, clock, SLOT_0930, slug="wxcc")
    uow.state.call_status[fell_back] = CallStatus(fell_back, fallback=True)
    clock.advance(SLOT_0900 - clock.now())

    with pytest.raises(DomainError) as excinfo:
        await queue.claim(agent, "wxcc", at_contact_center)
    assert excinfo.value.code is ErrorCode.BOOKING_NOT_FOUND
    assert (await queue.claim(agent, "wxcc", fell_back)).booking_id == fell_back
