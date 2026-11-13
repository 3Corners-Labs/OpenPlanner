"""One record of each kind only the gated paths write, for the rollback proof (gate item G07).

ADR 0121 point 9 and A30: before ``ROLLBACK_SAFE`` opens, the previous supported image must run
on a database that the new image wrote with the gate open, and book and claim there. The upgrade
matrix's ``rollback`` mode (``scripts/upgrade-matrix.sh``) writes these records with this
checkout through ``python -m app.cli seed-gated-records``, starts the previous image on the
database without a downgrade, and books on ``/g/demo`` there.

On the demonstration of ``demo`` (``seed-demonstration`` first), on the next weekday at 10:00
in the service's time zone:

* a booking of the in-person flow on the shared pool (a pool slot, the booking's flow);
* its request, claimed and started: work in treatment that occupies a unit of the pool;
* a second booking on the pool whose request has an open contact-center attempt (the shape of
  the record only: no flow of the fixture dispatches to the contact center yet, G13 to G15);
* an immediate request of the document review with its request-level personal data.

Written through the ``Transaction`` port and the pure rules the gated paths use
(``service_requests``, ``contact_center_attempts``, ``immediate_intake``), without their gate:
the gated paths refuse while ``ROLLBACK_SAFE`` is false, and the proof must be writable before
the image that opens it exists. That is why the command refuses production. A made-up
participant only; run it once, on a throwaway database.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.demonstration import (
    EVERYTHING,
    IN_PERSON_FLOW,
    REVIEW_FLOW,
    Demonstration,
)
from app.adapters.db.demonstration_stack import seed_demonstration_stack
from app.adapters.db.seed import (
    DEMO_SLUG,
    EXAMPLE_PARTICIPANT,
    FIRST_WEEKEND_DAY,
    seeded_group,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.domain import contact_center_attempts as attempts
from app.domain.booking import Booking, BookingKind, BookingStatus, Slot
from app.domain.flow_resolution import FlowRef
from app.domain.immediate_intake import new_access, new_intake_key
from app.domain.ports import Transaction
from app.domain.scheduling import Group
from app.domain.service_requests import (
    HandlingState,
    RequestPersonalData,
    ServiceRequest,
    appointment_request,
    claim,
    on_demand_request,
    start,
)
from app.domain.tokens import derive_token, hash_token, new_token_salt

AT = time(10)
"""Local start of the pool bookings: inside the shared pool's staffing and the demo's hours."""
LENGTH = timedelta(minutes=30)
"""The in-person flow's length (``demonstration.FLOWS``)."""
AGENT = "gated-records"
"""The pseudonymous agent of the started work; no staff member stands behind it."""
UNRESOLVED_HOURS = 24
"""The watchdog of the records: the default of ``request_unresolved_hours``."""


@dataclass(frozen=True)
class GatedRecords:
    """The ids of what was written.

    Attributes:
        pool_booking: The booking on the shared pool whose work is in treatment.
        started_request: Its request.
        attempt_request: The request of the second pool booking, with a contact-center attempt.
        immediate_request: The immediate request of the document review.
        day: The local date of the pool bookings (ISO), where the previous image books.
    """

    pool_booking: int
    started_request: int
    attempt_request: int
    immediate_request: int
    day: str


async def write_gated_records(engine: AsyncEngine, now: datetime, token_key: bytes) -> GatedRecords:
    """Seed the demonstration if needed, then write one record of each gated kind.

    Args:
        engine: A throwaway development database with ``seed-demo`` applied.
        now: The moment of writing (UTC).
        token_key: The management token key, for the bookings' management links.
    """
    ids = await seed_demonstration_stack(engine, now)
    async with SqlUnitOfWork(engine).locked() as tx:
        demo = await seeded_group(tx, DEMO_SLUG)
        begin = _next_weekday_at(demo, now)
        first = await _pool_booking(tx, demo, ids, begin, now, token_key)
        started = await _started(tx, demo, ids, first, now)
        second = await _pool_booking(tx, demo, ids, begin + LENGTH, now, token_key)
        attempt = await _attempt(tx, demo, ids, second, now)
        immediate = await _immediate(tx, demo, ids, now)
    day = begin.astimezone(demo.tz).date().isoformat()
    return GatedRecords(first.id or 0, started, attempt, immediate, day)


