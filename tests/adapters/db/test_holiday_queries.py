"""Holiday calendars (ADR 0024): storage, choices per organisation and group, availability."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.authorization import Principal, Role
from app.domain.holiday_service import HolidayService
from app.domain.holidays import GroupHolidays, Holiday, HolidayCalendar, HolidaySource
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.support.domain import MONDAY, NOW, TEST_TOKEN_KEY

SUPER = Principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())


async def _calendars(uow: SqlUnitOfWork) -> tuple[HolidayCalendar, HolidayCalendar]:
    async with uow.locked() as tx:
        national = await tx.add_holiday_calendar(HolidayCalendar(0, "nl", "Nationaal"))
        regional = await tx.add_holiday_calendar(HolidayCalendar(0, "zuid", "Carnaval"))
    return national, regional


async def test_calendars_and_holidays_round_trip(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    national, regional = await _calendars(uow)

    async with uow.locked() as tx:
        kept = await tx.add_holiday(Holiday(national.id, date(2026, 4, 27), "Koningsdag"))
        await tx.add_holiday(Holiday(national.id, date(2026, 1, 1), "Nieuwjaarsdag"))
        other = await tx.add_holiday(Holiday(regional.id, date(2026, 2, 16), "Carnaval"))
        removed_elsewhere = await tx.delete_holiday(national.id, other)
    async with uow.read() as tx:
        everything = await tx.get_holiday_calendars()
        found = await tx.get_holiday_calendar("nl")
        missing = await tx.get_holiday_calendar("nope")
        spring = await tx.get_holidays({national.id}, date(2026, 3, 1), date(2026, 12, 31))
        both = await tx.get_holidays(
            {national.id, regional.id}, date(2026, 1, 1), date(2026, 2, 28)
        )

    assert [c.slug for c in everything] == ["zuid", "nl"]  # by name
    assert found == national
    assert missing is None
    assert spring == [Holiday(national.id, date(2026, 4, 27), "Koningsdag")]
    assert spring[0].id == kept
    assert [h.name for h in both] == ["Nieuwjaarsdag", "Carnaval"]
    assert removed_elsewhere is False


async def test_one_holiday_per_date_per_calendar(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    national, _ = await _calendars(uow)

    async def add_twice() -> None:
        async with uow.locked() as tx:
            await tx.add_holiday(Holiday(national.id, date(2026, 1, 1), "Nieuwjaarsdag"))
            await tx.add_holiday(Holiday(national.id, date(2026, 1, 1), "Nogmaals"))

    with pytest.raises(IntegrityError):
        await add_twice()


async def test_choices_of_organisations_and_groups(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    national, regional = await _calendars(uow)
    async with uow.read() as tx:
        group = await tx.get_group("demo")
        default = await tx.get_group_holidays(group.id if group else 0)
    assert group is not None
    choice = GroupHolidays(HolidaySource.OWN, frozenset({regional.id}), frozenset({MONDAY}))

    async with uow.locked() as tx:
        await tx.save_organisation_holiday_calendars(
            group.organisation_id, frozenset({national.id})
        )
        await tx.save_group_holidays(group.id, choice)
        await tx.save_group_holidays(group.id, choice)  # replacing twice is fine
    async with uow.read() as tx:
        organisation = await tx.get_organisation_holiday_calendars(group.organisation_id)
        stored = await tx.get_group_holidays(group.id)
        users = await tx.count_holiday_calendar_users(national.id)

    assert default == GroupHolidays()
    assert organisation == {national.id}
    assert stored == choice
    assert users == 1


async def test_a_calendar_in_use_is_protected_by_the_database(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    national, _ = await _calendars(uow)
    async with uow.read() as tx:
        organisation = await tx.get_organisation("demo")
    assert organisation is not None
    async with uow.locked() as tx:
        await tx.save_organisation_holiday_calendars(organisation.id, frozenset({national.id}))

    with pytest.raises(IntegrityError):
        async with uow.locked() as tx:
            await tx.delete_holiday_calendar(national.id)


async def test_deleting_a_calendar_removes_its_holidays(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    national, _ = await _calendars(uow)
    async with uow.locked() as tx:
        await tx.add_holiday(Holiday(national.id, date(2026, 1, 1), "Nieuwjaarsdag"))
        await tx.delete_holiday_calendar(national.id)
    async with uow.read() as tx:
        left = await tx.get_holidays({national.id}, date(2026, 1, 1), date(2026, 12, 31))

    assert left == []


async def test_a_national_holiday_closes_every_inheriting_group(engine: AsyncEngine) -> None:
    # The whole chain on SQL: superuser calendar → organisation choice → citizen availability.
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    clock = FixedClock(NOW)
    holidays = HolidayService(uow, clock, AdminService(uow, clock))
    booking = BookingService(uow, clock, TEST_TOKEN_KEY)
    before = await booking.availability("demo", MONDAY, MONDAY)

    calendar = await holidays.create_calendar(SUPER, "nl", "Nationale feestdagen")
    await holidays.add_holiday(SUPER, "nl", MONDAY, "Testfeestdag")
    await holidays.choose_for_organisation(SUPER, "demo", frozenset({calendar.id}))
    closed = await booking.availability("demo", MONDAY, MONDAY)
    await holidays.keep_open(SUPER, "demo", MONDAY)
    reopened = await booking.availability("demo", MONDAY, MONDAY)

    assert before
    assert closed == []
    assert reopened == before
