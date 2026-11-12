"""Staffing of a counted pool and pool slots: one set of cases for the SQL adapter and the fake.

ADR 0122 step 2. Each test runs on ``SqlUnitOfWork`` over a migrated database and on
``InMemoryUnitOfWork`` (the ``world`` fixture), so the fake cannot drift from the adapter. The
checks of a block (overlaps, subjects) are ``check_pool_staffing`` and are tested in the domain;
these tests pin what the two backends *store*, the scope they filter by, and that a pool slot is
a place in the pool and never a place in a service's own slot.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.authorization import Scope
from app.domain.booking import Booking, BookingKind, BookingStatus, Slot
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.pool_accounting import Commitment
from app.domain.reschedule_proposal import HeldProposal, ProposalOutcome
from app.domain.scheduling import Offer, ScheduleOverride, ScheduleRule, TimeWindow
from tests.support.closures import confirmed_booking
from tests.support.domain import SLOT_0900
from tests.support.flow_world import EVERYTHING, World, add_pool

AMSTERDAM = "Europe/Amsterdam"
BETA = Scope(organisations=frozenset({"beta"}))
OTHER_DAY = date(2026, 3, 2)


def block(opens: int = 9, closes: int = 12, units: int = 2, offer: Offer = Offer.SCHEDULED) -> Any:
    """Return a Monday block ``opens`` to ``closes`` o'clock with ``units`` people."""
    return ScheduleRule(0, TimeWindow(time(opens), time(closes), units, "Balie", offer))


async def staffed_pool(world: World, name: str = "Loket") -> CapacityPool:
    """Store a counted pool of Acme with a time zone."""
    pool = await add_pool(world, name)
    async with world.uow.locked() as tx:
        assert await tx.set_pool_timezone(EVERYTHING, pool.id, AMSTERDAM)
    return pool


async def staffing(world: World, pool: CapacityPool, from_day: date | None = None) -> Any:
    """Read the staffing of ``pool``."""
    async with world.uow.read() as tx:
        return await tx.get_pool_staffing(EVERYTHING, pool.id, from_day)


async def test_no_staffing_before_a_time_zone_is_chosen(world: World) -> None:
    pool = await add_pool(world)

    assert await staffing(world, pool) is None
    async with world.uow.locked() as tx:
        assert await tx.set_pool_timezone(EVERYTHING, pool.id, AMSTERDAM)

    found = await staffing(world, pool)
    assert found.timezone == ZoneInfo(AMSTERDAM)
    assert found.rules == ()
    assert found.overrides == ()


async def test_the_time_zone_can_be_cleared(world: World) -> None:
    pool = await staffed_pool(world)

    async with world.uow.locked() as tx:
        assert await tx.set_pool_timezone(EVERYTHING, pool.id, None)

    assert await staffing(world, pool) is None


