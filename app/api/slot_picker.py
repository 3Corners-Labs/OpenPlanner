"""The slot picker of the citizen pages: a month calendar plus the times of one day (ADR 0020).

Without JavaScript it is a table of links (``?day=`` shows that day's times, ``?month=``
another month) and a form of time buttons. ``static/calendar.js`` turns the table into an ARIA
grid with arrow keys and swaps the times without reloading; both use the same markup.

    picker(..., month=None, day=None)
        ├─ first: the first free moment from today ("Eerst mogelijk")
        ├─ month: the requested month, the month of ``day``, or the month of ``first``
        └─ times: the requested day if it has free times, else the first such day in the month
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.domain.availability import MAX_RANGE_DAYS, AvailableSlot
from app.domain.scheduling import Group
from app.domain.services import BookingService
from app.i18n.dates import (
    format_clock,
    format_day_short,
    format_month,
    local_day,
    weekday_abbreviations,
    weekday_names,
)
from app.web.locale import RequestLocale

PARTS_OF_DAY: tuple[tuple[int, str], ...] = (
    (0, "picker.morning"),
    (12, "picker.afternoon"),
    (18, "picker.evening"),
)
"""Local hour from which a part of the day starts, and its heading."""


@dataclass(frozen=True)
class SlotView:
    """One time button."""

    value: str
    label: str


@dataclass(frozen=True)
class DayCell:
    """One cell of the month table.

    Attributes:
        number: Day of the month as shown; empty for the padding before the 1st.
        label: The full date for assistive technology (``maandag 26 oktober``).
        href: Link that shows this day's times; ``None`` when nothing is free.
        iso: The date as ``YYYY-MM-DD`` (for the grid script).
        selected: Whether this day's times are shown.
        today: Whether this is today in the group's time zone.
    """

    number: str
    label: str = ""
    href: str | None = None
    iso: str = ""
    selected: bool = False
    today: bool = False


@dataclass(frozen=True)
class MonthView:
    """The month table with navigation."""

    heading: str
    weekdays: list[tuple[str, str]]
    weeks: list[list[DayCell]]
    previous: str | None
    next: str | None


@dataclass(frozen=True)
class TimeGroup:
    """The times of one part of the day."""

    heading: str
    slots: list[SlotView]


@dataclass(frozen=True)
class DayTimes:
    """The free times of the selected day."""

    heading: str
    count: str
    groups: list[TimeGroup]


@dataclass(frozen=True)
class FirstFree:
    """The first free moment, offered as a one-click choice."""

    value: str
    label: str


@dataclass(frozen=True)
class PickerView:
    """Everything the picker template shows."""

    month: MonthView
    times: DayTimes | None
    first: FirstFree | None


def slot_value(start: datetime) -> str:
    """Return the form value of a slot: its start in UTC, ISO 8601 with ``Z``."""
    return start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_day(value: str | None) -> date | None:
    """Parse ``YYYY-MM-DD``; anything else is ``None``."""
    if not value or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_month(value: str | None) -> date | None:
    """Parse ``YYYY-MM`` into the first of that month; anything else is ``None``."""
    return _parse_day(f"{value}-01") if value and len(value) == 7 else None


def _add_months(first: date, months: int) -> date:
    """Return the first of the month ``months`` after the month of ``first``."""
    index = first.year * 12 + first.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def month_weeks(first: date) -> list[list[date | None]]:
    """Return the weeks of ``first``'s month, Monday first, padded with ``None``."""
    days: list[date | None] = [None] * first.weekday()
    day = first
    while day.month == first.month:
        days.append(day)
        day += timedelta(days=1)
    days += [None] * (-len(days) % 7)
    return [days[i : i + 7] for i in range(0, len(days), 7)]


def _by_day(group: Group, slots: Sequence[AvailableSlot]) -> dict[date, list[AvailableSlot]]:
    """Group slots by local date, in order."""
    found: dict[date, list[AvailableSlot]] = {}
    for slot in slots:
        found.setdefault(local_day(slot.start, group.tz), []).append(slot)
    return found


