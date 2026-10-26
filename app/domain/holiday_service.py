"""Holiday use cases: maintain calendars, choose them per organisation and group (ADR 0021).

Every operation asks ``AdminService.authorize`` first and is audited in the same transaction,
with keys of what changed (``calendar.add``, ``holiday.open``), never names or dates.

    overview                      holidays.assign  anywhere       calendars and organisations
    calendar / create / delete,
    add/remove holiday(s)         holidays.manage  global         superuser only
    organisation_choice / choose  holidays.assign  organisation   organisation admins too
    group_holidays / set_group_calendars / keep_open / close_again
                                  schedule.manage  group          group admins too

``effective_schedule`` is what the citizen side uses: the group's rules and exceptions with
its holidays added as closed days (``holidays.with_holidays``).
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum

from app.domain.admin import AdminService, Authorization, record_change, require
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.errors import DomainError, ErrorCode
from app.domain.holidays import (
    GroupHolidays,
    Holiday,
    HolidayCalendar,
    HolidaySource,
    closures,
    with_holidays,
)
from app.domain.lookups import group_by_slug, organisation_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group, Organisation, ScheduleOverride, ScheduleRule

UPCOMING_DAYS = 366
"""How far ahead the group page lists holidays."""


async def effective_closures(
    tx: Transaction, group: Group, first_day: date, last_day: date
) -> list[Holiday]:
    """Return the holidays on which ``group`` is closed between the two dates.

    The group's own exceptions are not considered here; ``with_holidays`` lets them win.
    """
    choice = await tx.get_group_holidays(group.id)
    inherited = await tx.get_organisation_holiday_calendars(group.organisation_id)
    calendar_ids = choice.calendars(inherited)
    if not calendar_ids:
        return []
    holidays = await tx.get_holidays(calendar_ids, first_day, last_day)
    return closures(holidays, choice.open_days)


async def effective_schedule(
    tx: Transaction, group: Group, first_day: date, last_day: date
) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
    """Return the group's rules and its overrides between the dates, holidays included."""
    rules, overrides = await tx.get_schedule(group.id, first_day, last_day)
    holidays = await effective_closures(tx, group, first_day, last_day)
    return rules, with_holidays(overrides, holidays)


@dataclass(frozen=True)
class HolidayOverview:
    """The holiday start page: every calendar and the organisations the admin may assign."""

    calendars: list[HolidayCalendar]
    organisations: list[Organisation]


@dataclass(frozen=True)
class CalendarDetail:
    """One calendar with its holidays of one year and how many choose it."""

    calendar: HolidayCalendar
    year: int
    holidays: list[Holiday]
    users: int


@dataclass(frozen=True)
class OrganisationChoice:
    """An organisation, every calendar and the ones it chose."""

    organisation: Organisation
    calendars: list[HolidayCalendar]
    chosen: frozenset[int]


class DayState(StrEnum):
    """How a group treats an upcoming holiday."""

    CLOSED = "closed"
    OPEN = "open"
    """Kept open as usual by the group."""
    EXCEPTION = "exception"
    """The group has its own exception on that date, which wins."""


@dataclass(frozen=True)
class UpcomingHoliday:
    """An upcoming holiday of the group's calendars and what the group does on it."""

    holiday: Holiday
    state: DayState


@dataclass(frozen=True)
class GroupHolidayView:
    """The group's holiday page: its choice, what it inherits and the coming holidays."""

    choice: GroupHolidays
    inherited: frozenset[int]
    calendars: list[HolidayCalendar]
    upcoming: list[UpcomingHoliday]


