import random
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.pool_accounting import (
    MAX_BUFFER_MINUTES,
    MAX_SPAN,
    Admission,
    AdmissionOutcome,
    Commitment,
    Shortfall,
    StartOutcome,
    admit_appointment,
    check_lengths,
    check_start,
    earliest_start,
    free_over,
    interval_end,
    one_per_key,
    shortfalls,
)
from app.domain.pool_staffing import Intake, PoolStaffing, Segment, Supply, supply_between
from app.domain.scheduling import MAX_CAPACITY, Offer, ScheduleRule, TimeWindow

ADMITTED = AdmissionOutcome.ADMITTED
FULL = AdmissionOutcome.FULL
NOT_STAFFED = AdmissionOutcome.NOT_STAFFED


def at(clock: str, day: int = 5) -> datetime:
    hours, minutes = clock.split(":")
    return datetime(2026, 1, day, int(hours), int(minutes), tzinfo=UTC)


def supply(*segments: tuple[str, str, int, Offer]) -> Supply:
    return Supply(tuple(Segment(at(a), at(b), u, o) for a, b, u, o in segments))


def morning(units: int = 2, offer: Offer = Offer.BOTH) -> Supply:
    return supply(("09:00", "12:00", units, offer))


def held(number: int, start: str, until: str, *, in_progress: bool = False) -> Commitment:
    return Commitment(("booking", number), at(start), at(until), in_progress)


def minutes_later(start: datetime, minutes: int) -> datetime:
    return start + timedelta(minutes=minutes)


# --- A11: two flows share a pool of two ---


def test_a11_a_pool_of_two_never_holds_three_at_once() -> None:
    pool = morning(2)
    flow_30 = [held(1, "10:00", "10:30")]  # one flow, 30 minutes
    both = [*flow_30, held(2, "10:15", "11:15")]  # another flow, 60 minutes

    # Every start whose interval touches the moment with two holds is full.
    for start, minutes in (("10:10", 10), ("10:00", 30), ("10:15", 15), ("10:20", 30)):
        assert admit_appointment(pool, both, at(start), minutes, 0).outcome is FULL
    assert admit_appointment(pool, both, at("09:45"), 30, 0) == Admission(ADMITTED, 1)  # ends 10:15
    assert admit_appointment(pool, flow_30, at("10:00"), 30, 0) == Admission(ADMITTED, 1)
    assert admit_appointment(pool, both, at("10:30"), 30, 0) == Admission(ADMITTED, 1)
    assert admit_appointment(pool, both, at("11:15"), 30, 0) == Admission(ADMITTED, 2)


def test_a11_booking_until_full_never_exceeds_the_units() -> None:
    pool = morning(2)
    taken: list[Commitment] = []
    for number in range(1, 6):
        if admit_appointment(pool, taken, at("10:00"), 30, 0).admitted:
            taken.append(held(number, "10:00", "10:30"))

    assert len(taken) == 2


# --- A12: independent pools ---


def test_a12_a_full_pool_leaves_another_pool_untouched() -> None:
    first, second = morning(1), morning(1)
    commitments_first = [held(1, "10:00", "10:30")]

    assert admit_appointment(first, commitments_first, at("10:00"), 30, 0).outcome is FULL
    assert admit_appointment(second, [], at("10:00"), 30, 0) == Admission(ADMITTED, 1)


# --- A13: a unit counts once whatever skills it holds ---


def test_a13_one_unit_serves_one_commitment_whatever_flow_asks() -> None:
    pool = morning(1)
    needs_x = [held(1, "10:00", "10:30")]

    assert admit_appointment(pool, needs_x, at("10:00"), 30, 0).outcome is FULL


# --- A14: appointments of different lengths ---


