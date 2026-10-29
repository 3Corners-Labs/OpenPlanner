"""Dispatch kind per group and its change deadline (ADR 0043)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.errors import DomainError
from app.domain.scheduling import DispatchKind
from tests.support.domain import make_group

WXCC = {"wxcc_business_address": "aangifte@example.org", "wxcc_channel": "video"}
QUEUE = DispatchKind.OPENPLANNER_QUEUE


def test_existing_groups_keep_the_contact_center() -> None:
    group = make_group()

    assert group.dispatch_kind is DispatchKind.CONTACT_CENTER
    assert group.queue_change_working_days == 1


def test_only_contact_center_groups_send_work_items() -> None:
    assert make_group(**WXCC).dispatch_enabled
    assert not make_group(dispatch_kind=QUEUE, **WXCC).dispatch_enabled


def test_contact_center_deadline_is_the_dispatch_moment() -> None:
    start = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
    group = make_group(dispatch_lead_minutes=15)

    assert group.change_deadline(start) == start - timedelta(minutes=15)


@pytest.mark.parametrize(
    ("start", "days", "deadline"),
    [
        # Wednesday 10:00 Amsterdam (09:00 UTC) → Tuesday, same local time
        (datetime(2026, 11, 4, 9, 0, tzinfo=UTC), 1, datetime(2026, 11, 3, 9, 0, tzinfo=UTC)),
        # Monday → the Friday before, skipping the weekend
        (datetime(2026, 11, 2, 9, 0, tzinfo=UTC), 1, datetime(2026, 10, 30, 9, 0, tzinfo=UTC)),
        # Sunday → Friday
        (datetime(2026, 11, 1, 9, 0, tzinfo=UTC), 1, datetime(2026, 10, 30, 9, 0, tzinfo=UTC)),
        # Tuesday, two working days → the Friday before
        (datetime(2026, 11, 3, 9, 0, tzinfo=UTC), 2, datetime(2026, 10, 30, 9, 0, tzinfo=UTC)),
        # Monday after the DST change (25 Oct) → Friday before it: same local 10:00
        (datetime(2026, 10, 26, 9, 0, tzinfo=UTC), 1, datetime(2026, 10, 23, 8, 0, tzinfo=UTC)),
    ],
)
def test_own_queue_deadline_is_working_days_before_at_the_same_local_time(
    start: datetime, days: int, deadline: datetime
) -> None:
    group = make_group(dispatch_kind=QUEUE, queue_change_working_days=days)

    assert group.change_deadline(start) == deadline


@pytest.mark.parametrize("days", [0, 6])
def test_working_days_are_bounded(days: int) -> None:
    with pytest.raises(DomainError) as excinfo:
        make_group(queue_change_working_days=days)

    assert excinfo.value.field == "queue_change_working_days"


@pytest.mark.parametrize("days", [1, 5])
def test_working_days_accept_the_bounds(days: int) -> None:
    make_group(queue_change_working_days=days)