class HolidayService:
    """Application service for holiday calendars.

    Args:
        uow: Opens database transactions.
        clock: Current time, for "upcoming" and audit events.
        admin: Authorises every operation and audits refusals.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, admin: AdminService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin

    # --- overview ---------------------------------------------------------------------------

    async def overview(self, principal: Principal) -> HolidayOverview:
        """Return every calendar and the organisations ``principal`` may assign calendars to.

        Raises:
            DomainError: ``not_permitted``.
        """
        require(await self._admin.authorize_anywhere(principal, Permission.HOLIDAYS_ASSIGN))
        async with self._uow.read() as tx:
            calendars = await tx.get_holiday_calendars()
            scope = scope_for(principal, Permission.HOLIDAYS_ASSIGN)
            organisations = await tx.list_organisations(scope)
        return HolidayOverview(calendars, organisations)

    # --- calendars (superuser) --------------------------------------------------------------

    async def calendar(self, principal: Principal, slug: str, year: int) -> CalendarDetail:
        """Return a calendar with its holidays in ``year``.

        Raises:
            DomainError: ``not_permitted`` or ``holiday_calendar_not_found``.
        """
        await self._manage(principal)
        async with self._uow.read() as tx:
            calendar = await _calendar(tx, slug)
            holidays = await tx.get_holidays({calendar.id}, date(year, 1, 1), date(year, 12, 31))
            users = await tx.count_holiday_calendar_users(calendar.id)
        return CalendarDetail(calendar, year, holidays, users)

    async def create_calendar(self, principal: Principal, slug: str, name: str) -> HolidayCalendar:
        """Create an empty calendar.

        Raises:
            DomainError: ``not_permitted``, ``slug_taken`` or ``invalid_configuration``
                (``slug`` or ``name``).
        """
        await self._manage(principal)
        calendar = HolidayCalendar(0, slug, name)
        async with self._uow.locked() as tx:
            if await tx.get_holiday_calendar(slug) is not None:
                raise DomainError(ErrorCode.SLUG_TAKEN, "slug")
            stored = await tx.add_holiday_calendar(calendar)
            await self._audit_global(tx, principal, slug, "calendar.add")
        return stored

    async def delete_calendar(self, principal: Principal, slug: str) -> None:
        """Delete a calendar no organisation or group uses any more.

        Raises:
            DomainError: ``not_permitted``, ``holiday_calendar_not_found`` or
                ``holiday_calendar_in_use``.
        """
        await self._manage(principal)
        async with self._uow.locked() as tx:
            calendar = await _calendar(tx, slug)
            if await tx.count_holiday_calendar_users(calendar.id):
                raise DomainError(ErrorCode.HOLIDAY_CALENDAR_IN_USE)
            await tx.delete_holiday_calendar(calendar.id)
            await self._audit_global(tx, principal, slug, "calendar.remove")

    async def add_holiday(self, principal: Principal, slug: str, day: date, name: str) -> int:
        """Add one holiday to a calendar.

        Raises:
            DomainError: ``not_permitted``, ``holiday_calendar_not_found`` or
                ``invalid_configuration`` (``name``, or ``holiday_day`` when the calendar
                already has that date).
        """
        await self._manage(principal)
        async with self._uow.locked() as tx:
            calendar = await _calendar(tx, slug)
            holiday = Holiday(calendar.id, day, name)
            if await tx.get_holidays({calendar.id}, day, day):
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "holiday_day")
            holiday_id = await tx.add_holiday(holiday)
            await self._audit_global(tx, principal, slug, "holiday.add")
        return holiday_id

    async def add_holidays(
        self, principal: Principal, slug: str, entries: Sequence[tuple[date, str]]
    ) -> int:
        """Add several holidays at once (for example a year of national holidays).

        Dates the calendar already has are skipped, so running it twice changes nothing.
        Returns how many were added.

        Raises:
            DomainError: ``not_permitted``, ``holiday_calendar_not_found`` or
                ``invalid_configuration`` (``name``).
        """
        await self._manage(principal)
        if not entries:
            return 0
        async with self._uow.locked() as tx:
            calendar = await _calendar(tx, slug)
            first, last = min(d for d, _ in entries), max(d for d, _ in entries)
            taken = {h.day for h in await tx.get_holidays({calendar.id}, first, last)}
            new = [Holiday(calendar.id, d, n) for d, n in entries if d not in taken]
            for holiday in new:
                await tx.add_holiday(holiday)
            if new:
                await self._audit_global(tx, principal, slug, "holiday.add")
        return len(new)

    async def remove_holiday(self, principal: Principal, slug: str, holiday_id: int) -> None:
        """Remove a holiday from a calendar.

        Raises:
            DomainError: ``not_permitted``, ``holiday_calendar_not_found`` or
                ``holiday_not_found``.
        """
        await self._manage(principal)
        async with self._uow.locked() as tx:
            calendar = await _calendar(tx, slug)
            if not await tx.delete_holiday(calendar.id, holiday_id):
                raise DomainError(ErrorCode.HOLIDAY_NOT_FOUND)
            await self._audit_global(tx, principal, slug, "holiday.remove")

    # --- organisations ----------------------------------------------------------------------

    async def organisation_choice(self, principal: Principal, slug: str) -> OrganisationChoice:
        """Return the organisation's calendar choice with every calendar to choose from.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._assign(principal, slug)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            calendars = await tx.get_holiday_calendars()
            chosen = await tx.get_organisation_holiday_calendars(organisation.id)
        return OrganisationChoice(organisation, calendars, chosen)

    async def choose_for_organisation(
        self, principal: Principal, slug: str, calendar_ids: frozenset[int]
    ) -> bool:
        """Replace the organisation's calendars; return whether anything changed.

        Groups that inherit follow at once; groups with their own choice are not touched.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``invalid_configuration`` (``calendars``) for an unknown calendar.
        """
        await self._assign(principal, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            await _require_known(tx, calendar_ids)
            current = await tx.get_organisation_holiday_calendars(organisation.id)
            if current == calendar_ids:
                return False
            await tx.save_organisation_holiday_calendars(organisation.id, calendar_ids)
            await record_change(
                tx,
                self._clock.now(),
                principal,
                Permission.HOLIDAYS_ASSIGN,
                slug,
                None,
                "calendars",
            )
        return True

    # --- groups -----------------------------------------------------------------------------

    async def group_holidays(self, principal: Principal, slug: str) -> GroupHolidayView:
        """Return the group's choice and its holidays from today on, with their state.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        await self._schedule(principal, slug)
        async with self._uow.read() as tx:
            group = await group_by_slug(tx, slug)
            choice = await tx.get_group_holidays(group.id)
            inherited = await tx.get_organisation_holiday_calendars(group.organisation_id)
            calendars = await tx.get_holiday_calendars()
            first = group.today(self._clock.now())
            last = first + timedelta(days=UPCOMING_DAYS)
            ids = choice.calendars(inherited)
            holidays = await tx.get_holidays(ids, first, last) if ids else []
            _, overrides = await tx.get_schedule(group.id, first, last)
        excepted = {o.day for o in overrides}
        upcoming = [
            UpcomingHoliday(h, _state(h.day, choice.open_days, excepted))
            for h in closures(holidays, ())
        ]
        return GroupHolidayView(choice, inherited, calendars, upcoming)

    async def set_group_calendars(
        self,
        principal: Principal,
        slug: str,
        source: HolidaySource,
        calendar_ids: frozenset[int],
    ) -> bool:
        """Let the group inherit, or give it its own calendars; return whether it changed.

        With ``inherit`` the submitted calendars are ignored and the own ones are kept.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``invalid_configuration``
                (``calendars``) for an unknown calendar.
        """
        await self._schedule(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            current = await tx.get_group_holidays(group.id)
            own = current.calendar_ids if source is HolidaySource.INHERIT else calendar_ids
            await _require_known(tx, own)
            updated = replace(current, source=source, calendar_ids=own)
            if updated == current:
                return False
            await tx.save_group_holidays(group.id, updated)
            await self._audit_group(tx, principal, group, "holidays.calendars")
        return True

    async def keep_open(self, principal: Principal, slug: str, day: date) -> None:
        """Keep the group open as usual on the holiday ``day``.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``holiday_not_found``
                when ``day`` is not an upcoming holiday of the group.
        """
        await self._set_open(principal, slug, day, keep_open=True)

    async def close_again(self, principal: Principal, slug: str, day: date) -> None:
        """Undo ``keep_open``: the group is closed on the holiday ``day`` again.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``holiday_not_found``.
        """
        await self._set_open(principal, slug, day, keep_open=False)

    # --- helpers ----------------------------------------------------------------------------

    async def _set_open(
        self, principal: Principal, slug: str, day: date, *, keep_open: bool
    ) -> None:
        """Add or remove ``day`` from the days the group keeps open, and audit it."""
        await self._schedule(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            if day < group.today(self._clock.now()):
                raise DomainError(ErrorCode.HOLIDAY_NOT_FOUND)
            current = await tx.get_group_holidays(group.id)
            inherited = await tx.get_organisation_holiday_calendars(group.organisation_id)
            ids = current.calendars(inherited)
            if not ids or not await tx.get_holidays(ids, day, day):
                raise DomainError(ErrorCode.HOLIDAY_NOT_FOUND)
            days = current.open_days | {day} if keep_open else current.open_days - {day}
            if days == current.open_days:
                return
            await tx.save_group_holidays(group.id, replace(current, open_days=days))
            await self._audit_group(
                tx, principal, group, "holiday.open" if keep_open else "holiday.close"
            )

    async def _manage(self, principal: Principal) -> Authorization:
        """Authorise ``holidays.manage`` (global)."""
        return require(await self._admin.authorize(principal, Permission.HOLIDAYS_MANAGE))

    async def _assign(self, principal: Principal, slug: str) -> Authorization:
        """Authorise ``holidays.assign`` on organisation ``slug``."""
        return require(
            await self._admin.authorize(
                principal, Permission.HOLIDAYS_ASSIGN, organisation_slug=slug
            )
        )

    async def _schedule(self, principal: Principal, slug: str) -> Authorization:
        """Authorise ``schedule.manage`` on group ``slug``."""
        return require(
            await self._admin.authorize(principal, Permission.SCHEDULE_MANAGE, group_slug=slug)
        )

    async def _audit_global(
        self, tx: Transaction, principal: Principal, slug: str, detail: str
    ) -> None:
        """Record a change of calendar ``slug`` (global: only the superuser sees it)."""
        await record_change(
            tx,
            self._clock.now(),
            principal,
            Permission.HOLIDAYS_MANAGE,
            None,
            None,
            f"{slug}:{detail}",
        )

    async def _audit_group(
        self, tx: Transaction, principal: Principal, group: Group, detail: str
    ) -> None:
        """Record a holiday change of ``group``."""
        organisation = await tx.get_organisation_by_id(group.organisation_id)
        await record_change(
            tx,
            self._clock.now(),
            principal,
            Permission.SCHEDULE_MANAGE,
            organisation.slug if organisation else None,
            group.slug,
            detail,
        )


def _state(day: date, open_days: Collection[date], excepted: Collection[date]) -> DayState:
    """Return what a group does on the holiday ``day``; its own exception wins."""
    if day in excepted:
        return DayState.EXCEPTION
    if day in open_days:
        return DayState.OPEN
    return DayState.CLOSED


async def _require_known(tx: Transaction, calendar_ids: frozenset[int]) -> None:
    """Refuse calendar ids that do not exist.

    Raises:
        DomainError: ``invalid_configuration`` with field ``calendars``.
    """
    known = {c.id for c in await tx.get_holiday_calendars()}
    if not calendar_ids <= known:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "calendars")


async def _calendar(tx: Transaction, slug: str) -> HolidayCalendar:
    """Return the calendar with ``slug``.

    Raises:
        DomainError: ``holiday_calendar_not_found``.
    """
    calendar = await tx.get_holiday_calendar(slug)
    if calendar is None:
        raise DomainError(ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND)
    return calendar