async def test_blocks_round_trip_with_their_ids_in_week_order(world: World) -> None:
    pool = await staffed_pool(world)
    late = ScheduleRule(
        0,
        TimeWindow(time(13), time(17), 3, None, Offer.BOTH),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    async with world.uow.locked() as tx:
        late_id = await tx.add_pool_rule(EVERYTHING, pool.id, late)
        early_id = await tx.add_pool_rule(EVERYTHING, pool.id, block())
        tuesday_id = await tx.add_pool_rule(
            EVERYTHING, pool.id, replace(block(), weekday=1, window=TimeWindow(time(8), time(9), 1))
        )

    found = await staffing(world, pool)

    assert [r.id for r in found.rules] == [early_id, late_id, tuesday_id]
    assert found.rules[0] == block()
    assert found.rules[1] == late
    assert found.rules[1].window.offer is Offer.BOTH
    assert found.rules[0].window.label == "Balie"
    assert found.rules[0].window.capacity == 2


async def test_a_block_is_changed_and_deleted_by_id_within_its_pool(world: World) -> None:
    pool = await staffed_pool(world)
    other = await staffed_pool(world, "Advies")
    async with world.uow.locked() as tx:
        rule_id = await tx.add_pool_rule(EVERYTHING, pool.id, block())
        assert rule_id is not None
        assert not await tx.update_pool_rule(EVERYTHING, other.id, rule_id, block(units=9))
        assert not await tx.delete_pool_rule(EVERYTHING, other.id, rule_id)
        assert await tx.update_pool_rule(EVERYTHING, pool.id, rule_id, block(units=5))

    assert [r.window.capacity for r in (await staffing(world, pool)).rules] == [5]
    async with world.uow.locked() as tx:
        assert await tx.delete_pool_rule(EVERYTHING, pool.id, rule_id)
        assert not await tx.delete_pool_rule(EVERYTHING, pool.id, rule_id)
        assert not await tx.update_pool_rule(EVERYTHING, pool.id, rule_id, block())

    assert (await staffing(world, pool)).rules == ()


async def test_exceptions_round_trip_a_closed_day_and_a_window(world: World) -> None:
    pool = await staffed_pool(world)
    opened = ScheduleOverride(OTHER_DAY, TimeWindow(time(10), time(11), 4, "Extra", Offer.DIRECT))
    closed = ScheduleOverride(date(2026, 3, 1), None)
    async with world.uow.locked() as tx:
        opened_id = await tx.add_pool_override(EVERYTHING, pool.id, opened)
        closed_id = await tx.add_pool_override(EVERYTHING, pool.id, closed)

    found = await staffing(world, pool)
    assert [o.id for o in found.overrides] == [closed_id, opened_id]
    assert found.overrides[0].window is None
    assert found.overrides[1] == opened
    assert found.overrides[1].window.offer is Offer.DIRECT
    assert [o.id for o in (await staffing(world, pool, OTHER_DAY)).overrides] == [opened_id]


async def test_an_exception_is_changed_and_deleted_by_id_within_its_pool(world: World) -> None:
    pool = await staffed_pool(world)
    other = await staffed_pool(world, "Advies")
    async with world.uow.locked() as tx:
        override_id = await tx.add_pool_override(
            EVERYTHING, pool.id, ScheduleOverride(OTHER_DAY, None)
        )
        assert override_id is not None
        replacement = ScheduleOverride(OTHER_DAY, TimeWindow(time(9), time(10), 1))
        assert not await tx.update_pool_override(EVERYTHING, other.id, override_id, replacement)
        assert not await tx.delete_pool_override(EVERYTHING, other.id, override_id)
        assert await tx.update_pool_override(EVERYTHING, pool.id, override_id, replacement)

    assert (await staffing(world, pool)).overrides[0].window.capacity == 1
    async with world.uow.locked() as tx:
        assert await tx.delete_pool_override(EVERYTHING, pool.id, override_id)
        assert not await tx.delete_pool_override(EVERYTHING, pool.id, override_id)
        assert not await tx.update_pool_override(EVERYTHING, pool.id, override_id, replacement)


async def test_staffing_is_filtered_by_scope_like_the_pool(world: World) -> None:
    pool = await staffed_pool(world)
    async with world.uow.locked() as tx:
        assert await tx.add_pool_rule(BETA, pool.id, block()) is None
        assert await tx.add_pool_override(BETA, pool.id, ScheduleOverride(OTHER_DAY, None)) is None
        assert not await tx.set_pool_timezone(BETA, pool.id, "UTC")
        assert not await tx.update_pool_rule(BETA, pool.id, 1, block())
        assert not await tx.delete_pool_rule(BETA, pool.id, 1)
        assert not await tx.update_pool_override(
            BETA, pool.id, 1, ScheduleOverride(OTHER_DAY, None)
        )
        assert not await tx.delete_pool_override(BETA, pool.id, 1)
    async with world.uow.read() as tx:
        assert await tx.get_pool_staffing(BETA, pool.id) is None
        assert await tx.get_pool_staffing(EVERYTHING, 9999) is None

    assert (await staffing(world, pool)).rules == ()


async def test_a_legacy_pool_has_no_staffing(world: World) -> None:
    async with world.uow.locked() as tx:
        legacy = await tx.save_capacity_pool(
            EVERYTHING,
            CapacityPool(
                None, world.acme, "Balie", PoolMode.LEGACY_GROUP, group_id=world.acme_group
            ),
        )
        assert not await tx.set_pool_timezone(EVERYTHING, legacy.id, AMSTERDAM)
        assert await tx.add_pool_rule(EVERYTHING, legacy.id, block()) is None
        assert (
            await tx.add_pool_override(EVERYTHING, legacy.id, ScheduleOverride(OTHER_DAY, None))
            is None
        )

    assert await staffing(world, legacy) is None


def pool_slot(world: World, pool: CapacityPool, minutes: int = 30, buffer: int = 5) -> Slot:
    """Return a pool slot of Acme's group at Monday 09:00 CET: ``minutes`` plus a wrap-up."""
    end = SLOT_0900 + timedelta(minutes=minutes)
    return Slot(
        None,
        world.acme_group,
        SLOT_0900,
        end,
        2,
        BookingKind.SCHEDULED,
        pool.id,
        end + timedelta(minutes=buffer),
    )


async def book_on(world: World, slot: Slot, number: int, **changes: Any) -> Booking:
    """Store a booking in ``slot`` with its own token hash and reference."""
    booking = confirmed_booking(
        **{
            "id": None,
            "group_id": slot.group_id,
            "slot_id": slot.id,
            "start": slot.start,
            "end": slot.end,
            "token_hash": f"{number:064d}",
            "public_ref": f"ref{number:020d}",
            **changes,
        }
    )
    async with world.uow.locked() as tx:
        return await tx.add_booking(booking)


async def test_a_pool_slot_keeps_its_pool_and_its_occupied_end(world: World) -> None:
    pool = await staffed_pool(world)
    wanted = pool_slot(world, pool)
    async with world.uow.locked() as tx:
        stored = await tx.add_slot(wanted)

    assert stored.id is not None
    assert (stored.capacity_pool_id, stored.occupied_until) == (pool.id, wanted.occupied_until)
    async with world.uow.read() as tx:
        found = await tx.get_pool_slot(
            pool.id, wanted.group_id, wanted.start, wanted.occupied_until
        )
        other_length = await tx.get_pool_slot(
            pool.id, wanted.group_id, wanted.start, wanted.occupied_until + timedelta(minutes=5)
        )
        other_pool = await tx.get_pool_slot(
            pool.id + 1, wanted.group_id, wanted.start, wanted.occupied_until
        )
    assert found == stored
    assert other_length is None
    assert other_pool is None


async def test_a_pool_slot_is_invisible_to_the_legacy_slot_queries(world: World) -> None:
    pool = await staffed_pool(world)
    async with world.uow.locked() as tx:
        slot = await tx.add_slot(pool_slot(world, pool))
    await book_on(world, slot, 1)
    upper = SLOT_0900 + timedelta(days=1)

    async with world.uow.read() as tx:
        assert await tx.get_slot(slot.group_id, slot.start) is None
        assert await tx.get_slot_usage(slot.group_id, SLOT_0900, upper) == []


async def test_a_pool_slot_and_a_legacy_slot_of_one_service_and_start_live_side_by_side(
    world: World,
) -> None:
    pool = await staffed_pool(world)
    async with world.uow.locked() as tx:
        legacy = await tx.add_slot(
            Slot(None, world.acme_group, SLOT_0900, SLOT_0900 + timedelta(minutes=30), 1)
        )
        pooled = await tx.add_slot(pool_slot(world, pool))
    await book_on(world, legacy, 1)
    await book_on(world, pooled, 2)
    await book_on(world, pooled, 3)

    async with world.uow.read() as tx:
        found = await tx.get_slot(world.acme_group, SLOT_0900)
        usage = await tx.get_slot_usage(world.acme_group, SLOT_0900, SLOT_0900 + timedelta(days=1))

    assert found is not None
    assert found.id == legacy.id
    assert found.capacity_pool_id is None
    assert [(u.capacity_snapshot, u.active) for u in usage] == [(1, 1)]


async def commitments(world: World, pool: CapacityPool, lower: datetime, upper: datetime) -> Any:
    """Return the commitments of ``pool`` in a window, ordered by key for comparison."""
    async with world.uow.read() as tx:
        found = await tx.get_pool_commitments(pool.id, lower, upper)
    return sorted(found, key=lambda c: c.key)


async def test_commitments_are_the_bookings_that_hold_a_place(world: World) -> None:
    pool = await staffed_pool(world)
    other = await staffed_pool(world, "Advies")
    async with world.uow.locked() as tx:
        slot = await tx.add_slot(pool_slot(world, pool))
        elsewhere = await tx.add_slot(pool_slot(world, other))
    kept = await book_on(world, slot, 1)
    await book_on(world, slot, 2, status=BookingStatus.CANCELLED)
    pending = await book_on(world, slot, 3, status=BookingStatus.PENDING_CONFIRMATION)
    await book_on(world, elsewhere, 4)

    found = await commitments(world, pool, SLOT_0900, SLOT_0900 + timedelta(hours=1))

    until = slot.occupied_until
    assert found == [
        Commitment(("booking", kept.id), SLOT_0900, until),
        Commitment(("booking", pending.id), SLOT_0900, until),
    ]


async def test_commitments_cover_the_whole_occupied_interval_and_touching_ones_do_not_count(
    world: World,
) -> None:
    pool = await staffed_pool(world)
    async with world.uow.locked() as tx:
        slot = await tx.add_slot(pool_slot(world, pool, minutes=30, buffer=10))
    booking = await book_on(world, slot, 1)
    until = slot.occupied_until
    assert until == SLOT_0900 + timedelta(minutes=40)

    started_inside = await commitments(
        world, pool, SLOT_0900 + timedelta(minutes=35), until + timedelta(hours=1)
    )
    ends_when_window_starts = await commitments(world, pool, until, until + timedelta(hours=1))
    starts_when_window_ends = await commitments(
        world, pool, SLOT_0900 - timedelta(hours=1), SLOT_0900
    )

    assert [c.key for c in started_inside] == [("booking", booking.id)]
    assert ends_when_window_starts == []
    assert starts_when_window_ends == []


async def test_an_open_proposal_holds_a_unit_until_it_is_released(world: World) -> None:
    pool = await staffed_pool(world)
    async with world.uow.locked() as tx:
        slot = await tx.add_slot(pool_slot(world, pool))
        legacy = await tx.add_slot(
            Slot(None, world.acme_group, SLOT_0900 - timedelta(days=1), SLOT_0900, 1)
        )
    waiting = await book_on(world, legacy, 1, status=BookingStatus.AWAITING_REBOOK)
    assert waiting.id is not None
    proposal = HeldProposal(
        None, waiting.id, slot.id, slot.start, slot.end, SLOT_0900, SLOT_0900 - timedelta(days=2)
    )
    async with world.uow.locked() as tx:
        stored = await tx.add_proposal(proposal)

    found = await commitments(world, pool, SLOT_0900, SLOT_0900 + timedelta(hours=1))
    assert [c.key for c in found] == [("proposal", stored.id)]
    assert found[0].until == slot.occupied_until

    async with world.uow.locked() as tx:
        await tx.save_proposal(
            replace(stored, released_at=SLOT_0900, outcome=ProposalOutcome.EXPIRED)
        )
    assert await commitments(world, pool, SLOT_0900, SLOT_0900 + timedelta(hours=1)) == []


async def test_the_day_after_is_empty(world: World) -> None:
    pool = await staffed_pool(world)
    async with world.uow.locked() as tx:
        await tx.add_slot(pool_slot(world, pool))
    nothing = await commitments(
        world, pool, datetime(2026, 1, 6, tzinfo=UTC), datetime(2026, 1, 7, tzinfo=UTC)
    )
    assert nothing == []
