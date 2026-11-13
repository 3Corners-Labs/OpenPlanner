"""A demo service with immediate intake and check-in on one counted pool (ADR 0121 step 7).

The pool ``loket`` has two units on Mondays 09:00–12:00 Amsterdam, offering both appointments
and work without appointment. Two flows draw on it: ``nu`` (no video, the own queue, 20 minutes,
the default of the on-demand path, also bookable) and ``balie`` (in person with an explicit
check-in, 30 minutes). Appointments are placed on the pool directly, as the booking path of ADR
0122 step 5 would, so the brief's 09:55 example (ADR 0122 section 2.4) can be set up exactly.
"""

from dataclasses import replace
from datetime import datetime, time, timedelta

from app.domain.admin import AdminService
from app.domain.authorization import Role
from app.domain.booking import BookingKind, Slot
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.flow_resolution import FlowRef
from app.domain.immediate_requests import Accepted, ImmediateRequests
from app.domain.pool_booking import PoolBookings
from app.domain.queue_service import QueueService
from app.domain.request_queue import RequestQueue
from app.domain.request_watch import RequestWatch
from app.domain.request_work import RequestWork
from app.domain.scheduling import DispatchKind, Offer, Organisation, ScheduleRule, TimeWindow
from app.domain.service_flows import (
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ServiceFlow,
)
from app.domain.services import BookingService, ContactInput
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import ADA, SLOT_0900, TEST_TOKEN_KEY, book_confirmed, make_group, window
from tests.support.principals import principal
from tests.support.service_flows import revision

POOL = 7
NOW_FLOW = FlowRef(60, 1)
DESK_FLOW = FlowRef(61, 1)
AGENT = principal("a-1", "A", [Role.AGENT], groups=["demo"])
COLLEAGUE = principal("a-2", "B", [Role.AGENT], groups=["demo"])
ADMIN = principal("g-1", "G", [Role.GROUP_ADMIN], groups=["demo"])
KEY = "k" * 43
"""An intake form's key, as ``new_intake_key`` makes them (43 characters)."""
GUEST = ContactInput(first_name="Ada", last_name="Lovelace", email="ada@voorbeeld.nl")


def at(hour: int, minute: int = 0) -> datetime:
    """Return Monday ``hour:minute`` Amsterdam (CET) as UTC."""
    return SLOT_0900 + timedelta(hours=hour - 9, minutes=minute)


def world(units: int = 2, offer: Offer = Offer.BOTH) -> InMemoryUnitOfWork:
    """Return the store: the demo service, the staffed pool and the two flows."""
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
    state.groups[2] = make_group(id=2, organisation_id=1, slug="andere", name="Andere")
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=5))]
    state.pools[POOL] = CapacityPool(POOL, 1, "Loket", PoolMode.COUNTED, code="loket")
    state.pool_timezones[POOL] = "Europe/Amsterdam"
    block = ScheduleRule(0, TimeWindow(time(9), time(12), units, None, offer), id=1)
    state.pool_rules[1] = (POOL, block)
    now_revision = revision(
        flow_id=NOW_FLOW.flow_id,
        intake_modes=frozenset({IntakeMode.ON_DEMAND, IntakeMode.APPOINTMENT}),
        interaction_kind=InteractionKind.NONE,
        handling_action=HandlingAction.VIEW_REQUEST,
        duration_minutes=20,
        capacity_pool_id=POOL,
        published_at=SLOT_0900,
    )
    desk_revision = revision(
        flow_id=DESK_FLOW.flow_id,
        interaction_kind=InteractionKind.IN_PERSON,
        handling_action=HandlingAction.VIEW_REQUEST,
        duration_minutes=30,
        capacity_pool_id=POOL,
        published_at=SLOT_0900,
    )
    state.flow_revisions[(60, 1)] = now_revision
    state.flow_revisions[(61, 1)] = desk_revision
    state.flows[60] = ServiceFlow(
        60, 1, "nu", FlowState.ACTIVE, frozenset({IntakeMode.ON_DEMAND}), now_revision
    )
    state.flows[61] = ServiceFlow(61, 1, "balie", FlowState.ACTIVE, frozenset(), desk_revision)
    state.next_id = 100
    return InMemoryUnitOfWork(state)


def pools(clock: FixedClock, buffer: int = 0) -> PoolBookings:
    """Return the pool side of booking, with the organisation's wrap-up."""
    return PoolBookings(clock, treatment_buffer=buffer)


def intake(uow: InMemoryUnitOfWork, clock: FixedClock, max_hours: int = 24) -> ImmediateRequests:
    """Return the participants' side of immediate work, wired as the application does."""
    return ImmediateRequests(
        uow, clock, pools(clock), lookahead_minutes=120, unresolved_hours=max_hours
    )


def staff(uow: InMemoryUnitOfWork, clock: FixedClock) -> RequestQueue:
    """Return the staff side of immediate work."""
    return RequestQueue(
        uow, clock, AdminService(uow, clock), pools(clock), b"k" * 32, lease_minutes=5
    )


def queue(uow: InMemoryUnitOfWork, clock: FixedClock) -> QueueService:
    """Return the appointments' queue with request work, as the application wires it."""
    work = RequestWork(pools(clock), lease_minutes=5, unresolved_hours=24)
    return QueueService(uow, clock, AdminService(uow, clock), b"k" * 32, work=work)


def watch(uow: InMemoryUnitOfWork, clock: FixedClock) -> RequestWatch:
    """Return the watchdog round."""
    return RequestWatch(uow, clock, 24)


async def ask(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    key: str = KEY,
    contact: ContactInput = GUEST,
) -> Accepted:
    """Take in an immediate request of the demo service now."""
    return await intake(uow, clock).accept(group_slug="demo", contact=contact, intake_key=key)


async def appointment(
    uow: InMemoryUnitOfWork, clock: FixedClock, start: datetime, flow: FlowRef, minutes: int
) -> int:
    """Place a confirmed appointment of ``flow`` on the pool at ``start``; return its id.

    The booking is made on the service's own staffing and moved onto a pool slot, as the pool
    booking path stores it (each with its own address, ADR 0066).
    """
    saved = clock.now()
    clock.advance(SLOT_0900 - timedelta(days=3) - clock.now())
    who = replace(ADA, email=f"p{len(uow.state.bookings)}@voorbeeld.nl")
    service = BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0)
    receipt = await book_confirmed(
        service, group_slug="demo", start=SLOT_0900, contact=who, locale="nl"
    )
    clock.advance(saved - clock.now())
    booking_id = receipt.booking.id or 0
    end = start + timedelta(minutes=minutes)
    slot = Slot(None, 1, start, end, 2, BookingKind.SCHEDULED, POOL, end)
    slot_id = 500 + booking_id
    uow.state.slots[slot_id] = replace(slot, id=slot_id)
    booking = uow.state.bookings[booking_id]
    uow.state.bookings[booking_id] = replace(
        booking, slot_id=slot_id, start=start, end=end, flow=flow
    )
    return booking_id
