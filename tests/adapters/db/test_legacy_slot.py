"""A pool slot beside a legacy slot of the same service and start (ADR 0122 step 2).

The slot queries that stand for a service's own staffing select legacy slots only
(``legacy_slot()``). The first test reads ``app/adapters`` and fails for any function that
references ``SlotRow`` and neither uses ``legacy_slot()`` nor is on the allow-list below with a
reason (or marked ``# slot-scope: all`` on or above its ``def``): a new query must decide. The
others seed a pool slot at the same service and start as a legacy slot and run the queries,
asserting that the legacy answers do not move and that the database lets both rows exist (and
refuses a second row of either kind).
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

ADAPTERS = Path(__file__).parents[3] / "app" / "adapters"
START = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
END = START + timedelta(minutes=30)
MARKER = "# slot-scope: all"

ALL_SLOTS = {
    # Bookings and slots are reached through a booking, whatever supplies its place.
    "db/uow.py:_booking_where": "finds one booking by its key; a pool booking is a booking",
    "db/uow.py:get_booking": "one booking by id",
    "db/uow.py:get_booking_by_public_ref": "one booking by its public reference",
    "db/uow.py:get_booking_by_token_hash": "one booking by its management token",
    "db/uow.py:fallback_bookings": "a participant's bookings by contact, any slot",
    "db/uow.py:find_bookings_by_phone": "lookup by phone; every booking of the number",
    "db/uow.py:list_bookings": "the admin list is the group's, a gate is the group's (ADR 0122 F4)",
    "db/uow.py:list_suspended_bookings": "closures keep pool places: all suspended bookings",
    "db/uow.py:latest_suspended_end": "when the last suspended booking ends, pool ones included",
    "db/uow.py:count_future_bookings": "a group's future bookings gate its removal, any slot",
    "db/uow.py:upcoming_starts_for_email": "the limit per email is about the person",
    "db/uow.py:get_direct_calls": "direct-call slots are never pool slots (kind direct)",
    "db/uow.py:count_active_bookings": "places taken in one slot by id",
    "db/uow.py:_slot_row": "one slot row by id",
    "db/uow.py:add_slot": "writes the slot with its pool columns",
    "db/uow.py:delete_group": "erases every slot of the group",
    # Retention and purge are keyed on the slot's end, for every booking.
    "db/retention.py:_candidates": "purge candidates by slot end; pool bookings hold PII too",
    "db/rotation.py:open_booking_credentials": "credentials of every open booking",
    "db/health.py:stale_tasks": "contact-center tasks of every booking",
    "db/rollup.py:find_rollable": "rolls up purged bookings, any slot",
    "db/usage_rollup.py:find_unbackfilled": "facts of every booking, a pool booking among them",
    "db/usage_slots.py:ended_slots": "closes every ended slot; a pool slot by the pool rule",
    "db/usage_slots.py:_bookings_of": "bookings of the slots ended_slots chose",
    # Erasing and seeding.
    "db/config_group.py:write_group": "a configuration import replaces the group's slots",
    "db/config_organisation.py:_delete_organisation": "erases every slot of the organisation",
    "db/upgrade_fixture.py:_bulk_bookings": "generated fixture data, legacy slots",
    "db/upgrade_fixture.py:_slot_ids": "generated fixture data, legacy slots",
    # Pool-specific and the places of a slot.
    "db/pool_staffing.py:get_pool_commitments": "reads pool slots by definition",
    "db/pool_staffing.py:get_pool_slot": "reads pool slots by definition",
    "db/service_requests.py:unclaimed_pool_bookings": "reads pool slots by definition",
    "db/proposals.py:places_taken": "places in whichever slot the outer query selected",
    "db/legacy_slot.py:legacy_slot": "defines the predicate",
}


def _marked(lines: list[str], function: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Return whether the marker comment is on the ``def`` line or the line above it."""
    first = function.lineno - 1
    return any(MARKER in lines[i] for i in (first, first - 1) if 0 <= i < len(lines))


def _decided() -> tuple[set[str], set[str]]:
    """Return the functions that reference ``SlotRow`` without ``legacy_slot()``, and all names.

    Docstrings are ignored; the marker comment counts as a decision.
    """
    undecided: set[str] = set()
    seen: set[str] = set()
    for path in sorted(ADAPTERS.rglob("*.py")):
        if path.name == "models.py":
            continue
        source = path.read_text()
        lines = source.splitlines()
        for function in ast.walk(ast.parse(source)):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            docstring = function.body[0] if ast.get_docstring(function) else None
            names = {
                node.id
                for statement in function.body
                if statement is not docstring
                for node in ast.walk(statement)
                if isinstance(node, ast.Name)
            }
            name = f"{path.relative_to(ADAPTERS)}:{function.name}"
            if "SlotRow" in names and "legacy_slot" not in names and not _marked(lines, function):
                undecided.add(name)
                seen.add(name)
    return undecided, seen


def test_every_query_of_slots_chooses_legacy_only_or_is_listed_with_a_reason() -> None:
    undecided, _ = _decided()

    assert undecided - ALL_SLOTS.keys() == set(), "use legacy_slot() or list it with a reason"
    assert ALL_SLOTS.keys() - undecided == set(), "listed but now uses legacy_slot(): remove it"
    assert all(ALL_SLOTS.values())


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


async def test_a_closure_reads_the_pool_booking_too(
    engine: AsyncEngine, seeded: tuple[int, int, int]
) -> None:
    group_id, legacy, pooled = seeded
    async with SqlUnitOfWork(engine).read() as tx:
        both = await tx.find_confirmed_bookings(group_id, START, None, include_pool=True)

    assert sorted(b.slot_id for b in both) == sorted([legacy, pooled])


async def test_the_closing_of_ended_slots_closes_the_pool_slot_as_a_pool_slot(
    engine: AsyncEngine, seeded: tuple[int, int, int]
) -> None:
    _, legacy, pooled = seeded
    async with SqlUnitOfWork(engine).read() as tx:
        ended = await tx.ended_slots(START, END + timedelta(hours=1), 100)

    by_id = {s.slot_id: s for s in ended.slots}
    assert sorted(by_id) == sorted([legacy, pooled])
    assert not by_id[legacy].pooled
    assert by_id[pooled].pooled


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
