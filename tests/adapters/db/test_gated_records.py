"""The records only the gated paths write, for the rollback proof (ADR 0121 A30, gate item G07).

``seed-gated-records`` writes them on the demonstration of ``demo`` with the gate shut, as the
upgrade matrix's ``rollback`` mode does before it starts the previous image. With the gate shut
(the previous image's behaviour) ``demo`` keeps its own times next to them, books at the very
time the pool bookings hold, and every work item stays the one active item of its request.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.gated_records import write_gated_records
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import BookingStatus
from app.domain.service_requests import HandlingState, WorkChannel, WorkItemState
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, TEST_TOKEN_KEY, another_participant
from tests.support.pool_flows import shut_gate


async def test_one_record_of_each_gated_kind_is_written(engine: AsyncEngine) -> None:
    await seed_demo(engine)

    records = await write_gated_records(engine, NOW, TEST_TOKEN_KEY)

    async with SqlUnitOfWork(engine).read() as tx:
        booking = await tx.get_booking(records.pool_booking)
        started = await tx.get_service_request(records.started_request)
        working = await tx.get_active_work_item(records.started_request)
        attempt = await tx.get_active_work_item(records.attempt_request)
        immediate = await tx.get_service_request(records.immediate_request)
    assert booking is not None
    assert booking.status is BookingStatus.CONFIRMED
    assert booking.flow is not None
    assert started is not None
    assert started.handling is HandlingState.IN_PROGRESS
    assert working is not None
    assert working.state is WorkItemState.IN_PROGRESS
    assert working.pool_id is not None  # it occupies a unit of the shared pool
    assert attempt is not None
    assert (attempt.channel, attempt.state) == (
        WorkChannel.CONTACT_CENTER,
        WorkItemState.DISPATCHING,
    )
    assert immediate is not None
    assert immediate.handling is HandlingState.QUEUED
    assert immediate.booking_id is None


async def test_with_the_gate_shut_demo_books_at_the_time_the_pool_bookings_hold(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    shut_gate(monkeypatch)
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    service = BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=0)
    records = await write_gated_records(engine, NOW, TEST_TOKEN_KEY)
    async with uow.read() as tx:
        held = await tx.get_booking(records.pool_booking)
    assert held is not None
    day = held.start.date()
    before = await service.availability("demo", day, day)

    receipt = await service.book(
        group_slug="demo", start=held.start, contact=another_participant(), locale="nl"
    )
    async with uow.read() as tx:
        legacy = await tx.get_slot(receipt.booking.group_id, held.start)

    assert any(slot.start == held.start for slot in before)  # the pool slot hides no own time
    assert receipt.booking.flow is None or receipt.booking.flow != held.flow
    assert legacy is not None
    assert legacy.id != held.slot_id  # its own slot, beside the pool's
    assert legacy.capacity_pool_id is None
