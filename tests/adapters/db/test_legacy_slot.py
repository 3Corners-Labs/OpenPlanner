"""A pool slot beside a legacy slot of the same service and start (ADR 0122 step 2).

The slot queries that stand for a service's own staffing select legacy slots only
(``legacy_slot()``). The first test reads the source and fails for a query that selects scheduled
slots without it; the others seed a pool slot at the same service and start as a legacy slot and
run the queries, asserting that the legacy answers do not move and that the database lets both
rows exist (and refuses a second row of either kind).
"""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.config_usage import read_usage
from app.adapters.db.models import BookingRow, CapacityPoolRow, GroupRow, SlotRow
from app.adapters.db.uow import SqlUnitOfWork
from tests.adapters.db.conftest import seed_group
from tests.support.domain import NOW

DB = Path(__file__).parents[3] / "app" / "adapters" / "db"
START = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
END = START + timedelta(minutes=30)

DELIBERATE = {"upcoming_starts_for_email"}
"""Queries that select scheduled slots and deliberately count pool bookings too: the limit of a
participant's bookings per email and service is about the person, whatever supplies the place."""


def test_every_query_of_scheduled_slots_names_the_legacy_predicate() -> None:
    missing = []
    for path in sorted(DB.glob("*.py")):
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            text = ast.get_source_segment(source, node) or ""
            if "SlotRow.kind == BookingKind.SCHEDULED" in text and "legacy_slot()" not in text:
                missing.append(f"{path.name}:{node.name}")
    assert {m.split(":")[1] for m in missing} == DELIBERATE, missing


async def _pool(engine: AsyncEngine, organisation_id: int) -> int:
    """Insert a counted pool of the organisation and return its id."""
    async with AsyncSession(engine) as session, session.begin():
        pool = CapacityPoolRow(
            organisation_id=organisation_id,
            name="Loket",
            mode="counted",
            code="loket",
            timezone="Europe/Amsterdam",
        )
        session.add(pool)
        await session.flush()
        return pool.id


async def _slots(engine: AsyncEngine, group_id: int, pool_id: int) -> tuple[int, int]:
    """Insert a legacy slot and a pool slot at one start of one service, each with a booking."""
    async with AsyncSession(engine) as session, session.begin():
        ids = []
        for number, pool in enumerate((None, pool_id)):
            slot = SlotRow(
                group_id=group_id,
                start=START,
                end=END,
                capacity_snapshot=1,
                capacity_pool_id=pool,
                occupied_until=None if pool is None else END + timedelta(minutes=5),
            )
            session.add(slot)
            await session.flush()
            session.add(
                BookingRow(
                    slot_id=slot.id,
                    group_id=group_id,
                    status="confirmed",
                    locale="nl",
                    management_token_hash=f"{number:064d}",
                    public_ref=f"ref{number}",
                    created_at=NOW,
                )
            )
            ids.append(slot.id)
        return ids[0], ids[1]


@pytest.fixture
async def seeded(engine: AsyncEngine) -> tuple[int, int, int]:
    """Return the group id, the legacy slot id and the pool slot id."""
    group_id = await seed_group(engine)
    async with AsyncSession(engine) as session:
        organisation_id = await session.scalar(
            select(GroupRow.organisation_id).where(GroupRow.id == group_id)
        )
    assert organisation_id is not None
    pool_id = await _pool(engine, organisation_id)
    legacy, pooled = await _slots(engine, group_id, pool_id)
    return group_id, legacy, pooled


async def test_availability_and_booking_reads_see_only_the_legacy_slot(
    engine: AsyncEngine, seeded: tuple[int, int, int]
) -> None:
    group_id, legacy, _ = seeded
    async with SqlUnitOfWork(engine).read() as tx:
        slot = await tx.get_slot(group_id, START)
        usage = await tx.get_slot_usage(group_id, START, START + timedelta(days=1))
        confirmed = await tx.find_confirmed_bookings(group_id, START, None)
        agenda = await tx.find_agenda_bookings(group_id, START, END)

    assert slot is not None
    assert slot.id == legacy
    assert [(u.capacity_snapshot, u.active) for u in usage] == [(1, 1)]
    assert [b.slot_id for b in confirmed] == [legacy]
    assert [b.slot_id for b in agenda] == [legacy]


async def test_the_closing_of_ended_slots_skips_the_pool_slot(
    engine: AsyncEngine, seeded: tuple[int, int, int]
) -> None:
    _, legacy, _ = seeded
    async with SqlUnitOfWork(engine).read() as tx:
        ended = await tx.ended_slots(START, END + timedelta(hours=1), 100)

    assert [s.slot_id for s in ended.slots] == [legacy]


async def test_the_planner_usage_counts_only_the_legacy_slot_load(
    engine: AsyncEngine, seeded: tuple[int, int, int]
) -> None:
    async with AsyncSession(engine) as session:
        usage = await read_usage(session, START - timedelta(days=1), frozenset())

    loads = [load for found in usage.slot_loads.values() for load in found]
    assert [(load.start, load.booked) for load in loads] == [(START, 1)]


async def test_the_database_keeps_one_row_of_each_kind(
    engine: AsyncEngine, seeded: tuple[int, int, int]
) -> None:
    group_id, _, pooled = seeded
    async with AsyncSession(engine) as session:
        pool_id = await session.scalar(select(SlotRow.capacity_pool_id).where(SlotRow.id == pooled))
    assert pool_id is not None
    for pool in (None, pool_id):
        with pytest.raises(IntegrityError):
            async with AsyncSession(engine) as session, session.begin():
                session.add(
                    SlotRow(
                        group_id=group_id,
                        start=START,
                        end=END,
                        capacity_snapshot=1,
                        capacity_pool_id=pool,
                        occupied_until=None if pool is None else END + timedelta(minutes=5),
                    )
                )
    async with AsyncSession(engine) as session, session.begin():
        session.add(  # another length on the same pool, service and start is another row
            SlotRow(
                group_id=group_id,
                start=START,
                end=END,
                capacity_snapshot=1,
                capacity_pool_id=pool_id,
                occupied_until=END + timedelta(minutes=10),
            )
        )
