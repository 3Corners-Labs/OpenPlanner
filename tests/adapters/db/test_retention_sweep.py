"""The retention sweep against SQLite: what is selected, purged and left (WP-A1)."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import AuditEventRow, BookingRow, JobRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import BookingStatus
from app.domain.retention_sweep import RetentionSweep
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import (
    ADA,
    NOW,
    SLOT_0900,
    SLOT_0930,
    TEST_TOKEN_KEY,
    book,
    book_pending,
)

DAY = timedelta(hours=24)


async def _holding(engine: AsyncEngine) -> dict[int, bool]:
    async with AsyncSession(engine) as session:
        rows = await session.execute(select(BookingRow.id, BookingRow.email))
        return {booking_id: email is not None for booking_id, email in rows}


async def test_overdue_bookings_are_purged_and_their_jobs_dropped(engine: AsyncEngine) -> None:
    await seed_group(engine, capacity=2)
    clock = FixedClock(NOW)
    uow = SqlUnitOfWork(engine)
    service = BookingService(uow, clock, TEST_TOKEN_KEY)
    confirmed = (await book(service, SLOT_0900)).booking.id or 0
    pending = (await book_pending(service, SLOT_0930, "aangifte")).booking
    assert pending.status is BookingStatus.PENDING_CONFIRMATION
    assert pending.confirm_by is not None
    clock.advance(pending.confirm_by - NOW + DAY + timedelta(hours=2))  # before any slot

    result = await RetentionSweep(uow, clock).run()

    assert (result.overdue, result.purged, result.failed) == (1, 1, 0)
    assert await _holding(engine) == {confirmed: True, pending.id: False}
    async with AsyncSession(engine) as session:
        left = (await session.execute(select(JobRow.booking_id))).scalars().all()
        events = (await session.execute(select(AuditEventRow))).scalars().all()
    assert pending.id not in left
    assert [(e.action, e.detail) for e in events] == [
        ("retention.sweep", "overdue:1,purged:1,failed:0,backlog:0")
    ]


async def test_a_second_sweep_finds_nothing(engine: AsyncEngine) -> None:
    await seed_group(engine)
    clock = FixedClock(NOW)
    uow = SqlUnitOfWork(engine)
    await book(BookingService(uow, clock, TEST_TOKEN_KEY), SLOT_0900)
    clock.advance(SLOT_0900 - NOW + 3 * DAY)
    sweep = RetentionSweep(uow, clock)

    first, second = await sweep.run(), await sweep.run()

    assert (first.purged, first.backlog) == (1, 1)
    assert (second.overdue, second.purged) == (0, 0)
    assert ADA.email not in repr(first)
