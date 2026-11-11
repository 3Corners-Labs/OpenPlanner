from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.pool_staffing import (
    Intake,
    PoolStaffing,
    Segment,
    Supply,
    check_pool_staffing,
    lowest_units,
    supply_between,
    with_closed_days,
)
from app.domain.scheduling import (
    MAX_CAPACITY,
    Offer,
    ScheduleOverride,
    ScheduleRule,
    TimeWindow,
)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
MONDAY = date(2026, 1, 5)  # CET, UTC+1
SPRING_SUNDAY = date(2026, 3, 29)  # 02:00 CET becomes 03:00 CEST
FALL_SUNDAY = date(2026, 10, 25)  # 03:00 CEST becomes 02:00 CET


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def block(opens: str, closes: str, units: int, offer: Offer = Offer.SCHEDULED) -> TimeWindow:
    return TimeWindow(time.fromisoformat(opens), time.fromisoformat(closes), units, offer=offer)


def weekly(weekday: int, window: TimeWindow, **kw: date) -> ScheduleRule:
    return ScheduleRule(weekday=weekday, window=window, **kw)


def staffing(*rules: ScheduleRule, overrides: tuple[ScheduleOverride, ...] = ()) -> PoolStaffing:
    return PoolStaffing(AMSTERDAM, rules, overrides)


def day_span(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time()).replace(tzinfo=UTC)
    return start - timedelta(hours=2), start + timedelta(hours=26)


def supply_of(
    pool: PoolStaffing, day: date, closed: frozenset[date] = frozenset()
) -> tuple[Segment, ...]:
    lower, upper = day_span(day)
    return supply_between(pool, closed, lower, upper).segments


def test_a_weekly_block_becomes_a_utc_segment_with_its_units_and_offer() -> None:
    pool = staffing(weekly(0, block("09:00", "12:00", 2, Offer.BOTH)))

    assert supply_of(pool, MONDAY) == (
        Segment(utc(2026, 1, 5, 8), utc(2026, 1, 5, 11), 2, Offer.BOTH),
    )


def test_summer_time_uses_the_summer_offset() -> None:
    pool = staffing(weekly(0, block("09:00", "12:00", 2)))

    assert supply_of(pool, date(2026, 7, 6)) == (
        Segment(utc(2026, 7, 6, 7), utc(2026, 7, 6, 10), 2),
    )


def test_only_blocks_of_the_weekday_and_validity_period_count() -> None:
    pool = staffing(
        weekly(0, block("09:00", "10:00", 1), valid_from=date(2026, 1, 12)),
        weekly(1, block("09:00", "10:00", 3)),
    )

    assert supply_of(pool, MONDAY) == ()
    assert supply_of(pool, date(2026, 1, 12))[0].units == 1
    assert supply_of(pool, date(2026, 1, 6))[0].units == 3


def test_an_exception_replaces_the_weekly_blocks_of_its_date() -> None:
    exception = ScheduleOverride(MONDAY, block("13:00", "14:00", 5))
    pool = staffing(weekly(0, block("09:00", "12:00", 2)), overrides=(exception,))

    assert supply_of(pool, MONDAY) == (Segment(utc(2026, 1, 5, 12), utc(2026, 1, 5, 13), 5),)


def test_a_closed_exception_leaves_no_segment() -> None:
    pool = staffing(
        weekly(0, block("09:00", "12:00", 2)), overrides=(ScheduleOverride(MONDAY, None),)
    )

    assert supply_of(pool, MONDAY) == ()


def test_a_holiday_closes_the_day_but_an_own_exception_wins() -> None:
    rule = weekly(0, block("09:00", "12:00", 2))
    holiday = frozenset({MONDAY})

    assert supply_of(staffing(rule), MONDAY, holiday) == ()
    opened = staffing(rule, overrides=(ScheduleOverride(MONDAY, block("10:00", "11:00", 1)),))
    assert supply_of(opened, MONDAY, holiday) == (
        Segment(utc(2026, 1, 5, 9), utc(2026, 1, 5, 10), 1),
    )
    assert supply_of(staffing(rule), date(2026, 1, 12), holiday)[0].units == 2


def test_blocks_without_units_leave_no_segment_and_touching_blocks_stay_separate() -> None:
    pool = staffing(
        weekly(0, block("09:00", "12:00", 2)),
        weekly(0, block("12:00", "12:30", 0)),
        weekly(0, block("12:30", "13:00", 1)),
        weekly(0, block("13:00", "14:00", 3, Offer.DIRECT)),
    )

    assert [(s.start.hour, s.start.minute, s.units, s.offer) for s in supply_of(pool, MONDAY)] == [
        (8, 0, 2, Offer.SCHEDULED),
        (11, 30, 1, Offer.SCHEDULED),
        (12, 0, 3, Offer.DIRECT),
    ]


def test_segments_come_back_in_time_order_whatever_the_rule_order() -> None:
    pool = staffing(weekly(0, block("14:00", "15:00", 1)), weekly(0, block("09:00", "10:00", 2)))

    assert [s.units for s in supply_of(pool, MONDAY)] == [2, 1]