def _next_weekday_at(group: Group, now: datetime) -> datetime:
    """Return ``AT`` on the next weekday (tomorrow at the earliest) in the group's zone, in UTC."""
    day: date = group.today(now) + timedelta(days=1)
    while day.weekday() >= FIRST_WEEKEND_DAY:
        day += timedelta(days=1)
    return datetime.combine(day, AT, tzinfo=group.tz).astimezone(UTC)


async def _pool_booking(
    tx: Transaction,
    group: Group,
    ids: Demonstration,
    begin: datetime,
    now: datetime,
    token_key: bytes,
) -> Booking:
    """Store a confirmed booking of the in-person flow on its own slot of the shared pool."""
    end = begin + LENGTH
    slot = await tx.add_slot(
        Slot(None, group.id, begin, end, 2, BookingKind.SCHEDULED, ids.shared_pool, end)
    )
    salt = new_token_salt()
    return await tx.add_booking(
        Booking(
            id=None,
            group_id=group.id,
            slot_id=slot.id or 0,
            start=begin,
            end=end,
            status=BookingStatus.CONFIRMED,
            contact=EXAMPLE_PARTICIPANT,
            locale="nl",
            token_hash=hash_token(derive_token(token_key, salt)),
            created_at=now,
            token_salt=salt,
            flow=FlowRef(ids.flows[IN_PERSON_FLOW], 1),
        )
    )


async def _request(
    tx: Transaction, group: Group, ids: Demonstration, booking: Booking, now: datetime
) -> ServiceRequest:
    """Store the queued request of a pool booking."""
    return await tx.add_service_request(
        appointment_request(
            organisation_id=group.organisation_id,
            group_id=group.id,
            flow=FlowRef(ids.flows[IN_PERSON_FLOW], 1),
            booking_id=booking.id or 0,
            booking_end=booking.end,
            now=now,
            unresolved_hours=UNRESOLVED_HOURS,
        )
    )


async def _started(
    tx: Transaction, group: Group, ids: Demonstration, booking: Booking, now: datetime
) -> int:
    """Claim and start the booking's request: work in treatment on a unit of the pool."""
    request = await _request(tx, group, ids, booking, now)
    assigned, item = claim(request, None, now=now, agent=AGENT, lease_minutes=5)
    await tx.update_service_request(assigned, HandlingState.QUEUED)
    held = await tx.add_work_item(item)
    begun, working = start(
        assigned, held, now=now, pool_id=ids.shared_pool, planned_until=now + LENGTH, conflict=False
    )
    await tx.update_work_item(working, held.state)
    await tx.update_service_request(begun, assigned.handling)
    return request.id or 0


async def _attempt(
    tx: Transaction, group: Group, ids: Demonstration, booking: Booking, now: datetime
) -> int:
    """Open a contact-center attempt on the booking's request."""
    request = await _request(tx, group, ids, booking, now)
    assigned, item = attempts.open_attempt(request, None, now=now)
    await tx.update_service_request(assigned, HandlingState.QUEUED)
    await tx.add_work_item(item)
    return request.id or 0


async def _immediate(tx: Transaction, group: Group, ids: Demonstration, now: datetime) -> int:
    """Store a waiting immediate request of the document review with its personal data."""
    flow = await tx.get_service_flow(EVERYTHING, ids.flows[REVIEW_FLOW])
    if flow is None or flow.current is None:  # pragma: no cover - the stack seed published it
        raise ValueError("the document review has no published revision")
    access, _ = new_access(new_intake_key())
    request = on_demand_request(
        organisation_id=group.organisation_id,
        group_id=group.id,
        flow=FlowRef(flow.id or 0, flow.current.revision),
        pool_id=ids.own_pool,
        subject=None,
        access=access,
        now=now,
        unresolved_hours=UNRESOLVED_HOURS,
    )
    contact = EXAMPLE_PARTICIPANT
    data = RequestPersonalData(first_name=contact.first_name, last_name=contact.last_name)
    stored = await tx.add_service_request(request, data)
    return stored.id or 0