@pytest.mark.parametrize(
    ("start", "minutes", "outcome", "free"),
    [
        ("09:00", 30, FULL, 0),  # only one place is taken *at* 09:00, but 09:15 has two
        ("09:00", 15, ADMITTED, 1),
        ("09:15", 15, FULL, 0),
        ("09:30", 30, ADMITTED, 2),
    ],
)
def test_a14_fifteen_and_thirty_minute_flows_share_one_pool(
    start: str, minutes: int, outcome: AdmissionOutcome, free: int
) -> None:
    pool = supply(("09:00", "11:00", 2, Offer.SCHEDULED))
    booked = [held(1, "09:00", "09:30"), held(2, "09:15", "09:30")]

    assert admit_appointment(pool, booked, at(start), minutes, 0) == Admission(outcome, free)


def test_a14_a_ninety_minute_appointment_across_a_gap_in_staffing_is_not_staffed() -> None:
    pool = supply(("09:00", "12:00", 2, Offer.SCHEDULED), ("12:30", "17:00", 2, Offer.SCHEDULED))

    assert admit_appointment(pool, [], at("11:00"), 90, 0) == Admission(NOT_STAFFED, 0)
    assert admit_appointment(pool, [], at("10:30"), 90, 0) == Admission(ADMITTED, 2)
    assert admit_appointment(pool, [], at("12:30"), 90, 0) == Admission(ADMITTED, 2)


def test_a14_touching_blocks_with_one_unit_are_one_stretch_and_a_neighbour_makes_it_full() -> None:
    pool = supply(
        ("09:00", "12:00", 2, Offer.SCHEDULED),
        ("12:00", "12:30", 1, Offer.SCHEDULED),
        ("12:30", "17:00", 2, Offer.SCHEDULED),
    )
    booked = [held(1, "12:30", "13:00"), held(2, "12:30", "13:00")]

    assert admit_appointment(pool, [], at("12:00"), 90, 0) == Admission(ADMITTED, 1)
    assert admit_appointment(pool, booked[:1], at("12:00"), 90, 0) == Admission(ADMITTED, 1)
    assert admit_appointment(pool, booked, at("12:00"), 90, 0) == Admission(FULL, 0)


def test_a14_the_capacity_across_touching_blocks_is_the_lower_one() -> None:
    pool = supply(("09:00", "10:00", 3, Offer.BOTH), ("10:00", "11:00", 1, Offer.BOTH))
    booked = [held(1, "10:00", "10:30")]

    assert free_over(pool, [], at("09:30"), at("10:30"), Intake.APPOINTMENT) == 1
    assert admit_appointment(pool, booked, at("09:30"), 60, 0).outcome is FULL


def _minute_by_minute_free(
    pool: Supply, commitments: list[Commitment], lower: datetime, upper: datetime
) -> int:
    lowest = 10**6
    moment = lower
    while moment < upper:
        load = sum(1 for c in commitments if c.start <= moment < c.until)
        lowest = min(lowest, pool.units_at(moment, Intake.APPOINTMENT) - load)
        moment += timedelta(minutes=1)
    return lowest


