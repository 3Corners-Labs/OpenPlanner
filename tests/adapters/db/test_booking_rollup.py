"""The booking row rollup against SQLite: counts added, rows and their timeline removed."""

from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.base import Base
from app.adapters.db.models import (
    BookingDayCountRow,
    BookingEventRow,
    BookingRow,
    JobRow,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import BookingStatus
from app.domain.booking_rollup_service import BookingRowRollup
from app.domain.calls import BookingEvent, EventKind
from app.domain.job_plan import JobType, PlannedJob
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, SLOT_0900, SLOT_0930, TEST_TOKEN_KEY, book

DAY = timedelta(days=1)
TERM = timedelta(days=730)


async def _purged(engine: AsyncEngine, service: BookingService, start: datetime = SLOT_0900) -> int:
    booking_id = (await book(service, start)).booking.id or 0
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.add_booking_event(BookingEvent(booking_id, NOW, EventKind.MEETING_FAILED, "x"))
        await tx.purge_personal_data(booking_id, NOW)
        await tx.delete_pending_jobs(booking_id, set(JobType))
    return booking_id


async def _count(engine: AsyncEngine, table: type[Base]) -> int:
    async with AsyncSession(engine) as session:
        return int(await session.scalar(select(func.count()).select_from(table)) or 0)


async def test_rows_become_a_day_count_and_take_their_events_along(engine: AsyncEngine) -> None:
    await seed_group(engine, capacity=2)
    clock = FixedClock(NOW)
    uow = SqlUnitOfWork(engine)
    service = BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0)
    first = await _purged(engine, service, SLOT_0900)
    await _purged(engine, service, SLOT_0930)
    group_id = (await book(service, SLOT_0900)).booking.group_id  # a live row, not purged
    clock.advance(TERM + DAY)
    rollup = BookingRowRollup(uow, clock, lambda: TERM)

    async with uow.read() as tx:
        before = await tx.booking_totals(group_id)
    result = await rollup.run()
    again = await rollup.run()

    assert (result.rows, result.day_counts, again.rows) == (2, 1, 0)
    assert await _count(engine, BookingRow) == 1
    assert await _count(engine, BookingEventRow) == 0
    async with AsyncSession(engine) as session:
        [row] = (await session.scalars(select(BookingDayCountRow))).all()
        assert first not in (await session.scalars(select(BookingRow.id))).all()
    assert (row.count, row.status, row.last_purged_at) == (2, "confirmed", NOW)
    async with uow.read() as tx:
        assert await tx.booking_totals(group_id) == before == {BookingStatus.CONFIRMED: 3}


async def test_a_later_run_adds_to_the_same_day(engine: AsyncEngine) -> None:
    await seed_group(engine, capacity=2)
    clock = FixedClock(NOW)
    uow = SqlUnitOfWork(engine)
    service = BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0)
    rollup = BookingRowRollup(uow, clock, lambda: TERM)
    await _purged(engine, service)
    later = await _purged(engine, service)
    async with engine.begin() as conn:
        await conn.execute(
            update(BookingRow).where(BookingRow.id == later).values(pii_purged_at=NOW + 10 * DAY)
        )
    clock.advance(TERM + DAY)
    assert (await rollup.run()).rows == 1
    clock.advance(20 * DAY)

    assert (await rollup.run()).rows == 1

    async with AsyncSession(engine) as session:
        [row] = (await session.scalars(select(BookingDayCountRow))).all()
    assert (row.count, row.last_purged_at) == (2, NOW + 10 * DAY)


async def test_a_row_with_an_open_job_or_data_stays(engine: AsyncEngine) -> None:
    await seed_group(engine, capacity=2)
    clock = FixedClock(NOW)
    uow = SqlUnitOfWork(engine)
    service = BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0)
    holding = (await book(service, SLOT_0900)).booking.id or 0  # still has its data and jobs
    busy = await _purged(engine, service, SLOT_0930)
    async with engine.begin() as conn:
        await conn.execute(update(BookingRow).values(pii_purged_at=NOW - 2 * TERM))
        await conn.execute(
            update(BookingRow).where(BookingRow.id == busy).values(management_token_hash="x" * 64)
        )
    async with uow.locked() as tx:
        await tx.replace_jobs(busy, [_job()])
    clock.advance(TERM)

    result = await BookingRowRollup(uow, clock, lambda: TERM).run()

    assert (result.rows, result.skipped) == (0, 2)
    assert await _count(engine, BookingRow) == 2
    assert await _count(engine, BookingDayCountRow) == 0
    assert await _count(engine, JobRow) > 0
    assert holding


def _job() -> PlannedJob:
    return PlannedJob(JobType.PURGE_PII, NOW, None)
