"""Slot calculation: which appointment start times a group offers.

Windows are expressed in the group's local time and converted to UTC per slot, so daylight
saving time is handled per date. Local times that do not exist (spring forward) are skipped;
ambiguous local times (fall back) use their first occurrence.

    windows_on(day) ──► slots_on(day) ──► filter: notice ≤ start ≤ horizon
                                          capacity = existing slot snapshot or window
                                          remaining = capacity − active bookings > 0
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Group, ScheduleOverride, ScheduleRule, TimeWindow, windows_on

MAX_RANGE_DAYS = 62
"""Largest number of days one availability request may cover."""


@dataclass(frozen=True)
class SlotTime:
    """A start time the schedule offers, in UTC, with the capacity of its window."""

    start: datetime
    end: datetime
    capacity: int


@dataclass(frozen=True)
class AvailableSlot:
    """A bookable slot with the number of places left."""

    start: datetime
    end: datetime
    capacity: int
    remaining: int


def slots_on(group: Group, day: date, windows: Sequence[TimeWindow]) -> list[SlotTime]:
    """Return every slot of ``day`` that fits completely inside one of ``windows``."""
    tz = group.tz
    length = timedelta(minutes=group.slot_minutes)
    slots: list[SlotTime] = []
    for window in windows:
        local = datetime.combine(day, window.opens)
        closes = datetime.combine(day, window.closes)
        while local + length <= closes:
            start = local.replace(tzinfo=tz).astimezone(UTC)
            # A local time that does not exist does not survive the round trip.
            if start.astimezone(tz).replace(tzinfo=None) == local:
                slots.append(SlotTime(start, start + length, window.capacity))
            local += length
    return slots


def offered_slot(
    group: Group,
    rules: Sequence[ScheduleRule],
    overrides: Sequence[ScheduleOverride],
    start: datetime,
) -> SlotTime | None:
    """Return the slot starting exactly at ``start`` if the schedule offers it.

    Raises:
        ValueError: If ``start`` is naive.
    """
    if start.tzinfo is None:
        raise ValueError("start must be timezone-aware")
    day = start.astimezone(group.tz).date()
    for slot in slots_on(group, day, windows_on(day, rules, overrides)):
        if slot.start == start:
            return slot
    return None


def booking_window(group: Group, now: datetime) -> tuple[datetime, datetime]:
    """Return the earliest and latest start a citizen may book at ``now``."""
    return (
        now + timedelta(minutes=group.min_notice_minutes),
        now + timedelta(days=group.horizon_days),
    )


def validate_range(first_day: date, last_day: date) -> int:
    """Return the number of days in the range.

    Raises:
        DomainError: ``invalid_range`` if the range is reversed or longer than
            ``MAX_RANGE_DAYS``.
    """
    days = (last_day - first_day).days + 1
    if not 1 <= days <= MAX_RANGE_DAYS:
        raise DomainError(ErrorCode.INVALID_RANGE)
    return days


def utc_bounds(group: Group, first_day: date, last_day: date) -> tuple[datetime, datetime]:
    """Return the UTC instants of local midnight at ``first_day`` and after ``last_day``."""
    tz = group.tz
    lower = datetime.combine(first_day, time()).replace(tzinfo=tz).astimezone(UTC)
    upper = datetime.combine(last_day + timedelta(days=1), time()).replace(tzinfo=tz)
    return lower, upper.astimezone(UTC)


def available_slots(
    *,
    group: Group,
    rules: Sequence[ScheduleRule],
    overrides: Sequence[ScheduleOverride],
    first_day: date,
    last_day: date,
    now: datetime,
    booked: Mapping[datetime, int],
    snapshots: Mapping[datetime, int],
) -> list[AvailableSlot]:
    """Return the slots citizens can book between two local dates (inclusive).

    Args:
        group: The group, for time zone, slot length, notice and horizon.
        rules: The group's weekly rules.
        overrides: The group's overrides (at least those within the range).
        first_day: First local date.
        last_day: Last local date; at most ``MAX_RANGE_DAYS`` days after ``first_day``.
        now: Current time (UTC).
        booked: Active bookings per slot start.
        snapshots: Capacity snapshot per existing slot start; wins over the window.

    Raises:
        DomainError: ``invalid_range`` if the range is reversed or too long.
    """
    days = validate_range(first_day, last_day)
    earliest, latest = booking_window(group, now)
    result: list[AvailableSlot] = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        for slot in slots_on(group, day, windows_on(day, rules, overrides)):
            if not earliest <= slot.start <= latest:
                continue
            capacity = snapshots.get(slot.start, slot.capacity)
            remaining = capacity - booked.get(slot.start, 0)
            if remaining > 0:
                result.append(AvailableSlot(slot.start, slot.end, capacity, remaining))
    return result
