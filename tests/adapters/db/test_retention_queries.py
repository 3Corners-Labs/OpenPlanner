"""The retention overview's queries in SQLite (ADR 0037)."""

from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import BookingRow, JobRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.booking import BookingStatus
from app.domain.job_plan import JobType
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, SLOT_0900, SLOT_0930, TEST_TOKEN_KEY, book

LATER = SLOT_0930 + timedelta(days=3)


async def _two_bookings(engine: AsyncEngine) -> tuple[SqlUnitOfWork, int, int]:
    """Book 09:00 and 09:30 in "aangifte"; purge the first, fail the second's purge job."""
    await seed_group(engine, slug="aangifte", capacity=2)
    await seed_group(engine, slug="elders")
    uow = SqlUnitOfWork(engine)
    service = BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY)
    purged = (await book(service, SLOT_0900)).booking.id or 0
    holding = (await book(service, SLOT_0930)).booking.id or 0
    async with uow.locked() as tx:
        await tx.purge_personal_data(purged, LATER)
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            update(JobRow)
            .where(JobRow.booking_id.in_([purged, holding]))
            .where(JobRow.type == JobType.PURGE_PII.value)
            .values(status="failed")
        )
    return uow, purged, holding


async def test_counts_and_candidates_per_group(engine: AsyncEngine) -> None:
    uow, _, _ = await _two_bookings(engine)

    async with uow.read() as tx:
        facts = await tx.list_retention(Scope(everything=True), LATER)

    assert [
        (c.group.group.slug, c.holding, c.failed_purges, c.last_purged_at) for c in facts.groups
    ] == [("aangifte", 1, 1, LATER), ("elders", 0, 0, None)]
    # Only the booking that still holds personal data; the purged one's failure is history.
    assert [(c.status, c.end, c.cancelled_at, c.closure_ends_at) for c in facts.candidates] == [
        (BookingStatus.CONFIRMED, SLOT_0930 + timedelta(minutes=30), None, None)
    ]


async def test_candidates_are_narrowed_by_time_and_status(engine: AsyncEngine) -> None:
    uow, _, holding = await _two_bookings(engine)

    async with uow.read() as tx:
        early = await tx.list_retention(Scope(everything=True), SLOT_0900)
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            update(BookingRow)
            .where(BookingRow.id == holding)
            .values(status=BookingStatus.CANCELLED.value, cancelled_at=NOW)
        )
    async with uow.read() as tx:
        cancelled = await tx.list_retention(Scope(everything=True), NOW + timedelta(seconds=1))

    assert early.candidates == []
    assert [(c.status, c.cancelled_at) for c in cancelled.candidates] == [
        (BookingStatus.CANCELLED, NOW)
    ]


async def test_the_scope_is_applied_in_sql(engine: AsyncEngine) -> None:
    uow, _, _ = await _two_bookings(engine)

    async with uow.read() as tx:
        elders = await tx.list_retention(Scope(organisations=frozenset({"org-elders"})), LATER)
        nothing = await tx.list_retention(Scope(), LATER)

    assert [c.group.group.slug for c in elders.groups] == ["elders"]
    assert elders.candidates == []
    assert nothing.groups == []
    assert nothing.candidates == []
