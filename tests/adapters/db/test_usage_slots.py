"""Closing ended slots against SQLite: one set of facts per slot, from columns that survive."""

from datetime import timedelta

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import UsageEventRow
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.db.usage_slots import CLOSING_COLUMNS
from app.domain.admin import AdminService
from app.domain.booking import BookingStatus
from app.domain.calls import CallState, CallStatus, EventKind
from app.domain.integration import IntegrationService
from app.domain.privacy import PURGED_FIELDS
from app.domain.services import BookingService
from app.domain.slot_close import CLOSE_DELAY
from app.domain.slot_close_service import SlotCloser
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, SLOT_0900, SLOT_0930, TEST_TOKEN_KEY, book, participant
from tests.support.seed_rows import seed_group


async def _facts(engine: AsyncEngine) -> list[UsageEventRow]:
    async with AsyncSession(engine) as session:
        return list(await session.scalars(select(UsageEventRow).order_by(UsageEventRow.id)))


def test_the_columns_it_reads_are_none_of_the_purged_ones() -> None:
    from app.adapters.db.models import BookingRow

    assert not set(CLOSING_COLUMNS) & set(PURGED_FIELDS)
    assert not {c for c in CLOSING_COLUMNS if not hasattr(BookingRow, c) and c != "slot_id"}


async def test_an_ended_slot_is_closed_once_with_its_outcomes(engine: AsyncEngine) -> None:
    await seed_group(engine, capacity=3)
    uow = SqlUnitOfWork(engine)
    clock = FixedClock(NOW)
    service = BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0)
    closer = SlotCloser(uow, clock, lambda: timedelta(minutes=15))
    await closer.run(clock.now())
    receipt = await book(service, SLOT_0900, contact=participant(1))
    other = await book(service, SLOT_0900, contact=participant(2))
    booking_id = receipt.booking.id or 0
    async with uow.locked() as tx:
        await tx.save_call_status(
            CallStatus(
                booking_id,
                call=CallState.ENDED,
                host_joined=True,
                guest_joined_at=SLOT_0900,
                peak_participants=2,
            )
        )
        await tx.save_call_status(CallStatus(other.booking.id or 0, call=CallState.SCHEDULED))
    clock.advance(SLOT_0930 + CLOSE_DELAY + timedelta(minutes=1) - clock.now())

    first = await closer.run(clock.now())
    second = await closer.run(clock.now() + timedelta(days=1))

    assert (first.slots, second.slots) == (1, 0)
    facts = [f for f in await _facts(engine) if f.kind in ("slot.closed", "booking.outcome")]
    closed = next(f for f in facts if f.kind == "slot.closed")
    assert (closed.capacity, closed.booked, closed.bucket_hour) == (3, 2, SLOT_0900)
    outcomes = sorted(f.outcome or "" for f in facts if f.kind == "booking.outcome")
    assert outcomes == ["completed", "no_show"]
    async with uow.read() as tx:
        stored = await tx.get_booking(booking_id)
        assert stored is not None
        assert stored.status is BookingStatus.CONFIRMED


async def test_the_status_columns_of_the_call_survive_a_round_trip(engine: AsyncEngine) -> None:
    await seed_group(engine, capacity=3)
    uow = SqlUnitOfWork(engine)
    clock = FixedClock(NOW)
    receipt = await book(BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0), SLOT_0900)
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    booking_id = receipt.booking.id or 0

    for kind in (EventKind.GUEST_JOINED, EventKind.HOST_JOINED, EventKind.MEETING_ENDED):
        await integration.record(booking_id, kind, SLOT_0900)

    async with uow.read() as tx:
        status = await tx.get_call_status(booking_id)
    assert status is not None
    assert (status.peak_participants, status.guest_joined_at, status.participants) == (
        2,
        SLOT_0900,
        0,
    )
    facts = [f.kind for f in await _facts(engine)]
    assert facts.count("call.ended") == 1
    assert facts.count("call.host_joined") == 1


async def test_the_closing_never_selects_a_purged_column(engine: AsyncEngine) -> None:
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, *rest):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN202, ARG001
        statements.append(statement)

    await seed_group(engine, capacity=3)
    uow = SqlUnitOfWork(engine)
    clock = FixedClock(NOW)
    closer = SlotCloser(uow, clock, lambda: timedelta(minutes=15))
    await closer.run(clock.now())
    await book(BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0), SLOT_0900)
    clock.advance(timedelta(days=10))
    statements.clear()

    await closer.run(clock.now())

    reads = [s for s in statements if "FROM slots" in s or "JOIN call_status" in s]
    assert reads
    for statement in statements:
        if "FROM bookings" in statement or "JOIN call_status" in statement:
            select_list = statement.split(" FROM ")[0]
            if "bookings." in select_list:
                for name in PURGED_FIELDS:
                    assert f"bookings.{name}" not in select_list, name


async def test_a_rebook_request_keeps_its_time(engine: AsyncEngine) -> None:
    from app.domain.booking import ChangeReason

    await seed_group(engine, capacity=3)
    uow = SqlUnitOfWork(engine)
    receipt = await book(
        BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=0), SLOT_0900
    )
    async with uow.locked() as tx:
        booking = await tx.get_booking(receipt.booking.id or 0)
        assert booking is not None
        await tx.save_booking(booking.request_rebook(ChangeReason.STAFFING, SLOT_0930, NOW))

    async with uow.read() as tx:
        stored = await tx.get_booking(receipt.booking.id or 0)
    assert stored is not None
    assert stored.rebook_requested_at == NOW
