"""Planning writes (WP7b): groups, rules and overrides with ids, audit detail."""

from dataclasses import replace
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Scope
from app.domain.booking import BookingStatus
from app.domain.scheduling import Group, PhoneMode, ScheduleOverride, ScheduleRule
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, SLOT_0900, TEST_TOKEN_KEY, book, window

EVERYTHING = Scope(everything=True)


async def test_organisations_are_listed_within_the_scope(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)

    async with uow.read() as tx:
        everything = await tx.list_organisations(EVERYTHING)
        demo = await tx.list_organisations(Scope(organisations=frozenset({"demo"})))
        by_group = await tx.list_organisations(Scope(groups=frozenset({"demo"})))
        found = await tx.get_organisation("andere")
        missing = await tx.get_organisation("nope")

    assert [o.slug for o in everything] == ["andere", "demo"]
    assert [o.slug for o in demo] == ["demo"]
    assert by_group == []  # a group scope grants no organisation
    assert found is not None
    assert found.name == "Andere gemeente"
    assert missing is None


async def test_groups_are_added_and_saved(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        organisation = await tx.get_organisation("demo")
    assert organisation is not None

    async with uow.locked() as tx:
        stored = await tx.add_group(
            Group(0, organisation.id, "Vergunningen", "vergunningen", "Europe/Amsterdam", 20)
        )
    async with uow.locked() as tx:
        await tx.save_group(replace(stored, name="Vergunningen-team", phone_mode=PhoneMode.HIDDEN))
    async with uow.read() as tx:
        reloaded = await tx.get_group("vergunningen")

    assert stored.id > 0
    assert reloaded == replace(stored, name="Vergunningen-team", phone_mode=PhoneMode.HIDDEN)


async def test_schedule_items_carry_ids_and_stay_within_their_group(engine: AsyncEngine) -> None:
    own = await seed_group(engine, slug="eigen")
    other = await seed_group(engine, slug="ander")
    uow = SqlUnitOfWork(engine)

    async with uow.locked() as tx:
        rule_id = await tx.add_schedule_rule(
            own, ScheduleRule(2, window("13:00", "15:00", 4), valid_from=date(2026, 2, 1))
        )
        past = await tx.add_schedule_override(own, ScheduleOverride(date(2025, 12, 1), None))
        closed = await tx.add_schedule_override(own, ScheduleOverride(date(2026, 1, 6), None))
        changed = await tx.add_schedule_override(
            own, ScheduleOverride(date(2026, 1, 7), window("10:00", "11:00", 1))
        )
        other_rule = await tx.add_schedule_rule(other, ScheduleRule(3, window()))
    async with uow.read() as tx:
        rules, overrides = await tx.get_schedule_items(own, date(2026, 1, 1))
        _, all_overrides = await tx.get_schedule_items(own)

    assert {(r.id, r.weekday, r.window.capacity, r.valid_from) for r in rules} >= {
        (rule_id, 2, 4, date(2026, 2, 1))
    }
    assert all(r.id is not None for r in rules)
    assert [(o.id, o.window) for o in overrides] == [
        (closed, None),
        (changed, window("10:00", "11:00", 1)),
    ]
    assert past in {o.id for o in all_overrides}

    async with uow.locked() as tx:
        edited = ScheduleRule(4, window("08:00", "10:00", 6))
        assert not await tx.update_schedule_rule(own, other_rule, edited)  # not this group's
        assert await tx.update_schedule_rule(own, rule_id, edited)
    async with uow.read() as tx:
        rules, _ = await tx.get_schedule_items(own)
    assert [(r.weekday, r.window, r.valid_from) for r in rules if r.id == rule_id] == [
        (4, window("08:00", "10:00", 6), None)
    ]

    async with uow.locked() as tx:
        assert not await tx.delete_schedule_rule(own, other_rule)  # not this group's rule
        assert await tx.delete_schedule_rule(own, rule_id)
        assert not await tx.delete_schedule_override(other, closed)
        assert await tx.delete_schedule_override(own, closed)
    async with uow.read() as tx:
        rules, overrides = await tx.get_schedule_items(own, date(2026, 1, 1))
        other_rules, _ = await tx.get_schedule_items(other)

    assert rule_id not in {r.id for r in rules}
    assert [o.id for o in overrides] == [changed]
    assert other_rule in {r.id for r in other_rules}


async def test_future_bookings_are_counted_per_group(engine: AsyncEngine) -> None:
    group_id = await seed_group(engine, slug="aangifte")
    uow = SqlUnitOfWork(engine)
    service = BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY)
    receipt = await book(service, SLOT_0900)

    async with uow.read() as tx:
        before = await tx.count_future_bookings(group_id, NOW)
        after_start = await tx.count_future_bookings(group_id, SLOT_0900 + timedelta(minutes=1))
        booking = await tx.get_booking(receipt.booking.id or 0)
    assert booking is not None
    async with uow.locked() as tx:
        await tx.save_booking(replace(booking, status=BookingStatus.CANCELLED, cancelled_at=NOW))
    async with uow.read() as tx:
        cancelled = await tx.count_future_bookings(group_id, NOW)

    assert (before, after_start, cancelled) == (1, 0, 0)


async def test_audit_detail_is_stored(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    event = AuditEvent(NOW, "sub-1", "group.edit", Outcome.SUCCESS, "", "demo", "demo", "name")

    async with uow.locked() as tx:
        await tx.add_audit_event(event)
    async with uow.read() as tx:
        entries = await tx.list_audit_events(EVERYTHING)

    assert entries[0].event == event
