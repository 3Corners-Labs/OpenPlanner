"""The pool report's reads against SQLite: times only, filtered by scope (ADR 0122 step 5)."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest

from app.domain.authorization import Scope
from app.domain.booking import BookingStatus
from app.domain.holidays import Holiday, HolidayCalendar
from tests.adapters.db.test_pool_staffing import block, book_on, pool_slot, staffed_pool
from tests.support.domain import SLOT_0900
from tests.support.flow_world import EVERYTHING, World, open_world

DAY = SLOT_0900.date()


@pytest.fixture
async def sql_world(database_url: str) -> AsyncIterator[World]:
    async with open_world("sql", database_url) as seeded:
        yield seeded


async def read(world: World, scope: Scope = EVERYTHING, slug: str = "acme") -> Any:
    async with world.uow.read() as tx:
        return await tx.read_pool_report_inputs(scope, slug, DAY, DAY)


async def test_a_pool_comes_with_its_staffing_and_the_intervals_that_hold_a_unit(
    sql_world: World,
) -> None:
    world = sql_world
    pool = await staffed_pool(world)
    async with world.uow.locked() as tx:
        await tx.add_pool_rule(EVERYTHING, pool.id, block())
        slot = await tx.add_slot(pool_slot(world, pool))
    await book_on(world, slot, 1)
    await book_on(world, slot, 2, status=BookingStatus.COMPLETED)
    await book_on(world, slot, 3, status=BookingStatus.NO_SHOW)
    await book_on(world, slot, 4, status=BookingStatus.CANCELLED)
    await book_on(world, slot, 5, status=BookingStatus.PENDING_CONFIRMATION)

    found = await read(world)

    assert found is not None
    [row] = found.pools
    assert (row.name, row.archived) == ("Loket", False)
    assert row.staffing is not None
    assert len(row.staffing.rules) == 1
    assert row.booked == ((SLOT_0900, slot.occupied_until),) * 3


async def test_a_holiday_of_the_organisations_calendar_is_a_closed_day(sql_world: World) -> None:
    world = sql_world
    await staffed_pool(world)
    async with world.uow.locked() as tx:
        calendar = await tx.add_holiday_calendar(HolidayCalendar(0, "nl", "Nederland"))
        await tx.add_holiday(Holiday(calendar.id, DAY, "Feestdag"))
        await tx.save_organisation_holiday_calendars(world.acme, frozenset({calendar.id}))

    found = await read(world)

    assert found.pools[0].closed_days == frozenset({DAY})


async def test_an_appointment_of_another_day_or_pool_is_not_read(sql_world: World) -> None:
    world = sql_world
    pool = await staffed_pool(world)
    other = await staffed_pool(world, "Advies")
    async with world.uow.locked() as tx:
        elsewhere = await tx.add_slot(pool_slot(world, other))
    await book_on(world, elsewhere, 1)

    found = await read(world)

    assert {p.name: p.booked for p in found.pools} == {
        "Loket": (),
        "Advies": ((SLOT_0900, elsewhere.occupied_until),),
    }
    async with world.uow.read() as tx:
        later = await tx.read_pool_report_inputs(
            EVERYTHING, "acme", DAY + timedelta(days=7), DAY + timedelta(days=7)
        )
    assert pool.id is not None
    assert all(p.booked == () for p in later.pools)


async def test_only_an_organisation_wide_scope_reads_a_pool(sql_world: World) -> None:
    world = sql_world
    await staffed_pool(world)
    own = Scope(organisations=frozenset({"acme"}))
    one_service = Scope(groups=frozenset({("acme", "balie")}))
    other = Scope(organisations=frozenset({"beta"}))

    assert (await read(world, own)) is not None
    assert await read(world, one_service) is None
    assert await read(world, other) is None
    assert await read(world, EVERYTHING, "nowhere") is None


async def test_an_archived_pool_and_one_without_a_time_zone_are_listed(sql_world: World) -> None:
    from tests.support.flow_world import add_pool

    world = sql_world
    old = await staffed_pool(world, "Oud")
    await world.archive_pool_directly(old.id)
    bare = await add_pool(world, "Kaal")

    found = await read(world)

    by_name = {p.name: p for p in found.pools}
    assert by_name["Oud"].archived
    assert by_name["Kaal"].staffing is None
    assert bare.id == by_name["Kaal"].pool_id
