import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.engine import BEGIN_MODE_OPTION, create_engine
from app.adapters.db.models import BookingRow, JobRow, SlotRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import BookingStatus
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import JobType
from app.domain.privacy import PII_FIELDS
from app.domain.services import BookingService
from tests.adapters.db.conftest import close_day, seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import (
    MONDAY,
    NOW,
    SLOT_0900,
    SLOT_0930,
    TEST_TOKEN_KEY,
    TUESDAY_0900,
    book,
    citizen,
)


@pytest.fixture
def service(engine: AsyncEngine) -> BookingService:
    return BookingService(SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY)


@asynccontextmanager
async def hold_write_lock(engine: AsyncEngine) -> AsyncIterator[None]:
    """Hold SQLite's write lock on a separate connection for the duration of the block."""
    async with engine.connect() as raw:
        conn = await raw.execution_options(**{BEGIN_MODE_OPTION: "IMMEDIATE"})
        await conn.begin()
        await conn.exec_driver_sql("SELECT 1")
        yield
        await conn.rollback()


async def count(engine: AsyncEngine, statement) -> int:  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        return int((await conn.execute(statement)).scalar_one())


async def test_book_persists_slot_booking_and_jobs(
    engine: AsyncEngine, service: BookingService
) -> None:
    await seed_group(engine)

    receipt = await book(service)

    found = (await service.find(receipt.management_token)).booking
    assert found.id == receipt.booking.id
    assert found.start == SLOT_0900
    assert found.start.tzinfo is UTC
    assert found.contact is not None
    assert found.contact.phone == "+31612345678"
    assert found.status is BookingStatus.CONFIRMED
    async with engine.connect() as conn:
        stored = (await conn.execute(select(BookingRow.management_token_hash))).scalar_one()
        jobs = (
            await conn.execute(select(JobRow.type).order_by(JobRow.run_at, JobRow.id))
        ).scalars()
        job_types = list(jobs)
    assert stored == found.token_hash
    assert receipt.management_token not in stored
    assert job_types[0] == JobType.CREATE_VIDEO_ROOM
    assert JobType.SEND_REMINDER in job_types
    assert job_types[-1] == JobType.PURGE_PII


async def test_availability_reflects_bookings(engine: AsyncEngine, service: BookingService) -> None:
    await seed_group(engine, capacity=2)
    await book(service)

    slots = await service.availability("aangifte", MONDAY, MONDAY)

    assert [(s.start, s.remaining) for s in slots] == [(SLOT_0900, 1), (SLOT_0930, 2)]


async def test_full_slot_and_cancel_frees_it(engine: AsyncEngine, service: BookingService) -> None:
    await seed_group(engine)
    receipt = await book(service)

    with pytest.raises(DomainError, match="slot_full"):
        await book(service)
    cancelled = await service.cancel(receipt.management_token)
    await book(service)

    assert cancelled.status is BookingStatus.CANCELLED
    assert cancelled.cancelled_at == NOW
    async with engine.connect() as conn:
        jobs = (
            await conn.execute(select(JobRow.type).where(JobRow.booking_id == receipt.booking.id))
        ).scalars()
        assert sorted(jobs) == sorted(
            [
                JobType.END_WXCC_TASK,
                JobType.DELETE_VIDEO_ROOM,
                JobType.DELETE_TRIAL_CALL,
                JobType.SEND_CANCELLATION,
                JobType.PURGE_PII,
            ]
        )
    assert await count(engine, select(func.count()).select_from(SlotRow)) == 1


async def test_reschedule_and_rollback(engine: AsyncEngine, service: BookingService) -> None:
    await seed_group(engine)
    first = await book(service)
    moved = await service.reschedule(first.management_token, SLOT_0930)
    second = await book(service)  # 09:00 is free again

    with pytest.raises(DomainError, match="slot_full"):
        await service.reschedule(second.management_token, SLOT_0930)

    assert (await service.find(second.management_token)).booking.status is BookingStatus.CONFIRMED
    assert (
        await service.find(moved.management_token)
    ).booking.rescheduled_from_id == first.booking.id
    assert (await service.find(first.management_token)).booking.status is BookingStatus.CANCELLED


async def test_reschedule_into_another_groups_slot(
    engine: AsyncEngine, service: BookingService
) -> None:
    await seed_group(engine, slug="aangifte", weekday=0)
    await seed_group(engine, slug="toeslagen", weekday=1)
    receipt = await book(service)

    with pytest.raises(DomainError, match="slot_unavailable"):
        await service.reschedule(receipt.management_token, TUESDAY_0900)

    assert (await service.find(receipt.management_token)).booking.status is BookingStatus.CONFIRMED


async def test_closed_day_is_respected(engine: AsyncEngine, service: BookingService) -> None:
    group_id = await seed_group(engine)
    await close_day(engine, group_id, MONDAY)

    with pytest.raises(DomainError, match="slot_unavailable"):
        await book(service)
    assert await service.availability("aangifte", MONDAY, MONDAY) == []


