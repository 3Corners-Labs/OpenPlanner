from datetime import date

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.holiday_service import DayState, HolidayService, effective_schedule
from app.domain.holidays import GroupHolidays, Holiday, HolidayCalendar, HolidaySource
from app.domain.planning import PlanningService
from app.domain.scheduling import ScheduleOverride, ScheduleRule
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import (
    GROUP_ADMIN,
    ORG_ADMIN,
    OUTSIDER,
    SUPER,
    audit_rows,
    two_organisations,
)
from tests.support.domain import MONDAY, NOW, SLOT_0900, TEST_TOKEN_KEY, book, window

NATIONAL, REGIONAL = 50, 51
TUESDAY = date(2026, 1, 6)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = two_organisations(next_id=100)
    state.rules[1] = [ScheduleRule(0, window()), ScheduleRule(1, window())]
    state.holiday_calendars = {
        NATIONAL: HolidayCalendar(NATIONAL, "nl", "Nationale feestdagen"),
        REGIONAL: HolidayCalendar(REGIONAL, "zuid", "Carnaval"),
    }
    state.holidays = {
        60: Holiday(NATIONAL, MONDAY, "Testfeestdag", id=60),
        61: Holiday(REGIONAL, TUESDAY, "Carnaval", id=61),
    }
    state.organisation_holidays = {1: frozenset({NATIONAL})}
    return InMemoryUnitOfWork(state)


@pytest.fixture
def holidays(uow: InMemoryUnitOfWork) -> HolidayService:
    clock = FixedClock(NOW)
    return HolidayService(uow, clock, AdminService(uow, clock))


async def _closed_days(uow: InMemoryUnitOfWork, first: date, last: date) -> list[date]:
    async with uow.read() as tx:
        _, overrides = await effective_schedule(tx, uow.state.groups[1], first, last)
    return [o.day for o in overrides if o.window is None]


# --- resolution ------------------------------------------------------------------------------


async def test_groups_inherit_the_organisation_calendars(uow: InMemoryUnitOfWork) -> None:
    assert await _closed_days(uow, MONDAY, TUESDAY) == [MONDAY]


async def test_a_group_can_choose_its_own_calendars_or_none(uow: InMemoryUnitOfWork) -> None:
    uow.state.group_holidays[1] = GroupHolidays(HolidaySource.OWN, frozenset({REGIONAL}))
    assert await _closed_days(uow, MONDAY, TUESDAY) == [TUESDAY]

    uow.state.group_holidays[1] = GroupHolidays(HolidaySource.OWN, frozenset())
    assert await _closed_days(uow, MONDAY, TUESDAY) == []


async def test_a_day_kept_open_follows_the_weekly_rules(uow: InMemoryUnitOfWork) -> None:
    uow.state.group_holidays[1] = GroupHolidays(open_days=frozenset({MONDAY}))

    assert await _closed_days(uow, MONDAY, TUESDAY) == []


async def test_holidays_close_citizen_availability(uow: InMemoryUnitOfWork) -> None:
    service = BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY)

    slots = await service.availability("demo", MONDAY, TUESDAY)

    assert {s.start.date() for s in slots} == {TUESDAY}
    with pytest.raises(DomainError) as caught:
        await book(service, SLOT_0900, slug="demo")
    assert caught.value.code is ErrorCode.SLOT_UNAVAILABLE


async def test_the_group_exception_on_a_holiday_wins(uow: InMemoryUnitOfWork) -> None:
    uow.state.overrides[1] = [ScheduleOverride(MONDAY, window("09:00", "09:30", 1))]
    service = BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY)

    slots = await service.availability("demo", MONDAY, MONDAY)

    assert [s.start for s in slots] == [SLOT_0900]


async def test_the_admin_preview_leaves_out_holidays(uow: InMemoryUnitOfWork) -> None:
    clock = FixedClock(NOW)
    planning = PlanningService(uow, clock, AdminService(uow, clock))

    preview = await planning.preview(GROUP_ADMIN, "demo", days=7)

    assert [d.day for d in preview] == [TUESDAY]


# --- calendars (superuser) -------------------------------------------------------------------


async def test_superuser_creates_and_fills_a_calendar(
    holidays: HolidayService, uow: InMemoryUnitOfWork
) -> None:
    calendar = await holidays.create_calendar(SUPER, "rijk", "Rijksoverheid")
    added = await holidays.add_holidays(
        SUPER, "rijk", [(date(2026, 1, 1), "Nieuwjaarsdag"), (date(2026, 4, 27), "Koningsdag")]
    )
    again = await holidays.add_holidays(SUPER, "rijk", [(date(2026, 1, 1), "Nieuwjaarsdag")])
    detail = await holidays.calendar(SUPER, "rijk", 2026)

    assert (added, again) == (2, 0)
    assert detail.calendar == calendar
    assert [h.name for h in detail.holidays] == ["Nieuwjaarsdag", "Koningsdag"]
    assert detail.users == 0
    assert audit_rows(uow) == [
        ("s-1", "holidays.manage", Outcome.SUCCESS, "rijk:calendar.add", None),
        ("s-1", "holidays.manage", Outcome.SUCCESS, "rijk:holiday.add", None),
    ]


