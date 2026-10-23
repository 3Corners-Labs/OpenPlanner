from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.domain.availability import (
    MAX_RANGE_DAYS,
    AvailableSlot,
    available_slots,
    offered_slot,
    slots_on,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import ScheduleOverride, ScheduleRule, TimeWindow
from tests.domain.test_scheduling import make_group, window

MONDAY = date(2026, 1, 5)  # CET, UTC+1
NOW = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
GROUP = make_group(slot_minutes=30, min_notice_minutes=60, horizon_days=60)
MONDAY_MORNING = [ScheduleRule(weekday=0, window=window("09:00", "10:30", 2))]


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_slots_fill_each_window_converted_to_utc() -> None:
    slots = slots_on(GROUP, MONDAY, [window("09:00", "10:30", 2)])

    assert [s.start for s in slots] == [
        utc(2026, 1, 5, 8, 0),
        utc(2026, 1, 5, 8, 30),
        utc(2026, 1, 5, 9, 0),
    ]
    assert all(s.end - s.start == timedelta(minutes=30) for s in slots)
    assert all(s.capacity == 2 for s in slots)


def test_a_slot_that_does_not_fit_before_closing_is_dropped() -> None:
    slots = slots_on(GROUP, MONDAY, [window("09:00", "09:50", 1)])

    assert [s.start for s in slots] == [utc(2026, 1, 5, 8, 0)]


def test_spring_forward_skips_nonexistent_local_times() -> None:
    # 29 March 2026: clocks jump from 02:00 to 03:00 in Europe/Amsterdam.
    slots = slots_on(GROUP, date(2026, 3, 29), [window("01:00", "04:00", 1)])

    assert [s.start for s in slots] == [
        utc(2026, 3, 29, 0, 0),  # 01:00 CET
        utc(2026, 3, 29, 0, 30),  # 01:30 CET
        utc(2026, 3, 29, 1, 0),  # 03:00 CEST
        utc(2026, 3, 29, 1, 30),  # 03:30 CEST
    ]


def test_fall_back_uses_the_first_occurrence_of_ambiguous_times() -> None:
    # 25 October 2026: 02:00-03:00 happens twice in Europe/Amsterdam.
    slots = slots_on(GROUP, date(2026, 10, 25), [window("02:00", "03:00", 1)])

    assert [s.start for s in slots] == [utc(2026, 10, 25, 0, 0), utc(2026, 10, 25, 0, 30)]


def test_summer_time_offset_is_applied() -> None:
    slots = slots_on(GROUP, date(2026, 6, 1), [window("09:00", "09:30", 1)])

    assert slots[0].start == utc(2026, 6, 1, 7, 0)


def available(**kwargs: object) -> list[AvailableSlot]:
    params: dict[str, object] = {
        "group": GROUP,
        "rules": MONDAY_MORNING,
        "overrides": [],
        "first_day": MONDAY,
        "last_day": MONDAY,
        "now": NOW,
        "booked": {},
        "snapshots": {},
    }
    params.update(kwargs)
    return available_slots(**params)  # type: ignore[arg-type]


def test_available_slots_report_remaining_capacity() -> None:
    slots = available(booked={utc(2026, 1, 5, 8, 0): 1})

    assert [(s.start, s.remaining) for s in slots] == [
        (utc(2026, 1, 5, 8, 0), 1),
        (utc(2026, 1, 5, 8, 30), 2),
        (utc(2026, 1, 5, 9, 0), 2),
    ]


def test_full_slots_are_not_offered() -> None:
    slots = available(booked={utc(2026, 1, 5, 8, 0): 2})

    assert utc(2026, 1, 5, 8, 0) not in [s.start for s in slots]


def test_snapshot_capacity_wins_over_changed_rule() -> None:
    slots = available(booked={utc(2026, 1, 5, 8, 0): 2}, snapshots={utc(2026, 1, 5, 8, 0): 3})

    assert slots[0].start == utc(2026, 1, 5, 8, 0)
    assert slots[0].capacity == 3
    assert slots[0].remaining == 1


def test_minimum_notice_boundary() -> None:
    # 08:00 UTC slot; with 60 minutes notice it is bookable until exactly 07:00 UTC.
    at_boundary = available(now=utc(2026, 1, 5, 7, 0))
    too_late = available(now=utc(2026, 1, 5, 7, 0, 1))

    assert at_boundary[0].start == utc(2026, 1, 5, 8, 0)
    assert too_late[0].start == utc(2026, 1, 5, 8, 30)


def test_horizon_boundary() -> None:
    group = make_group(horizon_days=4, min_notice_minutes=0)
    at_horizon = available(group=group, now=utc(2026, 1, 1, 8, 0))  # 08:00 + 4 days
    beyond = available(group=group, now=utc(2026, 1, 1, 7, 59))

    assert [s.start for s in at_horizon] == [utc(2026, 1, 5, 8, 0)]
    assert beyond == []


def test_override_closes_a_day() -> None:
    assert available(overrides=[ScheduleOverride(day=MONDAY, window=None)]) == []


def test_range_spans_several_days() -> None:
    slots = available(last_day=MONDAY + timedelta(days=7))

    assert len(slots) == 6


@pytest.mark.parametrize(
    ("first", "last"),
    [(MONDAY, MONDAY - timedelta(days=1)), (MONDAY, MONDAY + timedelta(days=MAX_RANGE_DAYS))],
)
def test_range_is_validated(first: date, last: date) -> None:
    with pytest.raises(DomainError) as excinfo:
        available(first_day=first, last_day=last)

    assert excinfo.value.code is ErrorCode.INVALID_RANGE


def test_maximum_range_is_accepted() -> None:
    available(last_day=MONDAY + timedelta(days=MAX_RANGE_DAYS - 1))


def test_offered_slot_finds_an_aligned_start() -> None:
    slot = offered_slot(GROUP, MONDAY_MORNING, [], utc(2026, 1, 5, 8, 30))

    assert slot is not None
    assert slot.capacity == 2


@pytest.mark.parametrize(
    "start",
    [
        utc(2026, 1, 5, 8, 7),  # unaligned
        utc(2026, 1, 5, 9, 30),  # after closing
        utc(2026, 1, 6, 8, 0),  # Tuesday, no rule
    ],
)
def test_offered_slot_rejects_starts_that_are_not_offered(start: datetime) -> None:
    assert offered_slot(GROUP, MONDAY_MORNING, [], start) is None


def test_offered_slot_respects_overrides() -> None:
    overrides = [ScheduleOverride(day=MONDAY, window=None)]

    assert offered_slot(GROUP, MONDAY_MORNING, overrides, utc(2026, 1, 5, 8, 0)) is None


def test_offered_slot_requires_an_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        offered_slot(GROUP, MONDAY_MORNING, [], datetime(2026, 1, 5, 8, 0))  # noqa: DTZ001


def test_window_type_is_reexported_for_callers() -> None:
    assert TimeWindow(time(9), time(10), 1).capacity == 1