def test_units_may_reach_the_upper_bound_and_not_pass_it() -> None:
    assert (
        supply_of(staffing(weekly(0, block("09:00", "10:00", MAX_CAPACITY))), MONDAY)[0].units
        == 1000
    )
    with pytest.raises(DomainError):
        block("09:00", "10:00", MAX_CAPACITY + 1)


def test_the_segments_that_touch_the_interval_are_returned_whole() -> None:
    pool = staffing(weekly(0, block("09:00", "12:00", 2)))
    inside = supply_between(pool, frozenset(), utc(2026, 1, 5, 9), utc(2026, 1, 5, 10))

    assert inside.segments == (Segment(utc(2026, 1, 5, 8), utc(2026, 1, 5, 11), 2),)


def test_an_interval_that_only_touches_a_segment_does_not_return_it() -> None:
    pool = staffing(weekly(0, block("09:00", "12:00", 2)))

    after = supply_between(pool, frozenset(), utc(2026, 1, 5, 11), utc(2026, 1, 5, 12))
    before = supply_between(pool, frozenset(), utc(2026, 1, 5, 7), utc(2026, 1, 5, 8))
    assert after.segments == before.segments == ()
    one_minute = supply_between(pool, frozenset(), utc(2026, 1, 5, 10, 59), utc(2026, 1, 5, 12))
    assert len(one_minute.segments) == 1


def test_the_local_date_decides_not_the_utc_date_at_both_ends_of_the_interval() -> None:
    late = staffing(
        weekly(2, block("00:00", "01:00", 4)),  # Wednesday 00:00-01:00 = Tuesday 23:00-00:00 UTC
        weekly(0, block("23:00", "23:45", 5)),  # Monday 23:00-23:45 = 22:00-22:45 UTC
    )

    ending_after_local_midnight = supply_between(
        late, frozenset(), utc(2026, 1, 6, 12), utc(2026, 1, 6, 23, 30)
    )
    assert [s.units for s in ending_after_local_midnight.segments] == [4]
    starting_before_local_midnight = supply_between(
        late, frozenset(), utc(2026, 1, 5, 22, 30), utc(2026, 1, 5, 23, 30)
    )
    assert [s.units for s in starting_before_local_midnight.segments] == [5]


def test_an_interval_over_several_days_collects_every_day() -> None:
    pool = staffing(weekly(0, block("09:00", "10:00", 1)), weekly(2, block("09:00", "10:00", 2)))

    supply = supply_between(pool, frozenset(), utc(2026, 1, 5, 0), utc(2026, 1, 8, 0))

    assert [s.units for s in supply.segments] == [1, 2]


def test_an_empty_or_reversed_interval_is_refused() -> None:
    pool = staffing()
    with pytest.raises(ValueError, match="lower"):
        supply_between(pool, frozenset(), utc(2026, 1, 5, 9), utc(2026, 1, 5, 9))
    with pytest.raises(ValueError, match="lower"):
        supply_between(pool, frozenset(), utc(2026, 1, 5, 10), utc(2026, 1, 5, 9))


# --- the two Sundays on which the clock changes ---


def test_spring_forward_a_block_over_the_change_lasts_one_hour_less() -> None:
    pool = staffing(weekly(6, block("01:00", "04:00", 2)))

    (segment,) = supply_of(pool, SPRING_SUNDAY)

    assert segment.start == utc(2026, 3, 29, 0)  # 01:00 CET
    assert segment.end == utc(2026, 3, 29, 2)  # 04:00 CEST: two hours, not three


def test_spring_forward_a_block_inside_the_missing_hour_vanishes() -> None:
    pool = staffing(weekly(6, block("02:00", "03:00", 2)))

    assert supply_of(pool, SPRING_SUNDAY) == ()


def test_spring_forward_a_start_in_the_missing_hour_falls_one_hour_later() -> None:
    pool = staffing(weekly(6, block("02:30", "04:00", 2)))

    (segment,) = supply_of(pool, SPRING_SUNDAY)

    assert (segment.start, segment.end) == (utc(2026, 3, 29, 1, 30), utc(2026, 3, 29, 2))


def test_fall_back_a_block_over_the_change_lasts_one_hour_more() -> None:
    pool = staffing(weekly(6, block("01:00", "04:00", 2)))

    (segment,) = supply_of(pool, FALL_SUNDAY)

    assert segment.start == utc(2026, 10, 24, 23)  # 01:00 CEST
    assert segment.end == utc(2026, 10, 25, 3)  # 04:00 CET: four hours, not three


def test_fall_back_the_repeated_hour_starts_at_its_first_occurrence() -> None:
    pool = staffing(weekly(6, block("02:00", "03:00", 2)))

    (segment,) = supply_of(pool, FALL_SUNDAY)

    assert (segment.start, segment.end) == (utc(2026, 10, 25, 0), utc(2026, 10, 25, 2))


