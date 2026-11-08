"""A held proposal in SQLite: stored, counted in its slot while open, released (ADR 0057)."""

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import ChangeReason
from app.domain.booking_changes import ProposalTiming
from app.domain.errors import DomainError, ErrorCode
from app.domain.reschedule_proposal import ProposalOutcome
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import ADA, NOW, SLOT_0900, TEST_TOKEN_KEY, book, participant

NEXT_0900 = SLOT_0900 + timedelta(days=7)


async def test_an_open_proposal_takes_its_place_until_it_is_released(engine: AsyncEngine) -> None:
    await seed_group(engine, capacity=1)
    uow = SqlUnitOfWork(engine)
    clock = FixedClock(NOW)
    bookings = BookingService(uow, clock, TEST_TOKEN_KEY)
    receipt = await book(bookings, contact=ADA)
    booking_id = receipt.booking.id or 0
    async with uow.locked() as tx:
        booking = await tx.get_booking(booking_id)
        group = await tx.get_group_by_id(booking.group_id)  # type: ignore[union-attr]
        assert booking is not None
        assert group is not None
        await bookings.propose_in(
            tx,
            booking,
            group,
            NEXT_0900,
            reason=ChangeReason.STAFFING,
            timing=ProposalTiming(
                rebook_days=14, rebook_reminder_days=3, confirm_hours=48, reminder_hours=12
            ),
        )

    async with uow.read() as tx:
        held = await tx.get_open_proposal(booking_id)
        assert held is not None
        usage = {
            u.start: u.active
            for u in await tx.get_slot_usage(group.id, NOW, NEXT_0900 + timedelta(days=1))
        }
        taken = await tx.count_active_bookings(held.slot_id)
    assert (held.start, held.outcome) == (NEXT_0900, None)
    assert usage == {SLOT_0900: 0, NEXT_0900: 1}
    assert taken == 1
    with pytest.raises(DomainError) as caught:
        await book(bookings, NEXT_0900, contact=participant(2))
    assert caught.value.code is ErrorCode.SLOT_FULL

    async with uow.locked() as tx:
        await tx.save_proposal(held.released(NOW, ProposalOutcome.WITHDRAWN))
    async with uow.read() as tx:
        assert await tx.get_open_proposal(booking_id) is None
        assert await tx.count_active_bookings(held.slot_id) == 0
