from datetime import datetime, timedelta

import pytest

from app.domain.booking import BookingStatus
from app.domain.closure_retention import DEFAULT_CLOSURE_RETENTION as LIMITS
from app.domain.closure_retention import ClosureRetention
from app.domain.flow_resolution import FlowRef
from app.domain.immediate_intake import new_access
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
from app.domain.service_requests import ServiceRequest, on_demand_request
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
    assert purge_moment(candidate(status, end_ago=DAY), LIMITS) == NOW


def test_a_cancelled_booking_is_purged_a_day_after_the_cancellation() -> None:
    found = candidate(BookingStatus.CANCELLED, end_ago=-7 * DAY, cancelled_ago=DAY)

    assert purge_moment(found, LIMITS) == NOW


def test_a_suspended_booking_waits_for_the_end_of_its_closure() -> None:
    lifted = candidate(BookingStatus.SUSPENDED, closure_ended_ago=30 * DAY)

    assert purge_moment(lifted, LIMITS) == NOW


def test_a_suspended_booking_under_an_open_closure_goes_at_the_maximum() -> None:
    open_ended = candidate(BookingStatus.SUSPENDED, end_ago=60 * DAY)
    shorter = ClosureRetention.from_days(30, 3)

    assert purge_moment(open_ended, LIMITS) == NOW + 30 * DAY
    assert purge_moment(open_ended, shorter) == NOW - 30 * DAY
    assert not is_overdue(open_ended, NOW, LIMITS)
    assert is_overdue(open_ended, NOW, shorter)


def test_a_late_closure_end_does_not_keep_the_data_past_the_maximum() -> None:
    late = PurgeCandidate(
        1, BookingStatus.SUSPENDED, NOW - 100 * DAY, None, NOW + 200 * DAY, organisation_id=3
    )

    assert purge_moment(late, LIMITS) == NOW - 10 * DAY
    assert is_overdue(late, NOW, LIMITS)
    assert is_backlog(late, NOW, LIMITS)


def test_the_overview_uses_the_maximum_of_the_candidates_own_organisation() -> None:
    held = PurgeCandidate(1, BookingStatus.SUSPENDED, NOW - 60 * DAY, None, None, organisation_id=7)
    other = PurgeCandidate(
        2, BookingStatus.SUSPENDED, NOW - 60 * DAY, None, None, organisation_id=8
    )
    facts = RetentionFacts(
        [GroupRetentionCounts(DEMO, 1, 0, None), GroupRetentionCounts(OTHER, 1, 0, None)],
        [held, other],
    )

    def of(organisation_id: int) -> ClosureRetention:
        return ClosureRetention.from_days(30 if organisation_id == 7 else 90, 7)

    overview = summarise(facts, NOW, of)

    assert [g.overdue for g in overview.groups] == [1, 0]


def test_a_booking_awaiting_a_new_time_is_purged_a_day_after_the_request_lapses() -> None:
    waiting = PurgeCandidate(
        1, BookingStatus.AWAITING_REBOOK, NOW - 13 * DAY, None, None, rebook_by=NOW - DAY
    )

    assert purge_moment(waiting, LIMITS) == NOW


def test_a_refused_booking_is_purged_a_day_after_the_attempt() -> None:
    """ADR 0066 step 8: the refusal email goes at once; the slot's end does not matter."""
    refused = PurgeCandidate(
        1, BookingStatus.REFUSED, NOW + 14 * DAY, None, None, created_at=NOW - DAY
    )

    assert purge_moment(refused, LIMITS) == NOW


def test_a_purge_within_its_grace_is_not_overdue() -> None:
    waiting = candidate(end_ago=DAY + PURGE_GRACE)
    late = candidate(end_ago=DAY + PURGE_GRACE + timedelta(seconds=1))

    assert not is_overdue(waiting, NOW, LIMITS)
    assert is_overdue(late, NOW, LIMITS)


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

    overview = summarise(facts, NOW, lambda _org: LIMITS)

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

    overview = summarise(facts, NOW, lambda _org: LIMITS)

    assert overview.in_order
    assert overview.last_purged_at is None


def test_an_unconfirmed_booking_is_purged_a_day_after_its_confirm_deadline() -> None:
    # The same moment ``plan_for_pending`` plans (ADR 0056): before the slot, not after it.
    pending = PurgeCandidate(
        1, BookingStatus.PENDING_CONFIRMATION, NOW + 7 * DAY, None, None, confirm_by=NOW - DAY
    )

    assert purge_moment(pending, LIMITS) == NOW


def test_a_booking_is_backlog_once_it_held_data_two_days_after_it_started_to_count() -> None:
    old = candidate(end_ago=2 * DAY + DAY)  # ended 3 days ago: purge moment was 2 days ago
    fresh = candidate(end_ago=2 * DAY)  # ended 2 days ago: only 24 h overdue

    assert is_backlog(old, NOW, LIMITS)
    assert not is_backlog(fresh, NOW, LIMITS)
    assert not is_backlog(candidate(BookingStatus.SUSPENDED, end_ago=50 * DAY), NOW, LIMITS)


def test_request_level_records_are_counted_on_their_own() -> None:
    """ADR 0121 step 7, follow-up 5d: held and overdue records per service, never bookings."""
    facts = RetentionFacts(
        [GroupRetentionCounts(DEMO, 0, 0, None), GroupRetentionCounts(OTHER, 0, 0, None)], []
    )
    records = [
        record(1, deleted_at=NOW + DAY),  # still within its time
        record(1, deleted_at=NOW - PURGE_GRACE - timedelta(minutes=1)),  # past moment and grace
        record(2, deleted_at=NOW - PURGE_GRACE + timedelta(minutes=1)),  # within the grace
        record(9, deleted_at=NOW - 9 * DAY),  # a service outside the overview's scope
    ]

    overview = summarise(facts, NOW, lambda _org: LIMITS, records)

    rows = [(g.requests_holding, g.requests_overdue, g.overdue) for g in overview.groups]
    assert rows == [(2, 1, 0), (1, 0, 0)]
    assert overview.requests_overdue == 1
    assert overview.groups[0].needs_attention
    assert not overview.in_order


def record(group_id: int, *, deleted_at: datetime) -> ServiceRequest:
    """Return immediate work of ``group_id`` whose data goes at ``deleted_at``."""
    access, _ = new_access("k" * 43)
    return on_demand_request(
        organisation_id=1,
        group_id=group_id,
        flow=FlowRef(1, 1),
        pool_id=1,
        subject=None,
        access=access,
        now=deleted_at - 2 * DAY,
        unresolved_hours=24,
    )
