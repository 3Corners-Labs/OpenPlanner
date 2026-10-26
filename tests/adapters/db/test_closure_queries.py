"""Emergency closures and suspended bookings in SQLite (ADR 0024)."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.authorization import Principal, Role, Scope
from app.domain.booking import BookingStatus
from app.domain.closure import Closure
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.job_plan import JobType, plan_closure_update, plan_purge
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.closures import NOTICES
from tests.support.domain import MONDAY, NOW, SLOT_0900, TEST_TOKEN_KEY, book

ADMIN = Principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
UNTIL = SLOT_0900 + timedelta(days=1)


async def _closed(engine: AsyncEngine) -> tuple[SqlUnitOfWork, BookingService, int]:
    """Seed a group with capacity 2, book 09:00 and close the group until Tuesday."""
    await seed_group(engine, slug="aangifte", capacity=2)
    uow = SqlUnitOfWork(engine)
    clock = FixedClock(NOW)
    bookings = BookingService(uow, clock, TEST_TOKEN_KEY)
    booking_id = (await book(bookings, SLOT_0900)).booking.id or 0
    closures = ClosureService(uow, clock, AdminService(uow, clock), bookings)
    await closures.close_group(ADMIN, "aangifte", ClosureInput(None, UNTIL, NOTICES))
    return uow, bookings, booking_id


async def test_closure_round_trips_with_its_notices(engine: AsyncEngine) -> None:
    uow, _, _ = await _closed(engine)

    async with uow.read() as tx:
        group = await tx.get_group("aangifte")
        assert group is not None
        closures = await tx.get_closures(group.id, NOW)
        ended = await tx.get_closures(group.id, UNTIL)
        stored = await tx.get_closure(closures[0].id or 0)
        missing = await tx.get_closure(999)

    assert [(c.starts_at, c.ends_at) for c in closures] == [(NOW, UNTIL)]
    assert stored is not None
    assert dict(stored.notices) == NOTICES
    assert ended == []
    assert missing is None


async def test_save_closure_replaces_window_and_notices(engine: AsyncEngine) -> None:
    uow, _, _ = await _closed(engine)
    async with uow.read() as tx:
        group = await tx.get_group("aangifte")
        assert group is not None
        current = (await tx.get_closures(group.id, NOW))[0]

    texts = {"nl": "Weer open.", "en": "Open again."}
    async with uow.locked() as tx:
        await tx.save_closure(
            Closure(current.id, group.id, NOW, NOW + timedelta(hours=1), texts, NOW)
        )
    async with uow.read() as tx:
        stored = await tx.get_closure(current.id or 0)

    assert stored is not None
    assert stored.ends_at == NOW + timedelta(hours=1)
    assert dict(stored.notices) == texts


async def test_suspended_booking_keeps_its_place_and_data(engine: AsyncEngine) -> None:
    uow, bookings, booking_id = await _closed(engine)

    async with uow.read() as tx:
        booking = await tx.get_booking(booking_id)
        usage = await tx.get_slot_usage(1, SLOT_0900, UNTIL)
        group = await tx.get_group("aangifte")
        assert group is not None
        on_hold = await tx.list_suspended_bookings(Scope(everything=True), group.id)
        elsewhere = await tx.list_suspended_bookings(Scope(groups=frozenset({"x"})), group.id)
        confirmed = await tx.find_confirmed_bookings(group.id, NOW, None)
        future = await tx.count_future_bookings(group.id, NOW)

    assert booking is not None
    assert booking.status is BookingStatus.SUSPENDED
    assert booking.closure_id is not None
    assert booking.contact is not None
    assert [u.active for u in usage] == [1]  # the place is still taken
    assert [b.id for b in on_hold] == [booking_id]
    assert elsewhere == []
    assert confirmed == []
    assert future == 1
    assert await bookings.availability("aangifte", MONDAY, MONDAY) == []


async def test_find_confirmed_bookings_respects_the_bounds(engine: AsyncEngine) -> None:
    await seed_group(engine, slug="aangifte", capacity=2)
    uow = SqlUnitOfWork(engine)
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY), SLOT_0900)

    async with uow.read() as tx:
        group_id = receipt.booking.group_id
        inside = await tx.find_confirmed_bookings(group_id, SLOT_0900, UNTIL)
        before = await tx.find_confirmed_bookings(group_id, NOW, SLOT_0900)

    assert [b.id for b in inside] == [receipt.booking.id]
    assert before == []


async def test_add_and_delete_pending_jobs_by_type(engine: AsyncEngine) -> None:
    uow, _, booking_id = await _closed(engine)

    async with uow.locked() as tx:
        await tx.delete_pending_jobs(booking_id, {JobType.PURGE_PII})
        await tx.add_jobs(booking_id, plan_closure_update(now=NOW) + plan_purge(UNTIL))
    async with engine.connect() as connection:
        rows = await connection.exec_driver_sql(
            "SELECT type FROM jobs WHERE booking_id = ? AND status = 'pending' ORDER BY id",
            (booking_id,),
        )
        types = [row[0] for row in rows]

    assert types == ["end_wxcc_task", "send_suspension", "send_closure_update", "purge_pii"]
