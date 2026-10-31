from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

from app.domain.agenda import AgendaSlot
from app.domain.booking import Booking, BookingStatus
from app.domain.rebooking import Proposal, Reach, last_made, may_take, propose, ranked
from app.domain.scheduling import DispatchKind
from tests.support.domain import NOW, SLOT_0900, make_group

GROUP = make_group()
HALF_HOUR = timedelta(minutes=30)
DAY = timedelta(days=1)
REACH = Reach(search_days=14, tolerance_minutes=60)
MON_0900 = SLOT_0900  # Monday 5 January 2026, 09:00 Amsterdam
MON_0930 = MON_0900 + HALF_HOUR
TUE_0900 = MON_0900 + DAY
WED_0900 = MON_0900 + 2 * DAY
THU_0900 = MON_0900 + 3 * DAY
FRI_0900 = MON_0900 + 4 * DAY


def _slot(start: datetime, agents: int = 2, booked: int = 0, *, closed: bool = False) -> AgendaSlot:
    return AgendaSlot(start, start + HALF_HOUR, agents, booked, closed)


def _booking(booking_id: int, start: datetime = MON_0900, made: datetime = NOW) -> Booking:
    return Booking(
        id=booking_id,
        group_id=GROUP.id or 0,
        slot_id=1,
        start=start,
        end=start + HALF_HOUR,
        status=BookingStatus.CONFIRMED,
        contact=None,
        locale="nl",
        token_hash="h",
        created_at=made,
    )


def _starts(slots: list[AgendaSlot]) -> list[datetime]:
    return [slot.start for slot in slots]


def test_the_same_time_on_another_day_beats_a_nearer_time_on_the_same_day() -> None:
    slots = [_slot(MON_0900, 1, 3), _slot(MON_0930), _slot(TUE_0900), _slot(WED_0900)]

    assert _starts(ranked(GROUP, MON_0900, slots, REACH, NOW)) == [
        TUE_0900,
        WED_0900,
        MON_0930,
    ]


def test_an_earlier_day_is_proposed_when_it_is_nearer() -> None:
    slots = [_slot(MON_0900), _slot(FRI_0900)]

    assert _starts(ranked(GROUP, WED_0900, slots, REACH, NOW)) == [MON_0900, FRI_0900]


def test_equally_near_days_go_to_the_earlier_one() -> None:
    slots = [_slot(FRI_0900), _slot(MON_0900)]

    assert _starts(ranked(GROUP, WED_0900, slots, REACH, NOW)) == [MON_0900, FRI_0900]


def test_a_smaller_time_difference_beats_a_nearer_day() -> None:
    slots = [_slot(TUE_0900 + 2 * HALF_HOUR), _slot(WED_0900 + HALF_HOUR)]

    assert _starts(ranked(GROUP, MON_0900, slots, REACH, NOW)) == [
        WED_0900 + HALF_HOUR,
        TUE_0900 + 2 * HALF_HOUR,
    ]


def test_times_beyond_the_tolerance_are_never_proposed() -> None:
    slots = [_slot(TUE_0900 + HALF_HOUR), _slot(TUE_0900 + 3 * HALF_HOUR)]

    assert _starts(ranked(GROUP, MON_0900, slots, REACH, NOW)) == [TUE_0900 + HALF_HOUR]
    assert ranked(GROUP, MON_0900, slots, Reach(14, 0), NOW) == []


def test_days_beyond_the_search_period_are_never_proposed() -> None:
    slots = [_slot(TUE_0900), _slot(FRI_0900)]

    assert _starts(ranked(GROUP, MON_0900, slots, Reach(2, 60), NOW)) == [TUE_0900]


def test_full_closed_short_and_own_slots_are_no_candidates() -> None:
    slots = [
        _slot(MON_0900),
        _slot(TUE_0900, 2, 2),
        _slot(WED_0900, 2, 0, closed=True),
        _slot(THU_0900, 1, 2),
        _slot(FRI_0900, 0, 0),
    ]

    assert ranked(GROUP, MON_0900, slots, REACH, NOW) == []


def test_a_slot_within_the_planner_lead_is_no_candidate() -> None:
    now = TUE_0900 - timedelta(minutes=GROUP.planner_change_min_lead_minutes)

    assert not may_take(GROUP, TUE_0900, now)
    assert may_take(GROUP, TUE_0900, now - timedelta(minutes=1))
    assert ranked(GROUP, MON_0900, [_slot(TUE_0900)], REACH, now) == []