def _times(group: Group, day: date, slots: list[AvailableSlot], locale: RequestLocale) -> DayTimes:
    """Return the times of ``day`` grouped into morning, afternoon and evening."""
    groups: list[TimeGroup] = []
    for index, (hour, key) in enumerate(PARTS_OF_DAY):
        until = PARTS_OF_DAY[index + 1][0] if index + 1 < len(PARTS_OF_DAY) else 24
        part = [s for s in slots if hour <= s.start.astimezone(group.tz).hour < until]
        if part:
            views = [
                SlotView(slot_value(s.start), format_clock(s.start, group.tz, locale.code))
                for s in part
            ]
            groups.append(TimeGroup(locale.gettext(key), views))
    return DayTimes(
        heading=format_day_short(day, locale.code),
        count=locale.gettext(
            "picker.count_one" if len(slots) == 1 else "picker.count", n=len(slots)
        ),
        groups=groups,
    )


async def _free(
    service: BookingService, group: Group, first: date, last: date, exclude: datetime | None
) -> list[AvailableSlot]:
    """Return the free slots between two local dates, without ``exclude``."""
    if last < first:
        return []
    slots = await service.availability(group.slug, first, last)
    return [slot for slot in slots if slot.start != exclude]


async def picker(
    service: BookingService,
    group: Group,
    now: datetime,
    locale: RequestLocale,
    base: str,
    *,
    month: str | None = None,
    day: str | None = None,
    exclude: datetime | None = None,
) -> PickerView:
    """Build the picker for ``group``, clamped to today … the booking horizon.

    Args:
        service: Reads availability.
        group: The group whose slots are shown.
        now: Current time.
        locale: Language of the labels.
        base: Path of the page; day and month links are ``{base}?day=…`` / ``?month=…``.
        month: Requested month (``YYYY-MM``), from the month navigation.
        day: Requested day (``YYYY-MM-DD``), from a day link.
        exclude: A start time not to offer (the booking that is being moved).
    """
    today = local_day(now, group.tz)
    last = local_day(now + timedelta(days=group.horizon_days), group.tz)
    first_month, last_month = today.replace(day=1), last.replace(day=1)

    upcoming = await _free(
        service, group, today, min(last, today + timedelta(days=MAX_RANGE_DAYS - 1)), exclude
    )
    first_slot = upcoming[0] if upcoming else None
    requested_day = _parse_day(day)
    requested_month = _parse_month(month)
    if requested_day is not None and today <= requested_day <= last:
        shown = requested_day.replace(day=1)
    elif requested_month is not None:
        shown = min(max(requested_month, first_month), last_month)
    elif first_slot is not None:
        shown = local_day(first_slot.start, group.tz).replace(day=1)
    else:
        shown = first_month
    month_end = _add_months(shown, 1) - timedelta(days=1)
    by_day = _by_day(
        group, await _free(service, group, max(shown, today), min(month_end, last), exclude)
    )
    selected = requested_day if requested_day in by_day else next(iter(by_day), None)

    names, short = weekday_names(locale.code), weekday_abbreviations(locale.code)
    weeks = [
        [
            DayCell(
                number=str(d.day),
                label=format_day_short(d, locale.code),
                href=f"{base}?day={d.isoformat()}#times" if d in by_day else None,
                iso=d.isoformat(),
                selected=d == selected,
                today=d == today,
            )
            if d is not None
            else DayCell("")
            for d in week
        ]
        for week in month_weeks(shown)
    ]
    previous, following = _add_months(shown, -1), _add_months(shown, 1)
    view = MonthView(
        heading=format_month(shown, locale.code),
        weekdays=list(zip(short, names, strict=True)),
        weeks=weeks,
        previous=f"{base}?month={previous:%Y-%m}" if shown > first_month else None,
        next=f"{base}?month={following:%Y-%m}" if shown < last_month else None,
    )
    times = _times(group, selected, by_day[selected], locale) if selected else None
    first = (
        FirstFree(
            slot_value(first_slot.start),
            f"{format_day_short(local_day(first_slot.start, group.tz), locale.code)}, "
            f"{format_clock(first_slot.start, group.tz, locale.code)}",
        )
        if first_slot
        else None
    )
    return PickerView(view, times, first)
