"""The facts about groups a configuration document is checked against (ADR 0099 point 7)."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.config_usage import read_usage
from app.adapters.db.models import (
    BookingRow,
    ClosureRow,
    GroupRow,
    ShortfallAcceptanceRow,
    SlotRow,
)
from app.domain.configuration_changes import SlotLoad
from tests.support.config_seed import NOW, seed


@pytest.fixture
async def seeded(engine: AsyncEngine) -> AsyncEngine:
    await seed(engine)
    return engine


async def group_id(session: AsyncSession, slug: str) -> int:
    found = await session.scalar(select(GroupRow.id).where(GroupRow.slug == slug))
    assert found is not None
    return int(found)


def booking(slot: SlotRow, group: int, status: str, ref: str) -> BookingRow:
    return BookingRow(
        slot_id=slot.id,
        group_id=group,
        status=status,
        locale="nl",
        management_token_hash=ref * 64,
        public_ref=f"ref-{ref}",
        created_at=NOW,
    )


async def test_a_group_with_a_client_grant_is_in_use_and_a_bare_one_is_not(
    seeded: AsyncEngine,
) -> None:
    async with AsyncSession(seeded) as session:
        usage = await read_usage(session, NOW, frozenset({"smtp_password"}))

    assert usage.in_use == {
        "group": frozenset({("acme", "helpdesk")}),
        "skill": frozenset(),
        "capacity_pool": frozenset(),
        "service_flow": frozenset(),
    }
    assert usage.future_bookings == frozenset()
    assert usage.slot_loads == {}
    assert usage.locked_settings == frozenset({"smtp_password"})


async def test_a_booking_a_closure_and_a_shortfall_each_keep_a_group_in_use(
    seeded: AsyncEngine,
) -> None:
    async with AsyncSession(seeded) as session, session.begin():
        loket = await group_id(session, "loket")
        slot = SlotRow(group_id=loket, start=NOW - timedelta(days=1), end=NOW, capacity_snapshot=1)
        session.add(slot)
        await session.flush()
        session.add(booking(slot, loket, "cancelled", "a"))
    async with AsyncSession(seeded) as session:
        assert ("acme", "loket") in (await read_usage(session, NOW, frozenset())).in_use["group"]

    async with AsyncSession(seeded) as session, session.begin():
        await session.execute(BookingRow.__table__.delete())
        helpdesk = await group_id(session, "helpdesk")
        session.add(ClosureRow(group_id=helpdesk, starts_at=NOW, created_at=NOW))
    async with AsyncSession(seeded) as session:
        usage = await read_usage(session, NOW, frozenset())
    assert usage.in_use["group"] == frozenset({("acme", "helpdesk")})

    async with AsyncSession(seeded) as session, session.begin():
        loket = await group_id(session, "loket")
        session.add(
            ShortfallAcceptanceRow(
                group_id=loket, start=NOW, agents=0, booked=1, accepted_at=NOW, actor="planner"
            )
        )
    async with AsyncSession(seeded) as session:
        usage = await read_usage(session, NOW, frozenset())
    assert usage.in_use["group"] == frozenset({("acme", "helpdesk"), ("acme", "loket")})


async def test_future_bookings_and_slot_loads_count_places_that_are_held(
    seeded: AsyncEngine,
) -> None:
    soon = NOW + timedelta(days=2)
    async with AsyncSession(seeded) as session, session.begin():
        loket = await group_id(session, "loket")
        future = SlotRow(
            group_id=loket, start=soon, end=soon + timedelta(minutes=15), capacity_snapshot=3
        )
        empty = SlotRow(
            group_id=loket,
            start=soon + timedelta(hours=1),
            end=soon + timedelta(hours=1, minutes=15),
            capacity_snapshot=3,
        )
        past = SlotRow(group_id=loket, start=NOW - timedelta(days=1), end=NOW, capacity_snapshot=3)
        session.add_all([future, empty, past])
        await session.flush()
        session.add_all(
            [
                booking(future, loket, "confirmed", "a"),
                booking(future, loket, "pending_confirmation", "b"),
                booking(future, loket, "cancelled", "c"),
                booking(past, loket, "confirmed", "d"),
            ]
        )

    async with AsyncSession(seeded) as session:
        usage = await read_usage(session, NOW, frozenset())

    assert usage.future_bookings == frozenset({("acme", "loket")})
    assert usage.slot_loads[("acme", "loket")] == (
        SlotLoad(soon, soon + timedelta(minutes=15), 2),
        SlotLoad(soon + timedelta(hours=1), soon + timedelta(hours=1, minutes=15), 0),
    )


async def test_a_past_booking_alone_freezes_nothing(seeded: AsyncEngine) -> None:
    async with AsyncSession(seeded) as session, session.begin():
        loket = await group_id(session, "loket")
        past = SlotRow(group_id=loket, start=NOW - timedelta(days=1), end=NOW, capacity_snapshot=1)
        session.add(past)
        await session.flush()
        session.add(booking(past, loket, "confirmed", "a"))

    async with AsyncSession(seeded) as session:
        usage = await read_usage(session, NOW, frozenset())

    assert usage.future_bookings == frozenset()
    assert usage.slot_loads == {}