async def test_adding_nothing_changes_nothing(holidays: HolidayService) -> None:
    assert await holidays.add_holidays(SUPER, "nl", []) == 0


async def test_a_calendar_slug_is_unique(holidays: HolidayService) -> None:
    with pytest.raises(DomainError) as caught:
        await holidays.create_calendar(SUPER, "nl", "Nog een")

    assert caught.value.code is ErrorCode.SLUG_TAKEN


async def test_one_holiday_per_date_per_calendar(holidays: HolidayService) -> None:
    holiday_id = await holidays.add_holiday(SUPER, "nl", date(2026, 12, 25), "Eerste Kerstdag")

    with pytest.raises(DomainError) as caught:
        await holidays.add_holiday(SUPER, "nl", date(2026, 12, 25), "Kerstmis")

    assert holiday_id
    assert caught.value.field == "holiday_day"


async def test_remove_a_holiday_only_from_its_own_calendar(
    holidays: HolidayService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as caught:
        await holidays.remove_holiday(SUPER, "nl", 61)  # 61 belongs to "zuid"
    await holidays.remove_holiday(SUPER, "nl", 60)

    assert caught.value.code is ErrorCode.HOLIDAY_NOT_FOUND
    assert set(uow.state.holidays) == {61}


async def test_a_calendar_in_use_cannot_be_deleted(
    holidays: HolidayService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as caught:
        await holidays.delete_calendar(SUPER, "nl")
    await holidays.delete_calendar(SUPER, "zuid")

    assert caught.value.code is ErrorCode.HOLIDAY_CALENDAR_IN_USE
    assert set(uow.state.holiday_calendars) == {NATIONAL}
    assert set(uow.state.holidays) == {60}


async def test_unknown_calendar(holidays: HolidayService) -> None:
    with pytest.raises(DomainError) as caught:
        await holidays.calendar(SUPER, "nope", 2026)

    assert caught.value.code is ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND


@pytest.mark.parametrize("principal", [ORG_ADMIN, GROUP_ADMIN])
async def test_only_the_superuser_maintains_calendars(
    holidays: HolidayService, uow: InMemoryUnitOfWork, principal: Principal
) -> None:
    with pytest.raises(DomainError) as caught:
        await holidays.add_holiday(principal, "nl", date(2026, 12, 25), "Kerst")

    assert caught.value.code is ErrorCode.NOT_PERMITTED
    assert [e.outcome for e in uow.state.audit] == [Outcome.DENIED]


# --- organisations ---------------------------------------------------------------------------


async def test_overview_lists_every_calendar_and_the_organisations_in_scope(
    holidays: HolidayService,
) -> None:
    mine = await holidays.overview(ORG_ADMIN)
    everything = await holidays.overview(SUPER)

    assert [c.slug for c in mine.calendars] == ["zuid", "nl"]  # by name
    assert [o.slug for o in mine.organisations] == ["demo"]
    assert [o.slug for o in everything.organisations] == ["andere", "demo"]
    with pytest.raises(DomainError):
        await holidays.overview(GROUP_ADMIN)


async def test_org_admin_chooses_calendars_for_their_organisation(
    holidays: HolidayService, uow: InMemoryUnitOfWork
) -> None:
    changed = await holidays.choose_for_organisation(
        ORG_ADMIN, "demo", frozenset({NATIONAL, REGIONAL})
    )
    unchanged = await holidays.choose_for_organisation(
        ORG_ADMIN, "demo", frozenset({NATIONAL, REGIONAL})
    )
    choice = await holidays.organisation_choice(ORG_ADMIN, "demo")

    assert (changed, unchanged) == (True, False)
    assert choice.chosen == {NATIONAL, REGIONAL}
    assert await _closed_days(uow, MONDAY, TUESDAY) == [MONDAY, TUESDAY]
    assert audit_rows(uow) == [("o-1", "holidays.assign", Outcome.SUCCESS, "calendars", None)]
    assert uow.state.audit[0].organisation == "demo"


async def test_org_admin_cannot_choose_for_another_organisation(
    holidays: HolidayService,
) -> None:
    with pytest.raises(DomainError) as caught:
        await holidays.choose_for_organisation(OUTSIDER, "demo", frozenset())

    assert caught.value.code is ErrorCode.NOT_PERMITTED


async def test_unknown_calendars_are_refused(holidays: HolidayService) -> None:
    with pytest.raises(DomainError) as caught:
        await holidays.choose_for_organisation(SUPER, "demo", frozenset({999}))

    assert caught.value.field == "calendars"


# --- groups ----------------------------------------------------------------------------------


async def test_group_page_shows_upcoming_holidays_with_their_state(
    holidays: HolidayService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.holidays[62] = Holiday(NATIONAL, date(2026, 4, 27), "Koningsdag", id=62)
    uow.state.holidays[63] = Holiday(NATIONAL, date(2025, 12, 25), "Kerst (voorbij)", id=63)
    uow.state.overrides[1] = [ScheduleOverride(date(2026, 4, 27), None)]
    uow.state.group_holidays[1] = GroupHolidays(open_days=frozenset({MONDAY}))

    view = await holidays.group_holidays(GROUP_ADMIN, "demo")

    assert view.inherited == {NATIONAL}
    assert [(u.holiday.day, u.state) for u in view.upcoming] == [
        (MONDAY, DayState.OPEN),
        (date(2026, 4, 27), DayState.EXCEPTION),
    ]


async def test_group_without_calendars_has_no_upcoming_holidays(
    holidays: HolidayService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.group_holidays[1] = GroupHolidays(HolidaySource.OWN, frozenset())

    assert (await holidays.group_holidays(GROUP_ADMIN, "demo")).upcoming == []


async def test_group_admin_overrides_the_organisation_choice(
    holidays: HolidayService, uow: InMemoryUnitOfWork
) -> None:
    changed = await holidays.set_group_calendars(
        GROUP_ADMIN, "demo", HolidaySource.OWN, frozenset({REGIONAL})
    )
    unchanged = await holidays.set_group_calendars(
        GROUP_ADMIN, "demo", HolidaySource.OWN, frozenset({REGIONAL})
    )
    back = await holidays.set_group_calendars(
        GROUP_ADMIN, "demo", HolidaySource.INHERIT, frozenset()
    )

    assert (changed, unchanged, back) == (True, False, True)
    # Inheriting again keeps the own choice for later.
    assert uow.state.group_holidays[1] == GroupHolidays(
        HolidaySource.INHERIT, frozenset({REGIONAL})
    )
    assert [e.detail for e in uow.state.audit] == ["holidays.calendars", "holidays.calendars"]
    assert {(e.organisation, e.group) for e in uow.state.audit} == {("demo", "demo")}


async def test_a_group_cannot_choose_unknown_calendars(holidays: HolidayService) -> None:
    with pytest.raises(DomainError) as caught:
        await holidays.set_group_calendars(SUPER, "demo", HolidaySource.OWN, frozenset({999}))

    assert caught.value.field == "calendars"


async def test_keep_a_holiday_open_and_close_it_again(
    holidays: HolidayService, uow: InMemoryUnitOfWork
) -> None:
    await holidays.keep_open(GROUP_ADMIN, "demo", MONDAY)
    await holidays.keep_open(GROUP_ADMIN, "demo", MONDAY)  # no second audit event
    assert await _closed_days(uow, MONDAY, MONDAY) == []

    await holidays.close_again(GROUP_ADMIN, "demo", MONDAY)
    assert await _closed_days(uow, MONDAY, MONDAY) == [MONDAY]
    assert [e.detail for e in uow.state.audit] == ["holiday.open", "holiday.close"]


@pytest.mark.parametrize("day", [TUESDAY, date(2025, 12, 31)])
async def test_only_upcoming_holidays_of_the_group_can_be_kept_open(
    holidays: HolidayService, day: date
) -> None:
    # Tuesday is a holiday of a calendar the group does not follow; 31-12-2025 lies before now.
    with pytest.raises(DomainError) as caught:
        await holidays.keep_open(GROUP_ADMIN, "demo", day)

    assert caught.value.code is ErrorCode.HOLIDAY_NOT_FOUND


async def test_keep_open_without_any_calendar(
    holidays: HolidayService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.group_holidays[1] = GroupHolidays(HolidaySource.OWN, frozenset())

    with pytest.raises(DomainError) as caught:
        await holidays.keep_open(GROUP_ADMIN, "demo", MONDAY)

    assert caught.value.code is ErrorCode.HOLIDAY_NOT_FOUND


async def test_group_admin_cannot_touch_another_group(holidays: HolidayService) -> None:
    with pytest.raises(DomainError) as caught:
        await holidays.set_group_calendars(
            GROUP_ADMIN, "andere-team", HolidaySource.OWN, frozenset()
        )

    assert caught.value.code is ErrorCode.NOT_PERMITTED
