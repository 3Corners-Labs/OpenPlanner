"""A pool booking and its request in storage, for the SQL-and-fake request tests (ADR 0121).

Shared by ``tests/adapters/db/test_service_requests.py`` (step 5) and
``tests/adapters/db/test_contact_center_attempts.py`` (step 6b): a pool flow of Acme's service,
one confirmed booking on its pool at 09:00, and the new request of that booking.
"""

from datetime import timedelta

from app.domain.booking import BookingKind, Slot
from app.domain.flow_resolution import FlowRef
from app.domain.service_requests import ServiceRequest, appointment_request
from tests.support.closures import confirmed_booking
from tests.support.domain import NOW, SLOT_0900
from tests.support.flow_world import World
from tests.support.pool_flows import PoolFlow, seed_pool_flow

LENGTH = timedelta(minutes=30)
UNTIL = SLOT_0900 + LENGTH + timedelta(minutes=5)


async def pool_booking(world: World, number: int = 1) -> tuple[PoolFlow, int]:
    """Seed a pool flow of Acme's service and one confirmed booking on its pool; return both."""
    flow = await seed_pool_flow(world.uow, world.acme, world.acme_group)
    slot = Slot(
        None,
        world.acme_group,
        SLOT_0900,
        SLOT_0900 + LENGTH,
        2,
        BookingKind.SCHEDULED,
        flow.pool_id,
        UNTIL,
    )
    async with world.uow.locked() as tx:
        stored = await tx.add_slot(slot)
        booking = await tx.add_booking(
            confirmed_booking(
                id=None,
                group_id=world.acme_group,
                slot_id=stored.id,
                start=stored.start,
                end=stored.end,
                token_hash=f"{number:064d}",
                public_ref=f"ref{number:020d}",
                flow=FlowRef(flow.flow_id, 1),
            )
        )
    return flow, int(booking.id)


def request_for(world: World, flow: PoolFlow, booking_id: int) -> ServiceRequest:
    """Return the new, queued request of ``booking_id``."""
    return appointment_request(
        organisation_id=world.acme,
        group_id=world.acme_group,
        flow=FlowRef(flow.flow_id, 1),
        booking_id=booking_id,
        booking_end=SLOT_0900 + LENGTH,
        now=NOW,
        unresolved_hours=24,
    )
