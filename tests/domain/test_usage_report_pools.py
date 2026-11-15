"""The utilisation of a counted pool: C1 to C3 per pool, small cells hidden (ADR 0122 step 5).

A pool of Amsterdam, Mondays 09:00 to 12:00 (08:00 to 11:00 UTC in January) with two units for
appointments. Monday 2026-01-05 is the day of the tests; ``NOW`` is long after it, so every hour
counts unless a test says otherwise.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.pool_staffing import PoolStaffing
from app.domain.scheduling import Offer, ScheduleOverride, ScheduleRule, TimeWindow
from app.domain.slot_close import CLOSE_DELAY
from app.domain.usage_report_pools import Interval, PoolInput, pool_report

DAY = date(2026, 1, 5)
NINE = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # 09:00 Amsterdam
NOW = datetime(2026, 1, 12, 12, 0, tzinfo=UTC)
THRESHOLD = 3
AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def monday(units: int = 2, offer: Offer = Offer.BOTH) -> ScheduleRule:
    return ScheduleRule(0, TimeWindow(time(9), time(12), units, None, offer))


def pool(
    booked: tuple[Interval, ...] = (),
    *,
    rules: tuple[ScheduleRule, ...] | None = None,
    overrides: tuple[ScheduleOverride, ...] = (),
    closed: frozenset[date] = frozenset(),
    archived: bool = False,
    staffing: bool = True,
    name: str = "Loket",
    pool_id: int = 1,
) -> PoolInput:
    plan = PoolStaffing(AMSTERDAM, rules if rules is not None else (monday(),), overrides)
    return PoolInput(pool_id, name, archived, plan if staffing else None, closed, booked)


def appointment(at: datetime, minutes: int = 35) -> Interval:
    return at, at + timedelta(minutes=minutes)


def report(*pools: PoolInput, now: datetime = NOW, threshold: int = THRESHOLD):  # type: ignore[no-untyped-def]
    return pool_report(pools, DAY, DAY, now=now, threshold=threshold)


def test_utilisation_is_booked_unit_time_over_staffed_unit_time() -> None:
    held = tuple(appointment(NINE + timedelta(minutes=m)) for m in (0, 0, 60))  # 3 x 35 min

    [row] = report(pool(held)).pools

    assert row.staffed_minutes == 2 * 180
    assert row.appointments == 3
    assert row.booked_minutes == 105
    assert row.utilisation_percent == round(100 * 105 / 360)
    assert row.idle_minutes == 360 - 105


def test_the_fill_of_each_staffed_hour_is_classed_like_a_slot() -> None:
    rules = (ScheduleRule(0, TimeWindow(time(9), time(21), 2, None, Offer.BOTH)),)  # 12 hours
    hours = [NINE + timedelta(hours=h) for h in range(12)]
    under_half = [appointment(at, 30) for at in hours[3:6]]  # 30 of 120 minutes
    under_full = [appointment(at, 35) for at in hours[6:9] for _ in range(2)]  # 70 of 120
    full = [(at, at + timedelta(hours=1)) for at in hours[9:] for _ in range(2)]  # 120 of 120

    [row] = report(pool((*under_half, *under_full, *full), rules=rules)).pools

    assert [(r.code, r.count) for r in row.fill.rows] == [
        ("empty", 3),
        ("under_half", 3),
        ("under_full", 3),
        ("full", 3),
    ]
    assert row.fill.total == 12


def test_a_shortage_shows_as_full_hours_and_never_above_one_hundred_percent() -> None:
    # Three appointments of a full hour on two units, in three hours: staffing was cut.
    rules = (ScheduleRule(0, TimeWindow(time(9), time(18), 2, None, Offer.BOTH)),)
    hours = [NINE + timedelta(hours=h) for h in range(3)]
    held = tuple((at, at + timedelta(hours=1)) for at in hours for _ in range(3))

    [row] = report(pool(held, rules=rules)).pools

    assert (row.fill.rows[3].count, row.fill.rows[0].count) == (3, 6)
    assert row.booked_minutes == 3 * 120
    assert row.utilisation_percent == round(100 * 360 / (9 * 120))
    assert row.idle_minutes == 9 * 120 - 360


def test_a_pool_with_few_appointments_hides_its_figures_but_not_its_staffing() -> None:
    [row] = report(pool((appointment(NINE),))).pools

    assert row.staffed_minutes == 360
    assert row.appointments is None
    assert row.booked_minutes is None
    assert row.utilisation_percent is None
    assert row.idle_minutes is None
    assert [r.count for r in row.fill.rows] == [None] * 4
    assert row.fill.total is None


def test_a_pool_nobody_booked_is_idle_not_hidden() -> None:
    [row] = report(pool()).pools

    assert (row.appointments, row.utilisation_percent, row.idle_minutes) == (0, 0, 360)
    assert row.fill.rows[0].count == 3


def test_only_hours_past_the_closing_delay_count() -> None:
    now = datetime(2026, 1, 5, 10, 30, tzinfo=UTC) + CLOSE_DELAY  # the 08:00 and 09:00 UTC hours

    [row] = report(pool(), now=now).pools

    assert row.staffed_minutes == 2 * 120
    assert report(pool(), now=NINE).pools[0].staffed_minutes == 0


def test_a_block_for_work_without_appointment_adds_no_appointment_capacity() -> None:
    plan = pool(rules=(monday(offer=Offer.DIRECT),))

    [row] = report(plan).pools

    assert (row.staffed_minutes, row.utilisation_percent) == (0, None)


def test_a_holiday_and_a_closed_exception_close_the_day() -> None:
    assert report(pool(closed=frozenset({DAY}))).pools[0].staffed_minutes == 0
    shut = ScheduleOverride(DAY, None)
    assert report(pool(overrides=(shut,))).pools[0].staffed_minutes == 0


def test_an_appointment_outside_the_staffed_hours_counts_for_nothing() -> None:
    late = appointment(NINE + timedelta(hours=8))

    [row] = report(pool((late, late, late))).pools

    assert row.appointments == 0
    assert row.booked_minutes == 0


def test_the_range_is_whole_pool_local_days() -> None:
    next_week = pool_report(
        [pool()], DAY, DAY + timedelta(days=7), now=NOW + timedelta(days=7), threshold=3
    )
    other_day = pool_report(
        [pool()], DAY + timedelta(days=1), DAY + timedelta(days=1), now=NOW, threshold=3
    )

    assert next_week.pools[0].staffed_minutes == 2 * 360
    assert other_day.pools[0].staffed_minutes == 0


def test_a_pool_without_a_time_zone_has_no_staffing_and_an_archived_empty_pool_is_left_out() -> (
    None
):
    result = report(
        pool(staffing=False, name="Zonder"),
        pool(archived=True, staffing=False, name="Oud", pool_id=2),
        pool(name="Aaa", pool_id=3),
    )

    assert [(p.name, p.staffed_minutes) for p in result.pools] == [("Aaa", 360), ("Zonder", 0)]
    assert result.pools[1].utilisation_percent is None


def test_the_report_carries_times_only() -> None:
    result = report(pool())

    assert result.counted_until == NOW - CLOSE_DELAY
    assert result.threshold == THRESHOLD


def test_a_day_with_a_clock_change_is_cut_at_local_midnight_and_staffed_in_full() -> None:
    from app.domain.usage_report_pools import range_bounds

    spring = date(2026, 3, 29)  # Europe/Amsterdam: the clocks go forward at 02:00
    lower, upper = range_bounds(AMSTERDAM, spring, spring)
    sunday = ScheduleRule(6, TimeWindow(time(9), time(12), 2, None, Offer.BOTH))

    result = pool_report(
        [pool(rules=(sunday,))], spring, spring, now=NOW + timedelta(days=90), threshold=THRESHOLD
    )

    assert upper - lower == timedelta(hours=23)
    assert result.pools[0].staffed_minutes == 2 * 180