async def test_concurrent_bookings_on_separate_connections(
    engine: AsyncEngine, database_url: str
) -> None:
    await seed_group(engine)
    other_engine = create_engine(database_url, busy_timeout_ms=5000)
    services = [
        BookingService(SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY),
        BookingService(SqlUnitOfWork(other_engine), FixedClock(NOW), TEST_TOKEN_KEY),
    ]
    try:
        results = await asyncio.gather(*(book(s) for s in services), return_exceptions=True)
    finally:
        await other_engine.dispose()

    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert [getattr(f, "code", f) for f in failures] == [ErrorCode.SLOT_FULL]
    assert await count(engine, select(func.count()).select_from(BookingRow)) == 1


async def test_lock_timeout_becomes_database_busy(
    engine: AsyncEngine, database_url: str, service: BookingService
) -> None:
    await seed_group(engine)
    blocker = create_engine(database_url)
    try:
        async with hold_write_lock(blocker):
            with pytest.raises(DomainError) as excinfo:
                await book(service)
    finally:
        await blocker.dispose()

    assert excinfo.value.code is ErrorCode.DATABASE_BUSY


async def test_reads_are_not_blocked_by_a_writer(
    engine: AsyncEngine, database_url: str, service: BookingService
) -> None:
    await seed_group(engine)
    blocker = create_engine(database_url)
    try:
        async with hold_write_lock(blocker):
            slots = await service.availability("aangifte", MONDAY, MONDAY)
    finally:
        await blocker.dispose()

    assert len(slots) == 2


async def test_slot_uniqueness_is_enforced_by_the_database(engine: AsyncEngine) -> None:
    group_id = await seed_group(engine)
    insert_slot = text(
        'INSERT INTO slots (group_id, start, "end", capacity_snapshot) '
        "VALUES (:g, '2026-01-05 08:00:00.000000', '2026-01-05 08:30:00.000000', 1)"
    )
    async with engine.begin() as conn:
        await conn.execute(insert_slot, {"g": group_id})
    with pytest.raises(Exception, match="UNIQUE"):
        async with engine.begin() as conn:
            await conn.execute(insert_slot, {"g": group_id})


async def test_salt_is_stored_and_purge_clears_personal_data(
    engine: AsyncEngine, service: BookingService
) -> None:
    from app.domain.tokens import derive_token

    await seed_group(engine)
    receipt = await book(service)
    uow = SqlUnitOfWork(engine)

    async with uow.read() as tx:
        stored = await tx.get_booking(receipt.booking.id or 0)
    assert stored is not None
    assert derive_token(TEST_TOKEN_KEY, stored.token_salt) == receipt.management_token

    async with uow.locked() as tx:
        await tx.purge_personal_data(stored.id or 0, NOW)
    async with uow.read() as tx:
        purged = await tx.get_booking(stored.id or 0)
        assert await tx.get_booking(9999) is None
    assert purged is not None
    assert purged.contact is None
    # ADR 0063: every personal-data column of the one list is cleared, whatever is added later.
    columns = [
        getattr(BookingRow, name) for name in sorted(PII_FIELDS) if hasattr(BookingRow, name)
    ]
    assert len(columns) == 8
    async with engine.connect() as conn:
        row = (await conn.execute(select(*columns, BookingRow.pii_purged_at))).one()
    assert row[:-1] == (None,) * len(columns)
    assert row[-1] == NOW


async def test_upcoming_starts_for_email_counts_place_holding_bookings_only(
    engine: AsyncEngine, service: BookingService
) -> None:
    group_id = await seed_group(engine, capacity=3)
    await seed_group(engine, slug="toeslagen", capacity=3)
    kept = await book(service)
    cancelled = await book(service, SLOT_0930, contact=citizen(2))
    await service.cancel(cancelled.management_token)
    await book(service, SLOT_0930, contact=citizen(3))
    await book(service, SLOT_0930, "toeslagen")

    async with SqlUnitOfWork(engine).read() as tx:
        ada = await tx.upcoming_starts_for_email(group_id, "ADA@example.ORG", NOW)
        gone = await tx.upcoming_starts_for_email(group_id, "citizen2@example.org", NOW)
        ended = await tx.upcoming_starts_for_email(group_id, "ada@example.org", SLOT_0930)

    assert ada == [kept.booking.start]
    assert gone == []
    assert ended == []


async def test_a_trial_call_is_stored_counted_cleared_and_its_link_purged(
    engine: AsyncEngine, service: BookingService
) -> None:
    from app.domain.booking import MeetingRef, TrialCall

    await seed_group(engine)
    receipt = await book(service)
    booking_id = receipt.booking.id or 0
    uow = SqlUnitOfWork(engine)
    trial = TrialCall(MeetingRef("trial-1", "https://x.webex.com/t/1"), NOW)

    async with uow.locked() as tx:
        await tx.save_trial_call(booking_id, trial)
    async with uow.read() as tx:
        stored = await tx.get_booking(booking_id)
    assert stored is not None
    assert stored.trial == trial
    assert stored.trial_count == 1
    assert "https://x.webex.com/t/1" not in repr(stored)  # a link is personal data

    async with uow.locked() as tx:
        await tx.clear_trial_call(booking_id)
        await tx.save_trial_call(booking_id, trial)
        await tx.purge_personal_data(booking_id, NOW)
    async with uow.read() as tx:
        purged = await tx.get_booking(booking_id)
    assert purged is not None
    assert purged.trial_count == 2  # clearing never gives a fresh allowance
    async with engine.connect() as conn:
        link = (
            await conn.execute(select(BookingRow.trial_join_url).where(BookingRow.id == booking_id))
        ).scalar_one()
    assert link is None
