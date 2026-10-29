from datetime import UTC, datetime, timedelta

from app.domain.agenda import AgendaSlot, agenda_day, agents_for, first_shortfall
from app.domain.closure import Closure
from tests.support.domain import MONDAY, NOW, SLOT_0900, SLOT_0930, make_group, window

GROUP = make_group()
HALF_HOUR = timedelta(minutes=30)


def _slot(start: datetime, agents: int, booked: int, *, closed: bool = False) -> AgendaSlot:
    return AgendaSlot(start, start + HALF_HOUR, agents, booked, closed)


def test_a_slot_with_more_bookings_than_agents_is_a_shortfall() -> None:
    slots = agenda_day(GROUP, MONDAY, [window(capacity=2)], {SLOT_0900: 3}, [])

    assert slots == [_slot(SLOT_0900, 2, 3), _slot(SLOT_0930, 2, 0)]
    assert [s.shortfall for s in slots] == [1, 0]


def test_a_full_slot_is_no_shortfall() -> None:
    (first, _) = agenda_day(GROUP, MONDAY, [window(capacity=2)], {SLOT_0900: 2}, [])

    assert first.shortfall == 0


def test_bookings_outside_the_staffing_need_agents_that_are_not_there() -> None:
    eleven = SLOT_0900 + timedelta(hours=2)

    slots = agenda_day(GROUP, MONDAY, [window(capacity=2)], {eleven: 1}, [])

    assert slots[-1] == _slot(eleven, 0, 1)
    assert slots[-1].shortfall == 1


def test_a_slot_across_two_blocks_gets_the_lower_count() -> None:
    blocks = [window("09:00", "09:15", 3), window("09:15", "10:00", 1)]

    assert agents_for(GROUP, SLOT_0900, SLOT_0900 + HALF_HOUR, MONDAY, blocks) == 1


def test_a_block_with_no_agents_or_a_gap_means_nobody() -> None:
    gap = [window("09:00", "09:15", 3), window("09:20", "10:00", 3)]
    empty = [window("09:00", "10:00", 0)]

    assert agents_for(GROUP, SLOT_0900, SLOT_0900 + HALF_HOUR, MONDAY, gap) == 0
    assert agents_for(GROUP, SLOT_0900, SLOT_0900 + HALF_HOUR, MONDAY, empty) == 0


def test_a_closed_slot_has_no_agents() -> None:
    closure = Closure(1, 1, SLOT_0900, None, {"nl": "Dicht", "en": "Closed"}, NOW)

    slots = agenda_day(GROUP, MONDAY, [window(capacity=2)], {}, [closure])

    assert slots == [_slot(SLOT_0900, 0, 0, closed=True), _slot(SLOT_0930, 0, 0, closed=True)]


def test_first_shortfall_is_the_earliest_one_from_now() -> None:
    past = _slot(SLOT_0900 - timedelta(days=1), 0, 2)
    later = _slot(SLOT_0930, 1, 2)
    earlier = _slot(SLOT_0900, 1, 3)

    assert first_shortfall([past, later, earlier], SLOT_0900 - HALF_HOUR) == earlier
    assert first_shortfall([_slot(SLOT_0900, 2, 2)], NOW) is None


def test_slot_times_are_utc() -> None:
    (first, _) = agenda_day(GROUP, MONDAY, [window()], {}, [])

    assert first.start.tzinfo is UTC