def test_a14_free_units_equal_a_minute_by_minute_count_on_generated_days() -> None:
    rng = random.Random(20261111)  # noqa: S311
    for _ in range(1500):
        segments: list[Segment] = []
        cursor = at("08:00")
        for _ in range(rng.randint(1, 4)):
            cursor += timedelta(minutes=rng.choice([0, 0, 15, 30]))
            end = cursor + timedelta(minutes=rng.choice([30, 60, 90]))
            segments.append(Segment(cursor, end, rng.randint(1, 3), rng.choice(list(Offer))))
            cursor = end
        pool = Supply(tuple(segments))
        commitments = []
        for number in range(rng.randint(0, 6)):
            start = at("08:00") + timedelta(minutes=5 * rng.randint(0, 60))
            commitments.append(
                Commitment(
                    ("booking", number), start, start + timedelta(minutes=5 * rng.randint(1, 18))
                )
            )
        lower = at("08:00") + timedelta(minutes=5 * rng.randint(0, 50))
        upper = lower + timedelta(minutes=5 * rng.randint(1, 24))

        expected = _minute_by_minute_free(pool, commitments, lower, upper)

        assert free_over(pool, commitments, lower, upper, Intake.APPOINTMENT) == expected
        minutes = int((upper - lower).total_seconds() // 60)
        staffed = all(
            pool.units_at(lower + timedelta(minutes=m), Intake.APPOINTMENT) > 0
            for m in range(minutes)
        )
        admission = admit_appointment(pool, commitments, lower, minutes, 0)
        assert admission.admitted == (staffed and expected >= 1)
        assert (admission.outcome is NOT_STAFFED) == (not staffed)


def test_touching_intervals_do_not_overlap() -> None:
    pool = morning(1)
    booked = [held(1, "10:00", "10:30")]

    assert admit_appointment(pool, booked, at("09:30"), 30, 0).admitted  # ends at 10:00
    assert admit_appointment(pool, booked, at("10:30"), 30, 0).admitted  # starts at 10:30
    assert not admit_appointment(pool, booked, at("09:30"), 35, 0).admitted
    assert not admit_appointment(pool, booked, at("10:25"), 30, 0).admitted
    assert not admit_appointment(pool, booked, at("09:30"), 30, 5).admitted  # buffer reaches 10:05


def test_the_buffer_is_part_of_the_interval() -> None:
    pool = morning(1)

    assert interval_end(at("10:00"), 30, 5) == at("10:35")
    assert admit_appointment(pool, [held(1, "10:30", "10:35")], at("10:00"), 30, 0).admitted
    assert not admit_appointment(pool, [held(1, "10:30", "10:35")], at("10:00"), 30, 5).admitted


# --- P7: what a block offers ---


def test_p7_an_appointment_is_not_staffed_in_a_block_for_work_without_appointment() -> None:
    pool = supply(("09:00", "12:00", 2, Offer.DIRECT))

    assert admit_appointment(pool, [], at("10:00"), 30, 0) == Admission(NOT_STAFFED, 0)
    assert check_start(pool, [], at("10:00"), 20, 0).may_start


def test_p7_work_without_appointment_may_not_start_in_an_appointments_only_block() -> None:
    pool = supply(("09:00", "12:00", 2, Offer.SCHEDULED))

    refused = check_start(pool, [], at("10:00"), 20, 0)

    assert refused.outcome is StartOutcome.REFUSED
    assert (refused.free, refused.short_at) == (0, at("10:00"))
    assert admit_appointment(pool, [], at("10:00"), 30, 0).admitted


def test_p7_a_block_that_changes_offer_inside_the_interval_refuses_the_whole_interval() -> None:
    pool = supply(("09:00", "10:00", 2, Offer.BOTH), ("10:00", "11:00", 2, Offer.DIRECT))

    assert admit_appointment(pool, [], at("09:45"), 30, 0).outcome is NOT_STAFFED
    assert admit_appointment(pool, [], at("09:30"), 30, 0).admitted


# --- boundaries and bounds ---


def test_units_of_zero_leave_nothing_to_book_and_the_maximum_is_counted_exactly() -> None:
    assert admit_appointment(Supply(), [], at("10:00"), 30, 0) == Admission(NOT_STAFFED, 0)
    big = morning(MAX_CAPACITY)
    nearly = [Commitment(("booking", n), at("10:00"), at("10:30")) for n in range(MAX_CAPACITY - 1)]
    assert admit_appointment(big, nearly, at("10:00"), 30, 0) == Admission(ADMITTED, 1)
    full = [*nearly, Commitment(("booking", MAX_CAPACITY), at("10:00"), at("10:30"))]
    assert admit_appointment(big, full, at("10:00"), 30, 0) == Admission(FULL, 0)


@pytest.mark.parametrize(("duration", "buffer"), [(5, 0), (480, 120), (5, 120), (480, 0)])
def test_the_bounds_of_duration_and_buffer_are_allowed(duration: int, buffer: int) -> None:
    check_lengths(duration, buffer)
    assert interval_end(at("09:00"), duration, buffer) == at("09:00") + timedelta(
        minutes=duration + buffer
    )


@pytest.mark.parametrize(
    ("duration", "buffer", "field"),
    [(0, 0, "duration"), (4, 0, "duration"), (7, 0, "duration"), (485, 0, "duration")]
    + [(30, -1, "buffer"), (30, 121, "buffer")],
)
def test_a_duration_or_buffer_out_of_bounds_is_refused(
    duration: int, buffer: int, field: str
) -> None:
    with pytest.raises(DomainError) as caught:
        check_lengths(duration, buffer)
    assert (caught.value.code, caught.value.field) == (ErrorCode.INVALID_CONFIGURATION, field)
    for call in (
        lambda: admit_appointment(morning(), [], at("10:00"), duration, buffer),
        lambda: check_start(morning(), [], at("10:00"), duration, buffer),
        lambda: earliest_start(morning(), [], at("10:00"), duration, buffer, at("11:00")),
    ):
        with pytest.raises(DomainError):
            call()


def test_the_longest_commitment_is_the_longest_length_plus_the_longest_buffer() -> None:
    assert MAX_SPAN == timedelta(minutes=480 + MAX_BUFFER_MINUTES) == timedelta(minutes=600)


def test_a_commitment_must_end_after_it_starts() -> None:
    with pytest.raises(ValueError, match="end after"):
        Commitment(("booking", 1), at("10:00"), at("10:00"))
    with pytest.raises(ValueError, match="end after"):
        Commitment(("booking", 1), at("10:00"), at("09:00"))
    Commitment(("booking", 1), at("10:00"), at("10:01"))


def test_an_empty_or_reversed_interval_is_refused() -> None:
    for lower, upper in ((at("10:00"), at("10:00")), (at("10:00"), at("09:00"))):
        with pytest.raises(ValueError, match="lower"):
            free_over(morning(), [], lower, upper, Intake.APPOINTMENT)
        with pytest.raises(ValueError, match="lower"):
            shortfalls(morning(), [], lower, upper)


def test_free_over_leaves_out_an_ignored_key_and_the_interval_edges_do_not_count() -> None:
    pool = morning(1)
    booked = [held(1, "10:00", "10:30"), held(2, "09:00", "10:00"), held(3, "10:30", "11:00")]

    assert free_over(pool, booked, at("10:00"), at("10:30"), Intake.APPOINTMENT) == 0
    assert (
        free_over(pool, booked, at("10:00"), at("10:30"), Intake.APPOINTMENT, ignore=("booking", 1))
        == 1
    )


def test_one_commitment_per_key_the_last_wins() -> None:
    hold = held(1, "10:00", "10:30")
    occupation = Commitment(("booking", 1), at("10:00"), at("10:45"), in_progress=True)
    other = held(2, "10:00", "10:30")

    assert one_per_key([hold, other, occupation]) == [occupation, other]
    assert one_per_key([]) == []


# --- A17: immediate work against reserved capacity ---


def reservations() -> list[Commitment]:
    return [held(1, "10:00", "10:30"), held(2, "10:00", "10:30")]


def test_a17_the_brief_example_a_ticket_may_wait_but_treatment_may_not_start() -> None:
    pool = morning(2)

    check = check_start(pool, reservations(), at("09:55"), 20, 0)

    assert check.outcome is StartOutcome.REFUSED
    assert not check.may_start
    assert (check.free, check.short_at) == (0, at("10:00"))
    assert earliest_start(pool, reservations(), at("09:55"), 20, 0, at("12:00")) == at("10:30")


def test_a17_ten_thirty_is_the_first_start_and_nothing_earlier_passes() -> None:
    pool = morning(2)
    for minute in range(55, 60):
        assert not check_start(pool, reservations(), at(f"09:{minute}"), 20, 0).may_start
    for clock in ("10:00", "10:29"):
        assert not check_start(pool, reservations(), at(clock), 20, 0).may_start
    assert check_start(pool, reservations(), at("10:30"), 20, 0) == check_start(
        pool, [], at("10:30"), 20, 0
    )
    assert check_start(pool, reservations(), at("10:30"), 20, 0).free == 2


def test_a17_a_request_that_ends_before_the_reservations_may_start() -> None:
    pool = morning(2)

    check = check_start(pool, reservations(), at("09:35"), 20, 0)

    assert (check.outcome, check.free, check.short_at) == (StartOutcome.MAY_START, 2, None)


def test_a17_with_a_buffer_the_interval_reaches_further() -> None:
    pool = morning(2)

    assert not check_start(pool, reservations(), at("09:40"), 20, 5).may_start  # to 10:05
    assert check_start(pool, reservations(), at("09:35"), 20, 5).may_start  # to 10:00, touching
    assert earliest_start(pool, reservations(), at("09:40"), 20, 5, at("12:00")) == at("10:30")


def test_a17_a_reservation_that_checks_in_keeps_its_own_unit() -> None:
    pool = morning(2)
    own = ("booking", 1)

    check = check_start(pool, reservations(), at("10:00"), 30, 0, own_key=own)

    assert (check.outcome, check.free) == (StartOutcome.MAY_START, 1)
    started = [Commitment(own, at("10:00"), at("10:30"), True), reservations()[1]]
    assert (
        free_over(pool, started, at("10:00"), at("10:30"), Intake.ON_DEMAND) == 0
    )  # the load stays 2


def test_the_hold_and_the_occupation_of_one_booking_count_once() -> None:
    pool = morning(2)
    hold = held(1, "10:00", "10:30")
    occupation = Commitment(("booking", 1), at("10:00"), at("10:30"), True)

    assert free_over(pool, [hold, occupation], at("10:00"), at("10:30"), Intake.APPOINTMENT) == 1


# --- earliest start in detail ---


def test_earliest_start_is_now_when_it_fits() -> None:
    assert earliest_start(morning(2), [], at("09:10"), 20, 0, at("12:00")) == at("09:10")


def test_earliest_start_waits_for_the_staffing_to_begin() -> None:
    assert earliest_start(morning(2), [], at("08:40"), 20, 0, at("12:00")) == at("09:00")
    assert earliest_start(morning(2), [], at("08:40"), 20, 0, at("08:59")) is None


def test_earliest_start_includes_the_limit_and_excludes_what_lies_beyond() -> None:
    pool = morning(1)
    booked = [held(1, "10:00", "10:30")]

    assert earliest_start(pool, booked, at("09:55"), 20, 0, at("10:30")) == at("10:30")
    assert earliest_start(pool, booked, at("09:55"), 20, 0, at("10:29")) is None


def test_earliest_start_is_none_when_the_work_does_not_fit_before_staffing_ends() -> None:
    assert earliest_start(morning(2), [], at("11:45"), 20, 0, at("13:00")) is None


def test_earliest_start_skips_a_gap_in_staffing() -> None:
    pool = supply(("09:00", "10:00", 1, Offer.BOTH), ("11:00", "12:00", 1, Offer.BOTH))

    assert earliest_start(pool, [], at("09:50"), 30, 0, at("12:00")) == at("11:00")


def test_earliest_start_never_passes_an_overrun_that_has_no_known_end() -> None:
    pool = morning(1)
    running = [held(1, "09:00", "09:30", in_progress=True)]

    assert earliest_start(pool, running, at("09:45"), 20, 0, at("12:00")) is None
    assert earliest_start(pool, running, at("09:15"), 10, 0, at("12:00")) == at("09:30")
    assert earliest_start(pool, running, at("09:30"), 20, 0, at("12:00")) is None


def test_earliest_start_uses_the_buffer_in_the_interval() -> None:
    pool = morning(1)
    booked = [held(1, "10:00", "10:30")]

    assert earliest_start(pool, booked, at("09:40"), 15, 0, at("12:00")) == at("09:40")
    assert earliest_start(pool, booked, at("09:40"), 15, 10, at("12:00")) == at("10:30")


# --- A18: an overrun stays visible and blocks conflicting starts ---


def overrun_scene() -> list[Commitment]:
    # Q started at 09:35 for 20 minutes (planned until 09:55) and still runs at 10:05.
    q = Commitment(("request", 7), at("09:35"), at("09:55"), in_progress=True)
    return [q, *reservations()]


def test_a18_the_overrun_is_listed_as_a_shortfall_while_it_lasts() -> None:
    pool = morning(2)
    now = at("10:05")

    assert shortfalls(pool, overrun_scene(), now, at("10:30"), overrun_at=now) == [
        Shortfall(at("10:05"), at("10:30"), 2, 3)
    ]
    assert shortfalls(pool, overrun_scene(), now, at("10:30")) == []  # planned ends only


def test_a18_a_start_that_conflicts_with_the_overrun_is_refused() -> None:
    pool = morning(2)

    check = check_start(pool, overrun_scene(), at("10:05"), 10, 0)

    assert check.outcome is StartOutcome.REFUSED
    assert (check.free, check.short_at) == (-1, at("10:05"))


def test_a18_an_overrun_never_refuses_a_future_appointment() -> None:
    pool = morning(2)

    assert admit_appointment(pool, overrun_scene(), at("10:30"), 30, 0).admitted
    assert not admit_appointment(pool, overrun_scene(), at("10:00"), 30, 0).admitted  # reservations


def test_a18_a_commitment_not_in_progress_past_its_end_is_no_overrun() -> None:
    pool = morning(1)
    done = [held(1, "09:00", "09:30")]

    assert check_start(pool, done, at("09:30"), 20, 0).may_start
    assert (
        free_over(pool, done, at("09:30"), at("09:50"), Intake.ON_DEMAND, overrun_at=at("09:30"))
        == 1
    )


def test_a18_a_commitment_in_progress_at_its_planned_end_already_counts_as_busy() -> None:
    pool = morning(1)
    running = [held(1, "09:00", "09:30", in_progress=True)]

    assert not check_start(pool, running, at("09:30"), 20, 0).may_start
    assert check_start(pool, running, at("09:29"), 1 * 5, 0).free == 0  # still inside its interval
    assert (
        free_over(pool, running, at("09:30"), at("09:50"), Intake.ON_DEMAND) == 1
    )  # no overrun_at


def test_p8_a_due_appointment_may_start_during_an_overrun_and_the_conflict_is_recorded() -> None:
    pool = morning(2)
    own = ("booking", 1)

    check = check_start(
        pool, overrun_scene(), at("10:05"), 30, 0, intake=Intake.APPOINTMENT, own_key=own
    )

    assert check.outcome is StartOutcome.OVERRUN_CONFLICT
    assert check.may_start
    assert (check.free, check.short_at) == (0, at("10:05"))


def test_p8_an_early_appointment_start_is_refused_until_a_unit_is_free() -> None:
    pool = morning(2)

    check = check_start(
        pool, overrun_scene(), at("09:58"), 30, 0, intake=Intake.APPOINTMENT, own_key=("booking", 1)
    )

    assert check.outcome is StartOutcome.REFUSED
    assert (check.free, check.short_at) == (0, at("10:00"))


def test_p8_due_means_the_own_interval_has_begun_and_not_ended() -> None:
    pool = morning(1)
    own = ("booking", 1)
    commitments = [
        held(1, "10:00", "10:30"),
        Commitment(("request", 9), at("09:30"), at("09:50"), in_progress=True),
    ]

    on_the_dot = check_start(pool, commitments, at("10:00"), 30, 0, own_key=own)
    last_minute = check_start(pool, commitments, at("10:29"), 5, 0, own_key=own)
    after_its_end = check_start(pool, commitments, at("10:30"), 5, 0, own_key=own)
    unknown_key = check_start(pool, commitments, at("10:00"), 30, 0, own_key=("booking", 99))

    assert on_the_dot.outcome is StartOutcome.OVERRUN_CONFLICT
    assert last_minute.outcome is StartOutcome.OVERRUN_CONFLICT
    assert after_its_end.outcome is StartOutcome.REFUSED
    assert unknown_key.outcome is StartOutcome.REFUSED


def test_p8_work_without_an_appointment_is_refused_during_the_overrun() -> None:
    check = check_start(morning(2), overrun_scene(), at("10:05"), 30, 0)

    assert check.outcome is StartOutcome.REFUSED


def test_p8_a_due_appointment_with_a_free_unit_starts_without_conflict() -> None:
    pool = morning(2)

    check = check_start(pool, reservations(), at("10:00"), 30, 0, own_key=("booking", 1))

    assert check.outcome is StartOutcome.MAY_START


# --- A35: a late start rechecks current capacity ---


def test_a35_the_same_request_is_decided_again_on_the_staffing_and_load_of_that_moment() -> None:
    request = (at("09:35"), 20, 0)
    assert check_start(morning(2), reservations(), *request).may_start

    # Staffing dropped in between.
    assert (
        check_start(morning(1), [held(3, "09:40", "09:50")], *request).outcome
        is StartOutcome.REFUSED
    )
    # A reservation was made in between.
    taken = [*reservations(), held(3, "09:50", "10:10"), held(4, "09:50", "10:10")]
    assert not check_start(morning(2), taken, *request).may_start
    # The block no longer takes work without appointment.
    assert not check_start(morning(2, Offer.SCHEDULED), reservations(), *request).may_start
    # A later moment that overlaps the reservations.
    assert not check_start(morning(2), reservations(), at("09:45"), 20, 0).may_start


# --- A36: staffing falls below the commitments ---


def test_a36_nothing_is_rewritten_new_admission_is_refused_and_the_shortage_is_listed() -> None:
    commitments = reservations()
    before = list(commitments)
    pool = morning(1)

    assert shortfalls(pool, commitments, at("08:00"), at("13:00")) == [
        Shortfall(at("10:00"), at("10:30"), 1, 2)
    ]
    assert commitments == before
    assert admit_appointment(pool, commitments, at("10:00"), 30, 0) == Admission(FULL, -1)
    assert admit_appointment(pool, commitments, at("09:45"), 30, 0).outcome is FULL
    assert admit_appointment(pool, commitments, at("09:30"), 30, 0).admitted  # ends at 10:00
    assert admit_appointment(pool, commitments, at("10:30"), 30, 0).admitted


def test_a36_a_commitment_outside_the_staffing_is_a_shortage_with_zero_units() -> None:
    pool = supply(("09:00", "10:00", 1, Offer.BOTH))

    assert shortfalls(pool, [held(1, "09:30", "10:30")], at("08:00"), at("12:00")) == [
        Shortfall(at("10:00"), at("10:30"), 0, 1)
    ]
    assert shortfalls(
        pool, [held(1, "09:30", "10:30"), held(2, "09:30", "09:45")], at("08:00"), at("12:00")
    ) == [
        Shortfall(at("09:30"), at("09:45"), 1, 2),
        Shortfall(at("10:00"), at("10:30"), 0, 1),
    ]
    assert shortfalls(Supply(), [held(1, "09:30", "10:00")], at("09:00"), at("12:00")) == [
        Shortfall(at("09:30"), at("10:00"), 0, 1)
    ]


def test_a36_equal_load_and_units_is_no_shortage() -> None:
    assert shortfalls(morning(2), reservations(), at("08:00"), at("13:00")) == []


def test_a36_neighbouring_stretches_merge_when_units_and_load_agree_and_split_otherwise() -> None:
    pool = supply(("09:00", "10:00", 1, Offer.BOTH), ("10:00", "11:00", 1, Offer.BOTH))
    steady = [held(1, "09:00", "11:00"), held(2, "09:00", "11:00")]
    changing = [*steady, held(3, "10:00", "10:30")]

    assert shortfalls(pool, steady, at("08:00"), at("12:00")) == [
        Shortfall(at("09:00"), at("11:00"), 1, 2)
    ]
    assert shortfalls(pool, changing, at("08:00"), at("12:00")) == [
        Shortfall(at("09:00"), at("10:00"), 1, 2),
        Shortfall(at("10:00"), at("10:30"), 1, 3),
        Shortfall(at("10:30"), at("11:00"), 1, 2),
    ]


def test_a36_two_separate_shortages_are_two_entries_and_the_range_clips_them() -> None:
    pool = supply(("09:00", "13:00", 1, Offer.BOTH))
    commitments = [
        held(1, "09:00", "10:00"),
        held(2, "09:30", "10:00"),
        held(3, "11:00", "12:00"),
        held(4, "11:00", "11:30"),
    ]

    assert shortfalls(pool, commitments, at("08:00"), at("13:00")) == [
        Shortfall(at("09:30"), at("10:00"), 1, 2),
        Shortfall(at("11:00"), at("11:30"), 1, 2),
    ]
    assert shortfalls(pool, commitments, at("09:45"), at("11:15")) == [
        Shortfall(at("09:45"), at("10:00"), 1, 2),
        Shortfall(at("11:00"), at("11:15"), 1, 2),
    ]


def test_a36_a_unit_for_work_that_the_block_does_not_offer_still_counts_as_a_person() -> None:
    pool = supply(("09:00", "10:00", 2, Offer.DIRECT))

    assert (
        shortfalls(
            pool, [held(1, "09:00", "10:00"), held(2, "09:00", "10:00")], at("08:00"), at("11:00")
        )
        == []
    )


# --- the two days on which the clock changes, 90-minute appointments ---


AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def night_pool(weekday: int, day: date) -> Supply:
    rule = ScheduleRule(weekday, TimeWindow(time(0), time(6), 1))
    start = datetime.combine(day, time()).replace(tzinfo=UTC)
    return supply_between(
        PoolStaffing(AMSTERDAM, (rule,)),
        frozenset(),
        start - timedelta(hours=3),
        start + timedelta(hours=30),
    )


def test_a_ninety_minute_appointment_across_the_spring_change_is_measured_in_real_time() -> None:
    pool = night_pool(6, date(2026, 3, 29))  # 00:00-06:00 local is 23:00-04:00 UTC, five hours
    start = datetime(2026, 3, 29, 0, 30, tzinfo=UTC)  # 01:30 CET; 90 minutes end 04:00 CEST

    assert admit_appointment(pool, [], start, 90, 0) == Admission(ADMITTED, 1)
    assert admit_appointment(pool, [], start + timedelta(minutes=150), 90, 0).outcome is NOT_STAFFED
    other = [
        Commitment(
            ("booking", 1),
            datetime(2026, 3, 29, 1, 30, tzinfo=UTC),
            datetime(2026, 3, 29, 2, 30, tzinfo=UTC),
        )
    ]
    assert admit_appointment(pool, other, start, 90, 0).outcome is FULL


def test_a_ninety_minute_appointment_across_the_fall_change_is_measured_in_real_time() -> None:
    pool = night_pool(6, date(2026, 10, 25))  # 00:00-06:00 local is 22:00-05:00 UTC, seven hours
    start = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # 02:30 CEST; 90 minutes end 03:00 CET

    assert admit_appointment(pool, [], start, 90, 0) == Admission(ADMITTED, 1)
    assert (
        admit_appointment(pool, [], datetime(2026, 10, 25, 4, 0, tzinfo=UTC), 90, 0).outcome
        is NOT_STAFFED
    )
    assert admit_appointment(pool, [], datetime(2026, 10, 25, 3, 30, tzinfo=UTC), 90, 0).admitted
