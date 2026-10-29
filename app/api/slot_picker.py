"""The slot picker of the citizen pages: a month calendar plus the times of one day (ADR 0020).

Without JavaScript it is a table of links (``?day=`` shows that day's times, ``?month=``
another month) and a form of time buttons. ``static/calendar.js`` turns the table into an ARIA
grid with arrow keys and swaps the times without reloading; both use the same markup.

    picker(..., month=None, day=None)
        ├─ first: the first free moment from today ("Eerst mogelijk")
        ├─ month: the requested month, the month of ``day``, or the month of ``first``
        └─ times: the requested day if it has free times, else the first such day in the month

A ``SlotFilter`` ("Wanneer past het u?": weekdays, from and until a full hour) narrows all three
to the slots that fit; it travels as ``?days=&from=&until=`` in every link, so the page can be
shared and works without JavaScript.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from urllib.parse import urlencode

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


WEEKDAY_VALUES = frozenset("0123456")
"""Accepted ``days`` values: one digit per weekday, Monday = 0."""
"""Query parameters of the filter; kept in every picker link and by the language switcher."""


@dataclass(frozen=True)
class SlotFilter:
    """Which slots fit the citizen ("Wanneer past het u?").

    Attributes:
        days: Weekdays that fit (0 = Monday); empty means every day.
        start_hour: A slot fits from this full hour on (local time).
        end_hour: A slot fits if it ends by this full hour (local time).
        invalid_range: The citizen chose an end at or before the start; ``end_hour`` is
            then dropped and the form explains why.
    """

    days: frozenset[int] = frozenset()
    start_hour: int | None = None
    end_hour: int | None = None
    invalid_range: bool = False

    @property
    def active(self) -> bool:
        """Whether any condition is set."""
        return bool(self.days) or self.start_hour is not None or self.end_hour is not None

    def matches(self, slot: AvailableSlot, tz: tzinfo) -> bool:
        """Return whether ``slot`` fits, in the group's time zone ``tz``."""
        start, end = slot.start.astimezone(tz), slot.end.astimezone(tz)
        if self.days and start.weekday() not in self.days:
            return False
        if self.start_hour is not None and start.hour < self.start_hour:
            return False
        if self.end_hour is None:
            return True
        ends = end.hour + end.minute / 60 + (24 if end.date() > start.date() else 0)
        return ends <= self.end_hour

    def query(self) -> list[tuple[str, str]]:
        """Return the filter as query parameters, in a stable order."""
        params = [("days", str(day)) for day in sorted(self.days)]
        if self.start_hour is not None:
            params.append(("from", str(self.start_hour)))
        if self.end_hour is not None:
            params.append(("until", str(self.end_hour)))
        return params


def _hour(value: str, low: int, high: int) -> int | None:
    """Return a whole hour between ``low`` and ``high``, or ``None``."""
    return int(value) if value.isdigit() and len(value) <= 2 and low <= int(value) <= high else None


def parse_filter(params: Sequence[tuple[str, str]]) -> SlotFilter:
    """Read the filter from query parameters; values that make no sense are ignored."""
    days = frozenset(
        int(value) for key, value in params if key == "days" and value in WEEKDAY_VALUES
    )
    values = dict(params)
    start = _hour(values.get("from", ""), 0, 23)
    end = _hour(values.get("until", ""), 1, 24)
    invalid = start is not None and end is not None and end <= start
    return SlotFilter(days, start, None if invalid else end, invalid)


@dataclass(frozen=True)
class Option:
    """One choice of the filter form."""

    value: str
    label: str
    selected: bool = False


