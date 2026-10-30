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
    assert group.citizen_change_lead_minutes == 60


def test_only_contact_center_groups_send_work_items() -> None:
    assert make_group(**WXCC).dispatch_enabled
    assert not make_group(dispatch_kind=QUEUE, **WXCC).dispatch_enabled


@pytest.mark.parametrize("kind", list(DispatchKind))
def test_citizen_moves_until_the_change_lead_for_both_kinds(kind: DispatchKind) -> None:
    start = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
    group = make_group(dispatch_kind=kind, citizen_change_lead_minutes=90)

    assert group.change_deadline(start) == start - timedelta(minutes=90)


@pytest.mark.parametrize("kind", list(DispatchKind))
def test_citizen_cancels_until_the_start_for_both_kinds(kind: DispatchKind) -> None:
    start = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)

    assert make_group(dispatch_kind=kind).cancel_deadline(start) == start


def test_a_monday_booking_is_not_locked_since_friday() -> None:
    """The case behind ADR 0068: own queue, Monday 09:30, two days before."""
    start = datetime(2026, 11, 2, 8, 30, tzinfo=UTC)  # Monday 09:30 Amsterdam
    group = make_group(dispatch_kind=QUEUE)
    sunday_noon = datetime(2026, 11, 1, 11, 0, tzinfo=UTC)

    assert sunday_noon < group.change_deadline(start) < start


@pytest.mark.parametrize("minutes", [-1, 1441])
def test_change_lead_is_bounded(minutes: int) -> None:
    with pytest.raises(DomainError) as excinfo:
        make_group(citizen_change_lead_minutes=minutes)

    assert excinfo.value.field == "citizen_change_lead_minutes"


@pytest.mark.parametrize("minutes", [0, 1440])
def test_change_lead_accepts_the_bounds(minutes: int) -> None:
    make_group(citizen_change_lead_minutes=minutes, dispatch_lead_minutes=0)


def test_change_lead_is_never_shorter_than_the_dispatch_lead() -> None:
    with pytest.raises(DomainError) as excinfo:
        make_group(dispatch_lead_minutes=15, min_notice_minutes=60, citizen_change_lead_minutes=10)

    assert excinfo.value.field == "citizen_change_lead_minutes"


def test_only_an_own_queue_group_with_an_address_sends_the_team_invite() -> None:
    address = {"calendar_invite_address": "team-aangifte@example.org"}

    assert make_group(dispatch_kind=QUEUE, **address).sends_team_invite
    assert not make_group(**address).sends_team_invite
    assert not make_group(dispatch_kind=QUEUE).sends_team_invite


@pytest.mark.parametrize("address", ["no-at-sign", "a@b", "x" * 250 + "@example.org", " "])
def test_the_invite_address_must_be_an_email_address(address: str) -> None:
    with pytest.raises(DomainError) as excinfo:
        make_group(calendar_invite_address=address)

    assert excinfo.value.field == "calendar_invite_address"
