"""Pool blocks, pools, pool slots and bookings for the adapter tests of ADR 0122."""

from datetime import time, timedelta
from typing import Any

from app.domain.booking import Booking, BookingKind, Slot
from app.domain.capacity import CapacityPool
from app.domain.scheduling import Offer, ScheduleRule, TimeWindow
from tests.support.closures import confirmed_booking
from tests.support.domain import SLOT_0900
from tests.support.flow_world import EVERYTHING, World, add_pool

AMSTERDAM = "Europe/Amsterdam"


def block(opens: int = 9, closes: int = 12, units: int = 2, offer: Offer = Offer.SCHEDULED) -> Any:
    """Return a Monday block ``opens`` to ``closes`` o'clock with ``units`` people."""
    return ScheduleRule(0, TimeWindow(time(opens), time(closes), units, "Balie", offer))


async def staffed_pool(world: World, name: str = "Loket") -> CapacityPool:
    """Store a counted pool of Acme with a time zone."""
    pool = await add_pool(world, name)
    async with world.uow.locked() as tx:
        assert await tx.set_pool_timezone(EVERYTHING, pool.id, AMSTERDAM)
    return pool


def pool_slot(world: World, pool: CapacityPool, minutes: int = 30, buffer: int = 5) -> Slot:
    """Return a pool slot of Acme's group at Monday 09:00 CET: ``minutes`` plus a wrap-up."""
    end = SLOT_0900 + timedelta(minutes=minutes)
    return Slot(
        None,
        world.acme_group,
        SLOT_0900,
        end,
        2,
        BookingKind.SCHEDULED,
        pool.id,
        end + timedelta(minutes=buffer),
    )


async def book_on(world: World, slot: Slot, number: int, **changes: Any) -> Booking:
    """Store a booking in ``slot`` with its own token hash and reference."""
    booking = confirmed_booking(
        **{
            "id": None,
            "group_id": slot.group_id,
            "slot_id": slot.id,
            "start": slot.start,
            "end": slot.end,
            "token_hash": f"{number:064d}",
            "public_ref": f"ref{number:020d}",
            **changes,
        }
    )
    async with world.uow.locked() as tx:
        return await tx.add_booking(booking)
