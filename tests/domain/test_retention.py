from datetime import timedelta

import pytest

from app.domain.booking import BookingStatus
from app.domain.retention import (
    PURGE_GRACE,
    GroupRetentionCounts,
    PurgeCandidate,
    RetentionFacts,
    candidates_before,
    is_backlog,
    is_overdue,
    purge_moment,
    summarise,
)
from app.domain.scheduling import GroupSummary
from tests.support.domain import NOW, make_group

DAY = timedelta(hours=24)
DEMO = GroupSummary(make_group(id=1, slug="demo"), "demo", "Demo")
OTHER = GroupSummary(make_group(id=2, slug="other"), "demo", "Demo")


def candidate(
    status: BookingStatus = BookingStatus.CONFIRMED,
    *,
    end_ago: timedelta = 2 * DAY,
    cancelled_ago: timedelta | None = None,
    closure_ended_ago: timedelta | None = None,
    group_id: int = 1,
) -> PurgeCandidate:
    return PurgeCandidate(
        group_id,
        status,
        NOW - end_ago,
        NOW - cancelled_ago if cancelled_ago is not None else None,
        NOW - closure_ended_ago if closure_ended_ago is not None else None,
    )


@pytest.mark.parametrize(
    "status", [BookingStatus.CONFIRMED, BookingStatus.COMPLETED, BookingStatus.NO_SHOW]
)
def test_an_ended_booking_is_purged_a_day_after_its_slot(status: BookingStatus) -> None:
    assert purge_moment(candidate(status, end_ago=DAY)) == NOW


def test_a_cancelled_booking_is_purged_a_day_after_the_cancellation() -> None:
    found = candidate(BookingStatus.CANCELLED, end_ago=-7 * DAY, cancelled_ago=DAY)

    assert purge_moment(found) == NOW


def test_a_suspended_booking_waits_for_the_end_of_its_closure() -> None:
    open_ended = candidate(BookingStatus.SUSPENDED, end_ago=60 * DAY)
    lifted = candidate(BookingStatus.SUSPENDED, closure_ended_ago=30 * DAY)

    assert purge_moment(open_ended) is None
    assert purge_moment(lifted) == NOW


def test_a_booking_awaiting_a_new_time_is_purged_a_day_after_the_request_lapses() -> None:
    waiting = PurgeCandidate(
        1, BookingStatus.AWAITING_REBOOK, NOW - 13 * DAY, None, None, rebook_by=NOW - DAY
    )

    assert purge_moment(waiting) == NOW


def test_a_purge_within_its_grace_is_not_overdue() -> None:
    waiting = candidate(end_ago=DAY + PURGE_GRACE)
    late = candidate(end_ago=DAY + PURGE_GRACE + timedelta(seconds=1))

    assert not is_overdue(waiting, NOW)
    assert is_overdue(late, NOW)
    assert not is_overdue(candidate(BookingStatus.SUSPENDED, end_ago=60 * DAY), NOW)


def test_candidates_start_where_the_earliest_purge_plus_grace_lies() -> None:
    assert candidates_before(NOW) == NOW - DAY - PURGE_GRACE


def test_the_overview_counts_per_group_and_in_total() -> None:
    last = NOW - timedelta(hours=3)
    facts = RetentionFacts(
        groups=[
            GroupRetentionCounts(DEMO, holding=5, failed_purges=1, last_purged_at=last),
            GroupRetentionCounts(OTHER, holding=2, failed_purges=0, last_purged_at=None),
        ],
        candidates=[
            candidate(end_ago=3 * DAY),
            candidate(end_ago=3 * DAY),
            candidate(BookingStatus.SUSPENDED, end_ago=60 * DAY),
            candidate(end_ago=DAY, group_id=2),
        ],
    )

    overview = summarise(facts, NOW)

    rows = [(g.group.group.slug, g.holding, g.overdue, g.failed_purges) for g in overview.groups]
    assert rows == [
        ("demo", 5, 2, 1),
        ("other", 2, 0, 0),
    ]
    assert overview.checked_at == NOW
    assert overview.overdue == 2
    assert overview.failed_purges == 1
    assert overview.last_purged_at == last
    assert not overview.in_order
    assert overview.groups[0].needs_attention
    assert not overview.groups[1].needs_attention


def test_an_overview_without_problems_is_in_order() -> None:
    facts = RetentionFacts([GroupRetentionCounts(DEMO, 3, 0, None)], [candidate(end_ago=DAY)])

    overview = summarise(facts, NOW)

    assert overview.in_order
    assert overview.last_purged_at is None


def test_an_unconfirmed_booking_is_purged_a_day_after_its_confirm_deadline() -> None:
    # The same moment ``plan_for_pending`` plans (ADR 0056): before the slot, not after it.
    pending = PurgeCandidate(
        1, BookingStatus.PENDING_CONFIRMATION, NOW + 7 * DAY, None, None, confirm_by=NOW - DAY
    )

    assert purge_moment(pending) == NOW


def test_a_booking_is_backlog_once_it_held_data_two_days_after_it_started_to_count() -> None:
    old = candidate(end_ago=2 * DAY + DAY)  # ended 3 days ago: purge moment was 2 days ago
    fresh = candidate(end_ago=2 * DAY)  # ended 2 days ago: only 24 h overdue

    assert is_backlog(old, NOW)
    assert not is_backlog(fresh, NOW)
    assert not is_backlog(candidate(BookingStatus.SUSPENDED, end_ago=90 * DAY), NOW)
