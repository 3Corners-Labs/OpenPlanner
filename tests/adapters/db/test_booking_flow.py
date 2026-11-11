"""A booking stores the flow and revision it was made under in SQLite (ADR 0121 step 3a)."""

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.flow_resolution import FlowRef
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import ADA, NOW, SLOT_0900, SLOT_0930, TEST_TOKEN_KEY
from tests.support.flows import booking_flow


async def test_a_service_without_flow_rows_books_with_null_columns(engine: AsyncEngine) -> None:
    await seed_group(engine, slug="balie")  # rows written behind the repair, as an old image does
    service = BookingService(SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY)

    receipt = await service.book(group_slug="balie", start=SLOT_0900, contact=ADA, locale="nl")

    assert receipt.booking.flow is None
    assert await booking_flow(engine, receipt.booking.id) == (None, None)


async def test_the_default_flow_and_its_revision_are_stored_and_kept_by_a_move(
    engine: AsyncEngine,
) -> None:
    group_id = await seed_group(engine, slug="balie")
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.ensure_default_flows(group_id)
        flows = await tx.list_service_flows(Scope(everything=True), group_id)
    appointment = next(f for f in flows if f.code == "appointment")
    assert appointment.id is not None
    assert appointment.current is not None
    expected = FlowRef(appointment.id, appointment.current.revision)
    service = BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY)

    receipt = await service.book(group_slug="balie", start=SLOT_0900, contact=ADA, locale="nl")
    await service.confirm(receipt.management_token)

    assert await booking_flow(engine, receipt.booking.id) == (expected.flow_id, expected.revision)
    async with uow.read() as tx:
        stored = await tx.get_booking(receipt.booking.id or 0)
    assert stored is not None
    assert stored.flow == expected

    moved = await service.reschedule(receipt.management_token, SLOT_0930)

    assert await booking_flow(engine, moved.booking.id) == (expected.flow_id, expected.revision)