@pytest.mark.parametrize("kind", list(DispatchKind))
def test_a_slot_within_the_participants_change_lead_is_no_candidate(kind: DispatchKind) -> None:
    """ADR 0069: the participant's change lead applies to both kinds; a day ahead is far enough."""
    group = make_group(dispatch_kind=kind, citizen_change_lead_minutes=180)
    two_hours_before = FRI_0900 - timedelta(hours=2)

    assert may_take(replace(group, citizen_change_lead_minutes=60), FRI_0900, two_hours_before)
    assert not may_take(group, FRI_0900, two_hours_before)
    assert may_take(group, FRI_0900, FRI_0900 - timedelta(days=1))


def test_each_booking_gets_its_best_slot_until_the_room_runs_out() -> None:
    movers = [_booking(1), _booking(2), _booking(3)]
    slots = [_slot(MON_0900, 1, 4), _slot(TUE_0900, 2, 1), _slot(WED_0900, 1, 0)]

    assert propose(GROUP, movers, slots, REACH, NOW) == [
        Proposal(3, MON_0900, TUE_0900),
        Proposal(2, MON_0900, WED_0900),
        Proposal(1, MON_0900, None),
    ]


def test_the_last_made_booking_is_served_first() -> None:
    older, newer = _booking(7, made=NOW - DAY), _booking(5, made=NOW)
    slots = [_slot(MON_0900, 1, 3), _slot(TUE_0900, 1, 0)]

    proposals = propose(GROUP, [older, newer], slots, REACH, NOW)

    assert proposals == [Proposal(5, MON_0900, TUE_0900), Proposal(7, MON_0900, None)]


def test_earlier_slots_are_served_before_later_ones() -> None:
    movers = [_booking(1, start=WED_0900), _booking(2, start=MON_0900)]
    slots = [_slot(MON_0900, 0, 1), _slot(WED_0900, 0, 1), _slot(TUE_0900, 1, 0)]

    assert propose(GROUP, movers, slots, REACH, NOW) == [
        Proposal(2, MON_0900, TUE_0900),
        Proposal(1, WED_0900, None),
    ]


def test_no_candidate_falls_back_to_ask_to_rebook() -> None:
    assert propose(GROUP, [_booking(1)], [_slot(MON_0900, 0, 1)], REACH, NOW) == [
        Proposal(1, MON_0900, None)
    ]


def test_the_local_time_is_kept_across_a_daylight_saving_change() -> None:
    before = datetime(2026, 3, 27, 8, 0, tzinfo=UTC)  # Friday 09:00 CET
    after = datetime(2026, 3, 30, 7, 0, tzinfo=UTC)  # Monday 09:00 CEST
    after_utc_twin = datetime(2026, 3, 30, 8, 0, tzinfo=UTC)  # Monday 10:00 CEST

    best = ranked(GROUP, before, [_slot(after_utc_twin), _slot(after)], REACH, NOW)

    assert _starts(best) == [after, after_utc_twin]


def test_last_made_takes_the_newest_and_breaks_ties_by_id() -> None:
    bookings = [_booking(1, made=NOW), _booking(3, made=NOW - DAY), _booking(2, made=NOW)]

    assert [b.id for b in last_made(bookings, 2)] == [2, 1]
    assert last_made(bookings, 0) == []
    assert last_made(bookings, -1) == []


def test_an_unstored_booking_cannot_be_proposed() -> None:
    unstored = replace(_booking(1), id=None)

    with pytest.raises(ValueError, match="not been stored"):
        propose(GROUP, [unstored], [_slot(TUE_0900)], REACH, NOW)


GRID = [MON_0900 + d * DAY + h * HALF_HOUR for d, h in product(range(5), range(4))]


@pytest.mark.parametrize("seed", range(40))
def test_proposals_never_overfill_a_slot_and_ignore_input_order(seed: int) -> None:
    # A spread of agents and bookings over a week, varied per seed without randomness.
    slots = [
        _slot(start, agents=(seed + i) % 3, booked=(seed * 7 + i * 3) % 4)
        for i, start in enumerate(GRID)
    ]
    short = [s for s in slots if s.booked > s.agents]
    movers = [
        _booking(100 * n + k, start=s.start, made=NOW - k * HALF_HOUR)
        for n, s in enumerate(short)
        for k in range(s.booked - s.agents)
    ]

    proposals = propose(GROUP, movers, slots, REACH, NOW)

    assert len(proposals) == len(movers)
    taken = Counter(p.new_start for p in proposals if p.new_start is not None)
    room = {s.start: s.agents - s.booked for s in slots}
    assert all(count <= room[start] for start, count in taken.items())
    assert all(p.new_start != p.old_start for p in proposals)
    assert propose(GROUP, movers[::-1], slots[::-1], REACH, NOW) == proposals