def test_the_ordinary_days_around_the_change_keep_their_offset() -> None:
    pool = staffing(weekly(5, block("09:00", "10:00", 1)), weekly(0, block("09:00", "10:00", 1)))

    assert supply_of(pool, date(2026, 3, 28))[0].start == utc(2026, 3, 28, 8)  # CET
    assert supply_of(pool, date(2026, 3, 30))[0].start == utc(2026, 3, 30, 7)  # CEST


# --- the supply ---


def test_units_at_a_border_belong_to_the_later_segment() -> None:
    supply = Supply(
        (
            Segment(utc(2026, 1, 5, 9), utc(2026, 1, 5, 12), 2),
            Segment(utc(2026, 1, 5, 12), utc(2026, 1, 5, 13), 5),
        )
    )

    assert supply.units_at(utc(2026, 1, 5, 8, 59)) == 0
    assert supply.units_at(utc(2026, 1, 5, 9)) == 2
    assert supply.units_at(utc(2026, 1, 5, 11, 59)) == 2
    assert supply.units_at(utc(2026, 1, 5, 12)) == 5
    assert supply.units_at(utc(2026, 1, 5, 13)) == 0


@pytest.mark.parametrize(
    ("offer", "appointment", "on_demand", "any_intake"),
    [
        (Offer.SCHEDULED, 3, 0, 3),
        (Offer.DIRECT, 0, 3, 3),
        (Offer.BOTH, 3, 3, 3),
    ],
)
def test_a_segment_counts_units_only_for_the_intake_its_block_offers(
    offer: Offer, appointment: int, on_demand: int, any_intake: int
) -> None:
    segment = Segment(utc(2026, 1, 5, 9), utc(2026, 1, 5, 12), 3, offer)

    assert segment.units_for(Intake.APPOINTMENT) == appointment
    assert segment.units_for(Intake.ON_DEMAND) == on_demand
    assert segment.units_for(None) == any_intake


def test_borders_between_are_strictly_inside_the_interval() -> None:
    supply = Supply(
        (
            Segment(utc(2026, 1, 5, 9), utc(2026, 1, 5, 10), 1),
            Segment(utc(2026, 1, 5, 10), utc(2026, 1, 5, 12), 1),
        )
    )

    assert supply.borders_between(utc(2026, 1, 5, 9), utc(2026, 1, 5, 12)) == {utc(2026, 1, 5, 10)}
    assert supply.borders_between(utc(2026, 1, 5, 8), utc(2026, 1, 5, 11)) == {
        utc(2026, 1, 5, 9),
        utc(2026, 1, 5, 10),
    }


def test_the_lowest_units_cover_a_gap_and_a_dip() -> None:
    supply = Supply(
        (
            Segment(utc(2026, 1, 5, 9), utc(2026, 1, 5, 10), 3),
            Segment(utc(2026, 1, 5, 10), utc(2026, 1, 5, 11), 1),
            Segment(utc(2026, 1, 5, 12), utc(2026, 1, 5, 13), 3),
        )
    )

    assert lowest_units(supply, utc(2026, 1, 5, 9), utc(2026, 1, 5, 10), None) == 3
    assert lowest_units(supply, utc(2026, 1, 5, 9, 30), utc(2026, 1, 5, 10, 30), None) == 1
    assert lowest_units(supply, utc(2026, 1, 5, 10, 30), utc(2026, 1, 5, 12, 30), None) == 0
    assert lowest_units(supply, utc(2026, 1, 5, 11), utc(2026, 1, 5, 12), None) == 0
    assert lowest_units(supply, utc(2026, 1, 5, 9), utc(2026, 1, 5, 10), Intake.ON_DEMAND) == 0


# --- validation ---


def test_a_valid_pool_schedule_passes() -> None:
    check_pool_staffing(
        staffing(weekly(0, block("09:00", "12:00", 2)), weekly(1, block("09:00", "12:00", 1)))
    )


def test_overlapping_blocks_of_a_pool_are_refused() -> None:
    pool = staffing(weekly(0, block("09:00", "12:00", 2)), weekly(0, block("11:00", "13:00", 1)))

    with pytest.raises(DomainError) as caught:
        check_pool_staffing(pool)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert caught.value.field == "schedule_rules"


def test_a_block_with_subjects_is_refused_on_a_pool_in_rules_and_exceptions() -> None:
    narrowed = TimeWindow(time(9), time(10), 1, subjects=("tax",))

    for pool in (
        staffing(ScheduleRule(0, narrowed)),
        staffing(overrides=(ScheduleOverride(MONDAY, narrowed),)),
    ):
        with pytest.raises(DomainError) as caught:
            check_pool_staffing(pool)
        assert caught.value.field == "subjects"


def test_closed_days_are_added_as_closed_exceptions_unless_the_day_has_one() -> None:
    own = ScheduleOverride(date(2026, 1, 6), block("09:00", "10:00", 1))

    result = with_closed_days([own], {date(2026, 1, 6), date(2026, 1, 5)})

    assert result == [own, ScheduleOverride(date(2026, 1, 5), None)]
