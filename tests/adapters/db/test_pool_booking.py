"""Booking on a counted pool on the SQL adapter and the fake (ADR 0122 step 5, gate opened).

Each case runs on ``SqlUnitOfWork`` over a migrated database and on the fake (the ``world``
fixture). The race for the last unit runs on SQL only: two bookings on separate connections,
queued behind a third that holds the write lock, so contention is certain (A11).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.adapters.db.engine import BEGIN_MODE_OPTION, create_engine
from app.adapters.db.models import BookingRow, SlotRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.errors import DomainError, ErrorCode
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.support.domain import MONDAY, NOW, TEST_TOKEN_KEY, make_group, participant
from tests.support.flow_world import World
from tests.support.pool_flows import PoolFlow, add_pool_flow, block, open_gate, seed_pool_flow

AT_0900 = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
PATIENT_MS = 60_000


@pytest.fixture(autouse=True)
def gate(monkeypatch: pytest.MonkeyPatch) -> None:
    open_gate(monkeypatch)


def _service(uow: object) -> BookingService:
    return BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=10)  # type: ignore[arg-type]


async def _desk(world: World, slug: str = "desk") -> int:
    """Add an Acme service without routing questions and return its id."""
    async with world.uow.locked() as tx:
        added = await tx.add_group(make_group(id=0, organisation_id=world.acme, slug=slug))
    return int(added.id)


async def _one_service(world: World, units: int = 2) -> PoolFlow:
    """Acme's service ``desk`` with the flow ``advies`` on a counted pool of ``units``."""
    return await seed_pool_flow(world.uow, world.acme, await _desk(world), [block(units=units)])


async def _two_services(world: World, units: int = 1) -> tuple[PoolFlow, PoolFlow]:
    """Acme's ``desk`` and a second Acme service ``kassa``, each with a flow on one pool."""
    first = await _one_service(world, units)
    kassa = await _desk(world, "kassa")
    second = await add_pool_flow(world.uow, kassa, first.pool_id, code="intake", duration=40)
    return first, second


async def _book(service: BookingService, slug: str, flow: str, number: int, start=AT_0900):  # type: ignore[no-untyped-def]
    return await service.book(
        group_slug=slug, start=start, contact=participant(number), locale="nl", flow=flow
    )


async def test_a_booking_stores_a_pool_slot_and_its_flow(world: World) -> None:
    made = await _one_service(world)
    service = _service(world.uow)

    receipt = await _book(service, "desk", "advies", 1)

    async with world.uow.read() as tx:
        slot = await tx.get_pool_slot(
            made.pool_id, receipt.booking.group_id, AT_0900, AT_0900 + timedelta(minutes=30)
        )
        stored = await tx.get_booking(receipt.booking.id or 0)
        legacy = await tx.get_slot(receipt.booking.group_id, AT_0900)
    assert slot is not None
    assert slot.id == receipt.booking.slot_id
    assert slot.end == AT_0900 + timedelta(minutes=20)
    assert legacy is None  # never a place in the service's own slot
    assert stored is not None
    assert stored.flow == receipt.booking.flow
    assert stored.end == AT_0900 + timedelta(minutes=20)


async def test_a11_two_services_on_one_pool_share_its_units(world: World) -> None:
    await _two_services(world, units=1)
    service = _service(world.uow)

    await _book(service, "kassa", "intake", 1)  # 09:00–09:50 with the wrap-up

    with pytest.raises(DomainError) as error:
        await _book(service, "desk", "advies", 2, AT_0900 + timedelta(minutes=30))
    assert error.value.code is ErrorCode.SLOT_FULL
    starts = [s.start for s in await service.availability("desk", MONDAY, MONDAY, flow="advies")]
    assert starts[0] == AT_0900 + timedelta(minutes=60)  # 09:50 is off the 30-minute grid


async def test_a_move_on_the_pool_frees_the_old_unit(world: World) -> None:
    await _one_service(world, units=1)
    service = _service(world.uow)
    receipt = await _book(service, "desk", "advies", 1)
    await service.confirm(receipt.management_token)

    moved = await service.reschedule(receipt.management_token, AT_0900 + timedelta(minutes=30))
    again = await _book(service, "desk", "advies", 2)

    assert moved.booking.flow == receipt.booking.flow
    assert again.booking.start == AT_0900


@asynccontextmanager
async def _hold_write_lock(url: str) -> AsyncIterator[None]:
    blocker = create_engine(url)
    try:
        async with blocker.connect() as raw:
            conn = await raw.execution_options(**{BEGIN_MODE_OPTION: "IMMEDIATE"})
            await conn.begin()
            await conn.exec_driver_sql("SELECT 1")
            yield
            await conn.rollback()
    finally:
        await blocker.dispose()


async def test_a11_two_bookings_racing_for_the_last_unit_never_overbook(
    world: World, database_url: str
) -> None:
    """Two services, one unit, two connections: exactly one wins, the other is ``slot_full``."""
    if world.granted_by is None:
        pytest.skip("the write lock is SQLite's; the fake has no connections")
    await _two_services(world, units=1)
    engines = [create_engine(database_url, busy_timeout_ms=PATIENT_MS) for _ in range(2)]
    try:
        services = [_service(SqlUnitOfWork(e)) for e in engines]
        async with _hold_write_lock(database_url):
            pending = [
                asyncio.ensure_future(_book(services[0], "desk", "advies", 1)),
                asyncio.ensure_future(_book(services[1], "kassa", "intake", 2)),
            ]
            await asyncio.sleep(0.1)
            assert not any(task.done() for task in pending)
        results = await asyncio.gather(*pending, return_exceptions=True)
    finally:
        for e in engines:
            await e.dispose()

    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(results) - len(failures) == 1
    assert [getattr(f, "code", f) for f in failures] == [ErrorCode.SLOT_FULL]
    check = create_engine(database_url)
    try:
        async with check.connect() as conn:
            bookings = (await conn.execute(select(func.count()).select_from(BookingRow))).scalar()
            pool_slots = (
                await conn.execute(
                    select(func.count())
                    .select_from(SlotRow)
                    .where(SlotRow.capacity_pool_id.is_not(None))
                )
            ).scalar()
    finally:
        await check.dispose()
    assert (bookings, pool_slots) == (1, 1)
