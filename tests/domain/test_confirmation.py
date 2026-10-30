from datetime import UTC, datetime, timedelta

import pytest

from app.domain.confirmation import ConfirmPolicy, ConfirmWindow, confirm_window
from app.domain.errors import DomainError, ErrorCode

NOW = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
LEAD = timedelta(minutes=5)
POLICY = ConfirmPolicy(
    window=timedelta(hours=24),
    cutoff=timedelta(hours=2),
    reminders=1,
    min_lead=timedelta(minutes=15),
)


def window(start: datetime, policy: ConfirmPolicy = POLICY) -> ConfirmWindow:
    return confirm_window(now=NOW, start=start, dispatch_lead=LEAD, policy=policy)


def test_far_ahead_the_window_after_booking_applies() -> None:
    result = window(NOW + timedelta(days=7))

    assert result.confirm_by == NOW + timedelta(hours=24)
    assert result.reminders == (NOW + timedelta(hours=12),)


def test_soon_the_cutoff_before_the_start_applies() -> None:
    start = NOW + timedelta(hours=10)

    result = window(start)

    assert result.confirm_by == start - timedelta(hours=2)
    assert result.reminders == (NOW + timedelta(hours=4),)


def test_reminders_are_spread_evenly() -> None:
    policy = ConfirmPolicy(
        window=timedelta(hours=24),
        cutoff=timedelta(hours=2),
        reminders=3,
        min_lead=timedelta(minutes=15),
    )

    result = window(NOW + timedelta(days=7), policy)

    assert result.reminders == (
        NOW + timedelta(hours=6),
        NOW + timedelta(hours=12),
        NOW + timedelta(hours=18),
    )


def test_no_reminders_when_the_policy_has_none() -> None:
    policy = ConfirmPolicy(
        window=timedelta(hours=24),
        cutoff=timedelta(hours=2),
        reminders=0,
        min_lead=timedelta(minutes=15),
    )

    assert window(NOW + timedelta(days=7), policy).reminders == ()


def test_reminders_too_close_to_the_deadline_are_dropped() -> None:
    # 40 minutes to confirm, three reminders at 10, 20 and 30 minutes: the last one lies only
    # 10 minutes before the deadline and is dropped.
    policy = ConfirmPolicy(
        window=timedelta(minutes=40),
        cutoff=timedelta(hours=2),
        reminders=3,
        min_lead=timedelta(minutes=15),
    )

    result = window(NOW + timedelta(days=7), policy)

    assert result.confirm_by == NOW + timedelta(minutes=40)
    assert result.reminders == (NOW + timedelta(minutes=10), NOW + timedelta(minutes=20))


def test_short_notice_confirms_until_the_change_deadline_without_reminder() -> None:
    start = NOW + timedelta(hours=2, minutes=5)  # cutoff lies 5 minutes ahead

    result = window(start)

    assert result.confirm_by == start - LEAD
    assert result.reminders == ()
    assert result.short_notice is True


def test_the_normal_window_is_not_short_notice() -> None:
    assert window(NOW + timedelta(days=7)).short_notice is False


def test_short_notice_past_the_change_deadline_cannot_be_booked() -> None:
    with pytest.raises(DomainError) as excinfo:
        window(NOW + LEAD)

    assert excinfo.value.code is ErrorCode.SLOT_UNAVAILABLE


def test_policy_refuses_values_outside_the_rules() -> None:
    with pytest.raises(ValueError, match="reminders"):
        ConfirmPolicy(
            window=timedelta(hours=1),
            cutoff=timedelta(0),
            reminders=-1,
            min_lead=timedelta(minutes=5),
        )
    with pytest.raises(ValueError, match="window"):
        ConfirmPolicy(
            window=timedelta(0),
            cutoff=timedelta(0),
            reminders=0,
            min_lead=timedelta(minutes=5),
        )
