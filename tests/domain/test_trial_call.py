"""The trial call rules (ADR 0070)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.booking import BookingStatus, MeetingRef, TrialCall
from app.domain.errors import DomainError, ErrorCode
from app.domain.trial_call import TrialState, trial_request, trial_state
from tests.support.closures import confirmed_booking
from tests.support.domain import NOW, SLOT_0900, make_group

HOST = {"video_host_email": "host@example.org"}


def test_a_confirmed_booking_can_open_a_trial_call() -> None:
    state = trial_state(confirmed_booking(), make_group(**HOST), NOW)

    assert state is TrialState.AVAILABLE


@pytest.mark.parametrize(
    ("group", "expected"),
    [
        ({"trial_call_enabled": False}, TrialState.OFF),
        ({"video_host_email": None}, TrialState.OFF),
    ],
)
def test_a_group_without_video_or_with_the_switch_off_offers_none(
    group: dict[str, object], expected: TrialState
) -> None:
    fields = {**HOST, **group}

    assert trial_state(confirmed_booking(), make_group(**fields), NOW) is expected


@pytest.mark.parametrize(
    "status", [BookingStatus.CANCELLED, BookingStatus.SUSPENDED, BookingStatus.PENDING_CONFIRMATION]
)
def test_only_a_confirmed_booking_has_a_trial_call(status: BookingStatus) -> None:
    booking = replace(confirmed_booking(), status=status)

    assert trial_state(booking, make_group(**HOST), NOW) is TrialState.INACTIVE


def test_the_trial_call_ends_where_the_appointment_starts() -> None:
    state = trial_state(confirmed_booking(), make_group(**HOST), SLOT_0900)

    assert state is TrialState.TOO_LATE


def test_an_open_trial_call_is_reused_until_it_closes() -> None:
    trial = TrialCall(MeetingRef("m1", "https://site.webex.com/m/1"), NOW + timedelta(minutes=30))
    booking = replace(confirmed_booking(), trial=trial, trial_count=1)
    group = make_group(**HOST)

    assert trial_state(booking, group, NOW + timedelta(minutes=29)) is TrialState.ACTIVE
    assert trial_state(booking, group, NOW + timedelta(minutes=30)) is TrialState.AVAILABLE


def test_the_number_of_trial_calls_is_limited_per_booking() -> None:
    booking = replace(confirmed_booking(), trial_count=3)

    assert trial_state(booking, make_group(**HOST), NOW) is TrialState.LIMIT_REACHED
    assert trial_state(booking, make_group(trial_calls_per_booking=4, **HOST), NOW) is (
        TrialState.AVAILABLE
    )


def test_the_trial_meeting_is_the_hosts_own_with_entry_before_the_host() -> None:
    group = make_group(name="Aangifte", trial_call_minutes=20, **HOST)
    now = NOW + timedelta(seconds=42)

    request = trial_request(group, now)

    assert request.title == "Proefgesprek Aangifte"
    assert request.host_email == "host@example.org"
    assert request.start == NOW
    assert request.end == NOW + timedelta(minutes=20)
    assert request.join_before_host is True
    assert request.timezone == group.timezone


def test_a_trial_request_needs_the_groups_host() -> None:
    with pytest.raises(DomainError) as excinfo:
        trial_request(make_group(), NOW)

    assert excinfo.value.code is ErrorCode.INTEGRATION_NOT_READY


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trial_call_minutes", 9),
        ("trial_call_minutes", 61),
        ("trial_calls_per_booking", 0),
        ("trial_calls_per_booking", 6),
    ],
)
def test_trial_settings_are_bounded(field: str, value: int) -> None:
    with pytest.raises(DomainError) as excinfo:
        make_group(**{field: value})

    assert excinfo.value.field == field


def test_the_trial_call_is_on_by_default() -> None:
    group = make_group()

    assert group.trial_call_enabled
    assert (group.trial_call_minutes, group.trial_calls_per_booking) == (30, 3)
