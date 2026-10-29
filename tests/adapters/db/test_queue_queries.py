"""Claims of the own queue in SQL (ADR 0043)."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import Claim
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, TEST_TOKEN_KEY, book


async def _booking_id(engine: AsyncEngine) -> int:
    await seed_group(engine)
    receipt = await book(BookingService(SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY))
    return receipt.booking.id or 0


async def test_only_the_first_claim_wins_and_a_release_reopens(engine: AsyncEngine) -> None:
    booking_id = await _booking_id(engine)
    uow = SqlUnitOfWork(engine)

    async with uow.locked() as tx:
        first = await tx.claim_booking(booking_id, Claim(NOW, "agent-a"))
        second = await tx.claim_booking(booking_id, Claim(NOW, "agent-b"))
    async with uow.read() as tx:
        claimed = await tx.get_booking(booking_id)
    async with uow.locked() as tx:
        await tx.release_claim(booking_id)
        again = await tx.claim_booking(booking_id, Claim(NOW, "agent-b"))

    assert (first, second, again) == (True, False, True)
    assert claimed is not None
    assert claimed.claim == Claim(NOW, "agent-a")


async def test_a_cancelled_booking_cannot_be_claimed(engine: AsyncEngine) -> None:
    booking_id = await _booking_id(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        booking = await tx.get_booking(booking_id)
        assert booking is not None
        await tx.save_booking(booking.cancel(NOW))

    async with uow.locked() as tx:
        claimed = await tx.claim_booking(booking_id, Claim(NOW, "agent-a"))

    assert not claimed


async def test_the_purge_removes_the_agent_pseudonym(engine: AsyncEngine) -> None:
    booking_id = await _booking_id(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.claim_booking(booking_id, Claim(NOW, "agent-a"))
        await tx.purge_personal_data(booking_id, NOW + timedelta(days=2))
    async with uow.read() as tx:
        booking = await tx.get_booking(booking_id)

    assert booking is not None
    assert booking.claim is None