@dataclass(frozen=True)
class FilterView:
    """The filter form: weekdays, from and until, and whether it narrows anything now."""

    active: bool
    days: list[Option]
    starts: list[Option]
    ends: list[Option]
    invalid_range: bool
    clear_href: str
    action: str


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
        later: Whether the day lies beyond the booking horizon ("nog niet te boeken"),
            as opposed to a day that is closed or full.
    """

    number: str
    label: str = ""
    href: str | None = None
    iso: str = ""
    selected: bool = False
    today: bool = False
    later: bool = False


@dataclass(frozen=True)
class MonthView:
    """The month table with navigation."""

    heading: str
    weekdays: list[tuple[str, str]]
    weeks: list[list[DayCell]]
    previous: str | None
    next: str | None
    horizon_days: int
    has_later: bool
    iso: str = ""
    previous_iso: str | None = None
    next_iso: str | None = None


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
    iso: str = ""


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
    filter: FilterView


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
        iso=day.isoformat(),
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


def _window_end(start: date, last: date) -> date:
    """Return the last day of one availability read from ``start``, at most ``last``."""
    return min(last, start + timedelta(days=MAX_RANGE_DAYS - 1))


async def _first_free(
    service: BookingService,
    group: Group,
    first_window: list[AvailableSlot],
    today: date,
    last: date,
    exclude: datetime | None,
    slot_filter: SlotFilter,
) -> AvailableSlot | None:
    """Return the first fitting free slot from today to the horizon.

    Availability is read at most ``MAX_RANGE_DAYS`` at a time, so a long horizon is searched
    window by window; ``first_window`` is the first of them, already read.
    """
    window, start = first_window, today
    while True:
        fitting = [slot for slot in window if slot_filter.matches(slot, group.tz)]
        if fitting:
            return fitting[0]
        start = _window_end(start, last) + timedelta(days=1)
        if start > last:
            return None
        window = await _free(service, group, start, _window_end(start, last), exclude)


def _end_hour(slot: AvailableSlot, tz: tzinfo) -> int:
    """Return the full hour by which ``slot`` has ended (10:30 → 11)."""
    end = slot.end.astimezone(tz)
    if (end.hour, end.minute) == (0, 0):
        return 24  # ends at midnight
    return end.hour + (1 if end.minute else 0)


def _filter_view(
    slot_filter: SlotFilter,
    group: Group,
    window: Sequence[AvailableSlot],
    locale: RequestLocale,
    base: str,
) -> FilterView:
    """Return the filter form; the hours offered are those the group has slots in."""
    starts = sorted({slot.start.astimezone(group.tz).hour for slot in window})
    ends = sorted({_end_hour(slot, group.tz) for slot in window})
    for chosen, hours in ((slot_filter.start_hour, starts), (slot_filter.end_hour, ends)):
        if chosen is not None and chosen not in hours:
            hours.append(chosen)
            hours.sort()
    names = weekday_names(locale.code)
    return FilterView(
        active=slot_filter.active,
        days=[Option(str(i), names[i], i in slot_filter.days) for i in range(7)],
        starts=[Option(str(h), f"{h:02d}:00", h == slot_filter.start_hour) for h in starts],
        ends=[Option(str(h), f"{h:02d}:00", h == slot_filter.end_hour) for h in ends],
        invalid_range=slot_filter.invalid_range,
        clear_href=base,
        action=base,
    )


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
    slot_filter: SlotFilter | None = None,
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
        slot_filter: Which slots fit the citizen; all of them when ``None``.
    """
    slot_filter = slot_filter or SlotFilter()
    today = local_day(now, group.tz)
    last = local_day(now + timedelta(days=group.horizon_days), group.tz)
    first_month, last_month = today.replace(day=1), last.replace(day=1)

    def href(*params: tuple[str, str]) -> str:
        return f"{base}?{urlencode([*slot_filter.query(), *params])}"

    window = await _free(service, group, today, _window_end(today, last), exclude)
    first_slot = await _first_free(service, group, window, today, last, exclude, slot_filter)
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
    month_slots = await _free(service, group, max(shown, today), min(month_end, last), exclude)
    by_day = _by_day(group, [s for s in month_slots if slot_filter.matches(s, group.tz)])
    selected = requested_day if requested_day in by_day else next(iter(by_day), None)

    names, short = weekday_names(locale.code), weekday_abbreviations(locale.code)
    weeks = [
        [
            DayCell(
                number=str(d.day),
                label=format_day_short(d, locale.code),
                href=f"{href(('day', d.isoformat()))}#times" if d in by_day else None,
                iso=d.isoformat(),
                selected=d == selected,
                today=d == today,
                later=d > last,
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
        previous=href(("month", f"{previous:%Y-%m}")) if shown > first_month else None,
        next=href(("month", f"{following:%Y-%m}")) if shown < last_month else None,
        horizon_days=group.horizon_days,
        has_later=month_end > last,
        iso=f"{shown:%Y-%m}",
        previous_iso=f"{previous:%Y-%m}" if shown > first_month else None,
        next_iso=f"{following:%Y-%m}" if shown < last_month else None,
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
    return PickerView(view, times, first, _filter_view(slot_filter, group, window, locale, base))
