"""PoolPlanningService (ADR 0122 step 4): rights, audit, validation, shortfalls ahead."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.domain import audit
from app.domain.admin import AdminService
from app.domain.authorization import Scope
from app.domain.booking import BookingKind, Slot
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.capacity_service import CapacityService
from app.domain.errors import DomainError, ErrorCode
from app.domain.holidays import Holiday, HolidayCalendar
from app.domain.pool_planning import PoolPlanningService
from app.domain.scheduling import Offer, ScheduleOverride, ScheduleRule, TimeWindow
from tests.fakes import clock as fake_clock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.closures import confirmed_booking
from tests.support.domain import NOW, SLOT_0900

EVERYTHING = Scope(everything=True)
AMSTERDAM = "Europe/Amsterdam"


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


def build(uow: InMemoryUnitOfWork) -> tuple[PoolPlanningService, CapacityService]:
    clock = fake_clock.FixedClock(NOW)
    admin = AdminService(uow, clock)
    return PoolPlanningService(uow, clock, admin, 14), CapacityService(uow, clock, admin, 5)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> PoolPlanningService:
    return build(uow)[0]


async def pool(
    uow: InMemoryUnitOfWork, org: str = "demo", zone: str | None = AMSTERDAM, name: str = "Loket"
) -> int:
    """Add a counted pool of ``org`` (with a time zone unless ``zone`` is ``None``)."""
    made = await build(uow)[1].create(SUPER, org, f"{name.lower()}-{org}", name, set())
    assert made.id is not None
    if zone:
        async with uow.locked() as tx:
            await tx.set_pool_timezone(EVERYTHING, made.id, zone)
    return made.id


def monday(
    opens: int = 9, closes: int = 12, units: int = 2, offer: Offer = Offer.SCHEDULED
) -> ScheduleRule:
    return ScheduleRule(0, TimeWindow(time(opens), time(closes), units, None, offer))


def details(uow: InMemoryUnitOfWork) -> list[str]:
    return [e.detail for e in uow.state.audit if e.outcome is audit.Outcome.SUCCESS]


async def test_every_operation_needs_capacity_manage_and_a_refusal_is_audited(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    pid = await pool(uow)
    await service.add_rule(ORG_ADMIN, "demo", pid, monday())
    before = len(details(uow))

    for who in (GROUP_ADMIN, OUTSIDER, AUDITOR):
        for call in (
            service.staffing(who, "demo", pid),
            service.rule(who, "demo", pid, 1),
            service.override(who, "demo", pid, 1),
            service.set_timezone(who, "demo", pid, AMSTERDAM),
            service.add_rule(who, "demo", pid, monday()),
            service.update_rule(who, "demo", pid, 1, monday(8)),
            service.split_rule(who, "demo", pid, 1, time(10), 0),
            service.remove_rule(who, "demo", pid, 1),
            service.add_override(who, "demo", pid, ScheduleOverride(date(2026, 3, 2), None)),
            service.update_override(who, "demo", pid, 1, ScheduleOverride(date(2026, 3, 2), None)),
            service.remove_override(who, "demo", pid, 1),
        ):
            with pytest.raises(DomainError) as refused:
                await call
            assert refused.value.code is ErrorCode.NOT_PERMITTED

    assert len(details(uow)) == before
    assert len([e for e in uow.state.audit if e.outcome is audit.Outcome.DENIED]) == 33


async def test_blocks_need_a_time_zone_and_it_is_checked(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    pid = await pool(uow, zone=None)

    with pytest.raises(DomainError) as refused:
        await service.add_rule(ORG_ADMIN, "demo", pid, monday())
    page = await service.staffing(ORG_ADMIN, "demo", pid)
    with pytest.raises(DomainError) as unknown:
        await service.set_timezone(ORG_ADMIN, "demo", pid, "Mars/Olympus")

    assert (refused.value.code, refused.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "timezone",
    )
    assert (unknown.value.code, unknown.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "timezone",
    )
    assert (page.timezone, page.rules, page.overrides, page.shortfalls) == (None, (), (), ())
    with pytest.raises(DomainError) as missing:  # no zone: no block can exist
        await service.rule(ORG_ADMIN, "demo", pid, 1)
    assert missing.value.code is ErrorCode.CONFIGURATION_OBJECT_MISSING


async def test_the_zone_is_set_audited_and_a_repeat_changes_nothing(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    pid = await pool(uow, zone=None)

    assert await service.set_timezone(ORG_ADMIN, "demo", pid, AMSTERDAM) is True
    assert await service.set_timezone(ORG_ADMIN, "demo", pid, AMSTERDAM) is False
    assert await service.set_timezone(ORG_ADMIN, "demo", pid, "UTC") is True

    assert (await service.staffing(ORG_ADMIN, "demo", pid)).timezone == "UTC"
    assert [d for d in details(uow) if d.startswith("pool_staffing")] == [
        "pool_staffing.timezone,pool:loket-demo"
    ] * 2


async def test_a_block_is_added_changed_split_and_removed_with_audit_keys_only(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    pid = await pool(uow)

    rule_id = await service.add_rule(ORG_ADMIN, "demo", pid, monday())
    stored = await service.rule(ORG_ADMIN, "demo", pid, rule_id)
    assert await service.update_rule(ORG_ADMIN, "demo", pid, rule_id, stored) is False
    changed = ScheduleRule(0, TimeWindow(time(9), time(12), 3, "Balie", Offer.BOTH), id=rule_id)
    assert await service.update_rule(ORG_ADMIN, "demo", pid, rule_id, changed) is True
    second = await service.split_rule(ORG_ADMIN, "demo", pid, rule_id, time(11), 0, "Lunch")
    page = await service.staffing(ORG_ADMIN, "demo", pid)
    await service.remove_rule(ORG_ADMIN, "demo", pid, second)

    assert [(r.window.opens, r.window.closes, r.window.capacity) for r in page.rules] == [
        (time(9), time(11), 3),
        (time(11), time(12), 0),
    ]
    assert page.rules[0].window.offer is Offer.BOTH
    assert [d for d in details(uow) if d.startswith("pool_staffing")] == [
        f"pool_staffing.{what},pool:loket-demo"
        for what in ("rule_add", "rule_edit", "rule_split", "rule_remove")
    ]
    assert all("Balie" not in d and "Lunch" not in d for d in details(uow))


async def test_overlapping_blocks_and_blocks_with_subjects_are_refused(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    pid = await pool(uow)
    first = await service.add_rule(ORG_ADMIN, "demo", pid, monday())

    with pytest.raises(DomainError) as overlap:
        await service.add_rule(ORG_ADMIN, "demo", pid, monday(11, 13))
    with pytest.raises(DomainError) as split_outside:
        await service.split_rule(ORG_ADMIN, "demo", pid, first, time(12), 0)
    subjects = ScheduleRule(1, TimeWindow(time(9), time(10), 1, None, Offer.SCHEDULED, ("id",)))
    with pytest.raises(DomainError) as with_subjects:
        await service.add_rule(ORG_ADMIN, "demo", pid, subjects)

    assert overlap.value.field == "schedule_rules"
    assert split_outside.value.field == "split_at"
    assert with_subjects.value.field == "subjects"
    assert len((await service.staffing(ORG_ADMIN, "demo", pid)).rules) == 1


async def test_a_change_may_overlap_its_own_old_times(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    pid = await pool(uow)
    rule_id = await service.add_rule(ORG_ADMIN, "demo", pid, monday())

    moved = ScheduleRule(0, TimeWindow(time(10), time(13), 2, None), id=rule_id)

    assert await service.update_rule(ORG_ADMIN, "demo", pid, rule_id, moved) is True


async def test_exceptions_are_added_changed_and_removed_and_may_not_lie_in_the_past(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    pid = await pool(uow)
    day = date(2026, 1, 12)

    closed = await service.add_override(ORG_ADMIN, "demo", pid, ScheduleOverride(day, None))
    stored = await service.override(ORG_ADMIN, "demo", pid, closed)
    assert await service.update_override(ORG_ADMIN, "demo", pid, closed, stored) is False
    own = ScheduleOverride(day, TimeWindow(time(10), time(11), 1))
    assert await service.update_override(ORG_ADMIN, "demo", pid, closed, own) is True
    await service.remove_override(ORG_ADMIN, "demo", pid, closed)
    with pytest.raises(DomainError) as past:
        await service.add_override(
            ORG_ADMIN, "demo", pid, ScheduleOverride(date(2025, 12, 31), None)
        )
    kept = await service.add_override(ORG_ADMIN, "demo", pid, ScheduleOverride(day, None))
    with pytest.raises(DomainError) as past_change:
        await service.update_override(
            ORG_ADMIN, "demo", pid, kept, ScheduleOverride(date(2025, 12, 31), None)
        )

    assert past.value.field == "day"
    assert past_change.value.field == "day"
    assert [d for d in details(uow) if "override" in d] == [
        f"pool_staffing.{what},pool:loket-demo"
        for what in ("override_add", "override_edit", "override_remove", "override_add")
    ]


async def test_an_unknown_block_or_exception_is_missing_and_changes_nothing(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    pid, other = await pool(uow), await pool(uow, name="Advies")
    mine = await service.add_rule(ORG_ADMIN, "demo", pid, monday())
    attempts = (
        service.rule(ORG_ADMIN, "demo", other, mine),
        service.update_rule(ORG_ADMIN, "demo", other, mine, monday(8)),
        service.split_rule(ORG_ADMIN, "demo", other, mine, time(10), 0),
        service.remove_rule(ORG_ADMIN, "demo", other, mine),
        service.override(ORG_ADMIN, "demo", pid, 99),
        service.update_override(
            ORG_ADMIN, "demo", pid, 99, ScheduleOverride(date(2026, 2, 2), None)
        ),
        service.remove_override(ORG_ADMIN, "demo", pid, 99),
    )

    for attempt in attempts:
        with pytest.raises(DomainError) as refused:
            await attempt
        assert refused.value.code is ErrorCode.CONFIGURATION_OBJECT_MISSING

    assert len((await service.staffing(ORG_ADMIN, "demo", pid)).rules) == 1


async def test_another_organisations_archived_and_legacy_pools_are_not_there(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    theirs = await pool(uow, "andere")
    archived = await pool(uow, name="Oud")
    await build(uow)[1].archive(SUPER, "demo", archived)
    async with uow.locked() as tx:
        legacy = await tx.save_capacity_pool(
            EVERYTHING, CapacityPool(None, 1, "Demo", PoolMode.LEGACY_GROUP, group_id=1)
        )

    for pid, code in (
        (theirs, ErrorCode.CONFIGURATION_OBJECT_MISSING),
        (archived, ErrorCode.CONFIGURATION_OBJECT_MISSING),
        (legacy.id, ErrorCode.INVALID_CONFIGURATION),
    ):
        for attempt in (
            service.staffing(ORG_ADMIN, "demo", pid or 0),
            service.add_rule(ORG_ADMIN, "demo", pid or 0, monday()),
            service.set_timezone(ORG_ADMIN, "demo", pid or 0, AMSTERDAM),
        ):
            with pytest.raises(DomainError) as refused:
                await attempt
            assert refused.value.code is code

    assert not [d for d in details(uow) if d.startswith("pool_staffing")]


async def commit(
    uow: InMemoryUnitOfWork, pid: int, count: int, start: datetime = SLOT_0900
) -> None:
    """Hold ``count`` places in a 30-minute slot of pool ``pid`` starting at ``start``."""
    end = start + timedelta(minutes=30)
    async with uow.locked() as tx:
        slot = await tx.add_slot(
            Slot(None, 1, start, end, 2, BookingKind.SCHEDULED, pid, end + timedelta(minutes=5))
        )
        for number in range(count):
            await tx.add_booking(
                confirmed_booking(
                    id=None,
                    slot_id=slot.id,
                    start=start,
                    end=end,
                    token_hash=f"{number + start.hour * 10:064d}",
                    public_ref=f"ref{number + start.hour * 10:020d}",
                )
            )


async def test_a_shortfall_is_listed_where_commitments_exceed_the_staffed_units(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    pid = await pool(uow)
    await service.add_rule(ORG_ADMIN, "demo", pid, monday(9, 12, units=1))
    await commit(uow, pid, 2)  # Monday 5 January 09:00 CET, two places, one person

    page = await service.staffing(ORG_ADMIN, "demo", pid)

    assert [(s.start, s.end, s.units, s.load) for s in page.shortfalls] == [
        (SLOT_0900, SLOT_0900 + timedelta(minutes=35), 1, 2)
    ]
    assert page.horizon_days == 14


async def test_dropping_the_staffing_shows_the_shortage_and_touches_no_booking(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    pid = await pool(uow)
    rule_id = await service.add_rule(ORG_ADMIN, "demo", pid, monday(9, 12, units=2))
    await commit(uow, pid, 2)
    assert (await service.staffing(ORG_ADMIN, "demo", pid)).shortfalls == ()
    bookings = dict(uow.state.bookings)

    drop = ScheduleRule(0, TimeWindow(time(9), time(12), 1), id=rule_id)
    await service.update_rule(ORG_ADMIN, "demo", pid, rule_id, drop)

    assert len((await service.staffing(ORG_ADMIN, "demo", pid)).shortfalls) == 1
    assert uow.state.bookings == bookings


async def test_a_closed_exception_leaves_a_shortfall_and_commitments_beyond_the_horizon_are_ignored(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    pid = await pool(uow)
    await service.add_rule(ORG_ADMIN, "demo", pid, monday(9, 12, units=2))
    await commit(uow, pid, 1)
    await commit(uow, pid, 1, SLOT_0900 + timedelta(days=60))
    await service.add_override(ORG_ADMIN, "demo", pid, ScheduleOverride(date(2026, 1, 5), None))

    page = await service.staffing(ORG_ADMIN, "demo", pid)

    assert [(s.units, s.load) for s in page.shortfalls] == [(0, 1)]  # only inside the 14 days
    assert [o.day for o in page.overrides] == [date(2026, 1, 5)]


async def test_a_holiday_of_the_organisations_calendars_closes_the_pool_unless_an_exception_wins(
    service: PoolPlanningService, uow: InMemoryUnitOfWork
) -> None:
    pid = await pool(uow)
    await service.add_rule(ORG_ADMIN, "demo", pid, monday(9, 12, units=2))
    await commit(uow, pid, 1)
    async with uow.locked() as tx:
        calendar = await tx.add_holiday_calendar(HolidayCalendar(0, "nl", "Nederland"))
        await tx.add_holiday(Holiday(calendar.id, date(2026, 1, 5), "Feestdag"))
        await tx.save_organisation_holiday_calendars(1, frozenset({calendar.id}))

    closed = await service.staffing(ORG_ADMIN, "demo", pid)
    await service.add_override(
        ORG_ADMIN, "demo", pid, ScheduleOverride(date(2026, 1, 5), TimeWindow(time(9), time(12), 2))
    )
    open_again = await service.staffing(ORG_ADMIN, "demo", pid)

    assert [(s.units, s.load) for s in closed.shortfalls] == [(0, 1)]
    assert open_again.shortfalls == ()


async def test_past_exceptions_are_not_listed_and_the_zone_decides_today(
    uow: InMemoryUnitOfWork,
) -> None:
    clock = fake_clock.FixedClock(datetime(2026, 1, 5, 23, 30, tzinfo=UTC))  # Tuesday 00:30 CET
    admin = AdminService(uow, clock)
    service = PoolPlanningService(uow, clock, admin, 14)
    pid = await pool(uow)
    async with uow.locked() as tx:
        await tx.add_pool_override(EVERYTHING, pid, ScheduleOverride(date(2026, 1, 5), None))
        await tx.add_pool_override(EVERYTHING, pid, ScheduleOverride(date(2026, 1, 6), None))

    page = await service.staffing(ORG_ADMIN, "demo", pid)

    assert [o.day for o in page.overrides] == [date(2026, 1, 6)]
    with pytest.raises(DomainError):  # 5 January is past in Amsterdam
        await service.add_override(ORG_ADMIN, "demo", pid, ScheduleOverride(date(2026, 1, 5), None))
