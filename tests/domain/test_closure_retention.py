from datetime import UTC, datetime, timedelta

from app.domain.closure_retention import (
    DEFAULT_CLOSURE_RETENTION,
    RETENTION_AFTER_CLOSURE,
    ClosureRetention,
    default_closure_retention,
    is_capped,
    maximum_moment,
    suspended_purge_moment,
)

DAY = timedelta(days=1)
SLOT_END = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
LIMITS = ClosureRetention.from_days(60, 7)


def test_the_defaults_are_those_of_the_adr() -> None:
    assert DEFAULT_CLOSURE_RETENTION.max_retention == 90 * DAY
    assert DEFAULT_CLOSURE_RETENTION.notice == 14 * DAY
    assert default_closure_retention(42) is DEFAULT_CLOSURE_RETENTION


def test_an_open_closure_purges_at_the_maximum_after_the_original_slot() -> None:
    assert suspended_purge_moment(SLOT_END, None, LIMITS) == SLOT_END + 60 * DAY
    assert maximum_moment(SLOT_END, LIMITS) == SLOT_END + 60 * DAY


def test_a_closure_that_ends_soon_purges_thirty_days_after_its_end() -> None:
    ends = SLOT_END + 5 * DAY

    assert suspended_purge_moment(SLOT_END, ends, LIMITS) == ends + RETENTION_AFTER_CLOSURE


def test_a_closure_that_ends_late_is_cut_short_by_the_maximum() -> None:
    ends = SLOT_END + 200 * DAY

    assert suspended_purge_moment(SLOT_END, ends, LIMITS) == SLOT_END + 60 * DAY


def test_the_maximum_decides_exactly_when_it_comes_first() -> None:
    meeting = SLOT_END + 30 * DAY  # end + 30 d == slot end + 60 d

    assert not is_capped(SLOT_END, meeting, LIMITS)
    assert is_capped(SLOT_END, meeting + timedelta(seconds=1), LIMITS)
    assert is_capped(SLOT_END, None, LIMITS)
    assert not is_capped(SLOT_END, SLOT_END, LIMITS)


def test_a_later_slot_gets_a_later_maximum_so_the_term_counts_from_the_appointment() -> None:
    later = SLOT_END + 40 * DAY

    assert suspended_purge_moment(later, None, LIMITS) == later + 60 * DAY
