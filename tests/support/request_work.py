"""A demo service with a staffed counted pool and request work, for the step 5b tests.

ADR 0121 step 5b: a flow without video on a counted pool of one unit (Mondays 09:00–12:00
Amsterdam), an appointment of 30 minutes plus 5 minutes wrap-up at 09:00, the queue with request
work wired as the application does, and a flow with video on the same pool that is not request
work.
"""

from dataclasses import replace
from datetime import time, timedelta

from app.domain.admin import AdminService
from app.domain.authorization import Role
from app.domain.booking import BookingKind, MeetingRef, Slot
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.flow_resolution import FlowRef
from app.domain.pool_booking import PoolBookings
from app.domain.queue_service import QueueService
from app.domain.request_work import RequestWork
from app.domain.scheduling import DispatchKind, Offer, Organisation, ScheduleRule, TimeWindow
from app.domain.service_flows import InteractionKind
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import (
    ADA,
    NOW,
    SLOT_0900,
    TEST_TOKEN_KEY,
    book_confirmed,
    make_group,
    window,
)
from tests.support.principals import principal
from tests.support.service_flows import revision

POOL = 7
FLOW = FlowRef(50, 1)
VIDEO_FLOW = FlowRef(52, 1)
AGENT = principal("a-1", "A", [Role.AGENT], groups=["demo"])
OTHER = principal("a-2", "B", [Role.AGENT], groups=["demo"])
ADMIN = principal("g-1", "G", [Role.GROUP_ADMIN], groups=["demo"])
LENGTH = timedelta(minutes=30)


def unit_block(units: int) -> ScheduleRule:
    """Return the Monday block of the pool: ``units`` people from 09:00 to 12:00."""
    return ScheduleRule(0, TimeWindow(time(9), time(12), units, None, Offer.BOTH))


def world(units: int = 1) -> InMemoryUnitOfWork:
    """Return the store: the demo service with an own queue and a staffed counted pool."""
    state = State(
        organisation_slugs={1: "demo"}, organisations={1: Organisation(1, "Demo", "demo")}
    )
    state.groups[1] = make_group(
        id=1,
        organisation_id=1,
        slug="demo",
        dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
        queue_free_pick_allowed=True,
        queue_visible_from_minutes=60,
    )
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=5))]
    state.pools[POOL] = CapacityPool(POOL, 1, "Loket", PoolMode.COUNTED, code="loket")
    state.pool_timezones[POOL] = "Europe/Amsterdam"
    state.pool_rules[1] = (POOL, replace(unit_block(units), id=1))
    state.flow_revisions[(50, 1)] = revision(
        flow_id=50,
        capacity_pool_id=POOL,
        duration_minutes=30,
        interaction_kind=InteractionKind.NONE,
        published_at=NOW,
    )
    state.flow_revisions[(52, 1)] = revision(
        flow_id=52, capacity_pool_id=POOL, interaction_kind=InteractionKind.VIDEO, published_at=NOW
    )
    state.next_id = 100
    return InMemoryUnitOfWork(state)


def queue_of(uow: InMemoryUnitOfWork, clock: FixedClock) -> QueueService:
    """Return the queue with request work wired as the application does."""
    work = RequestWork(
        PoolBookings(clock, treatment_buffer=5), lease_minutes=5, unresolved_hours=24
    )
    return QueueService(uow, clock, AdminService(uow, clock), b"k" * 32, work=work)


async def book(uow: InMemoryUnitOfWork, clock: FixedClock, flow: FlowRef | None = FLOW) -> int:
    """Book a confirmed appointment at 09:00; with a flow it is moved onto a pool slot.

    Each booking has its own address (one address books a slot once, ADR 0066).
    """
    who = replace(ADA, email=f"p{len(uow.state.bookings)}@voorbeeld.nl")
    receipt = await book_confirmed(
        booking_service(uow, clock), group_slug="demo", start=SLOT_0900, contact=who, locale="nl"
    )
    booking_id = receipt.booking.id or 0
    booking = uow.state.bookings[booking_id]
    if flow is None:
        uow.state.bookings[booking_id] = replace(booking, meeting=MeetingRef("m", "https://v/j"))
        return booking_id
    slot = Slot(
        900 + booking_id,
        1,
        SLOT_0900,
        SLOT_0900 + LENGTH,
        1,
        BookingKind.SCHEDULED,
        POOL,
        SLOT_0900 + LENGTH + timedelta(minutes=5),
    )
    uow.state.slots[slot.id or 0] = slot
    uow.state.bookings[booking_id] = replace(booking, slot_id=slot.id or 0, end=slot.end, flow=flow)
    return booking_id


def booking_service(uow: InMemoryUnitOfWork, clock: FixedClock) -> BookingService:
    """Return the booking service that makes the appointment (its own staffing, then moved)."""
    return BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0)


def at(clock: FixedClock, minutes: int = 0) -> None:
    """Move the clock to 09:00 Amsterdam plus ``minutes``."""
    clock.advance(SLOT_0900 + timedelta(minutes=minutes) - clock.now())
