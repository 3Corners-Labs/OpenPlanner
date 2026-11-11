"""``grid_on`` is the old ``slots_on`` with the time zone and length passed in (ADR 0122, P6)."""

import itertools
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.availability import (
    SlotTime,
    _covered,
    _subjects,
    grid_on,
    slots_on,
    staffed_runs,
)
from app.domain.scheduling import Group, Offer, TimeWindow
from tests.support.domain import make_group


def reference_slots_on(group: Group, day: date, windows: Sequence[TimeWindow]) -> list[SlotTime]:
    """The function as it was before the refactor, copied verbatim, as the proof's reference."""
    tz = group.tz
    length = timedelta(minutes=group.slot_minutes)
    slots: list[SlotTime] = []
    for run in staffed_runs(windows):
        local = datetime.combine(day, run[0].opens)
        closes = datetime.combine(day, run[-1].closes)
        while local + length <= closes:
            start = local.replace(tzinfo=tz).astimezone(UTC)
            if start.astimezone(tz).replace(tzinfo=None) == local:
                covered = _covered(run, day, local, local + length)
                capacity = min(w.capacity for w in covered)
                slots.append(SlotTime(start, start + length, capacity, _subjects(covered)))
            local += length
    return slots


def win(
    opens: str,
    closes: str,
    capacity: int = 2,
    offer: Offer = Offer.SCHEDULED,
    subjects: tuple[str, ...] = (),
) -> TimeWindow:
    return TimeWindow(
        time.fromisoformat(opens),
        time.fromisoformat(closes),
        capacity,
        offer=offer,
        subjects=subjects,
    )


WINDOW_SETS: list[list[TimeWindow]] = [
    [],
    [win("09:00", "10:00")],
    [win("09:00", "12:00", 3)],
    [win("09:00", "10:15", 3), win("10:15", "12:00", 2)],
    [win("09:00", "10:00"), win("10:30", "12:00", 1)],
    [win("09:00", "10:00", 0), win("10:00", "11:00", 2)],
    [win("09:00", "10:00", 2, Offer.DIRECT), win("10:00", "11:00", 2, Offer.BOTH)],
    [win("09:00", "10:00", 2, subjects=("a",)), win("10:00", "11:00", 2, subjects=("a", "b"))],
    [win("00:00", "06:00", 1)],  # the night of a clock change
    [win("01:00", "04:00", 1), win("04:00", "05:00", 2)],
    [win("02:00", "03:00", 4)],
    [win("23:00", "23:59", 1)],
]
DAYS = [
    date(2026, 1, 5),  # winter
    date(2026, 7, 6),  # summer
    date(2026, 3, 29),  # spring forward
    date(2026, 10, 25),  # fall back
    date(2026, 3, 28),
    date(2026, 10, 24),
]
ZONES = ["Europe/Amsterdam", "UTC", "America/New_York", "Australia/Lord_Howe"]
MINUTES = [5, 15, 20, 30, 45, 60, 90, 480]


@pytest.mark.parametrize("zone", ZONES)
@pytest.mark.parametrize("minutes", MINUTES)
def test_the_group_calculation_equals_the_old_function_on_a_spread_of_inputs(
    zone: str, minutes: int
) -> None:
    group = make_group(timezone=zone, slot_minutes=minutes)

    for windows, day in itertools.product(WINDOW_SETS, DAYS):
        assert slots_on(group, day, windows) == reference_slots_on(group, day, windows)


def test_a_group_with_a_working_grid_still_produces_slots() -> None:
    group = make_group(slot_minutes=30)

    assert len(slots_on(group, date(2026, 1, 5), [win("09:00", "12:00", 3)])) == 6


def test_grid_on_with_the_group_arguments_is_slots_on() -> None:
    group = make_group(slot_minutes=30)
    windows = [win("09:00", "10:15", 3), win("10:15", "12:00", 2)]

    assert grid_on(group.tz, timedelta(minutes=30), date(2026, 1, 5), windows) == slots_on(
        group, date(2026, 1, 5), windows
    )


# --- P6: one after another from the start of each block, appointment length plus wrap-up ---


def test_p6_a_flow_steps_by_its_length_plus_the_buffer_from_the_start_of_the_block() -> None:
    slots = grid_on(
        ZoneInfo("Europe/Amsterdam"),
        timedelta(minutes=35),  # a 30-minute flow with a wrap-up of 5
        date(2026, 1, 5),
        [win("09:00", "10:30")],
    )

    assert [s.start for s in slots] == [
        datetime(2026, 1, 5, 8, 0, tzinfo=UTC),
        datetime(2026, 1, 5, 8, 35, tzinfo=UTC),
    ]  # 10:10 would end at 10:45, after the block closes
    assert all(s.end - s.start == timedelta(minutes=35) for s in slots)


def test_p6_two_flows_of_different_lengths_have_their_own_grid_from_the_same_block_start() -> None:
    day, tz, windows = date(2026, 1, 5), ZoneInfo("Europe/Amsterdam"), [win("09:00", "10:00")]

    quarter = grid_on(tz, timedelta(minutes=15), day, windows)
    half = grid_on(tz, timedelta(minutes=30), day, windows)

    assert [s.start.minute for s in quarter] == [0, 15, 30, 45]
    assert [s.start.minute for s in half] == [0, 30]


def test_p6_a_run_of_touching_blocks_has_one_grid_across_the_border() -> None:
    slots = grid_on(
        ZoneInfo("Europe/Amsterdam"),
        timedelta(minutes=40),
        date(2026, 1, 5),
        [win("09:00", "10:00", 3), win("10:00", "11:00", 1)],
    )

    assert [(s.start.hour, s.start.minute, s.capacity) for s in slots] == [
        (8, 0, 3),
        (8, 40, 1),  # 09:40-10:20 straddles the border: the lower count
        (9, 20, 1),
    ]


def test_p7_a_block_without_appointments_or_units_takes_no_part_in_the_grid() -> None:
    slots = grid_on(
        ZoneInfo("Europe/Amsterdam"),
        timedelta(minutes=30),
        date(2026, 1, 5),
        [
            win("09:00", "10:00", 2, Offer.DIRECT),
            win("10:00", "11:00", 0),
            win("11:00", "12:00", 1, Offer.BOTH),
        ],
    )

    assert [s.start.hour for s in slots] == [10, 10]


def test_p6_a_start_in_the_missing_hour_of_the_spring_change_is_not_offered() -> None:
    slots = grid_on(
        ZoneInfo("Europe/Amsterdam"),
        timedelta(minutes=30),
        date(2026, 3, 29),
        [win("01:00", "04:00", 1)],
    )

    assert [s.start for s in slots] == [
        datetime(2026, 3, 29, 0, 0, tzinfo=UTC),  # 01:00 CET
        datetime(2026, 3, 29, 0, 30, tzinfo=UTC),  # 01:30 CET
        datetime(2026, 3, 29, 1, 0, tzinfo=UTC),  # 03:00 CEST (02:00-02:30 do not exist)
        datetime(2026, 3, 29, 1, 30, tzinfo=UTC),  # 03:30 CEST
    ]
