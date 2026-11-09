"""When a digest is due and what it covers (ADR 0117 point 16)."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.domain.report_digest import (
    DigestPeriod,
    Subscription,
    due_at,
    is_late,
    latest_due,
    runs_on,
    window_of,
)

AMS = ZoneInfo("Europe/Amsterdam")
MADE = datetime(2026, 11, 1, tzinfo=UTC)


def _sub(period: DigestPeriod, weekday: int = 0, month_day: int = 1) -> Subscription:
    return Subscription(1, 1, "demo", None, None, period, weekday, month_day, (3,), "sub", MADE)


def test_a_daily_digest_is_due_every_day_from_the_send_hour() -> None:
    sub = _sub(DigestPeriod.DAILY)
    before = datetime(2026, 11, 9, 5, 30, tzinfo=UTC)  # 06:30 local
    after = datetime(2026, 11, 9, 6, 30, tzinfo=UTC)  # 07:30 local

    assert latest_due(sub, before, AMS, 7) == date(2026, 11, 8)
    assert latest_due(sub, after, AMS, 7) == date(2026, 11, 9)


def test_a_weekly_digest_is_due_on_its_weekday_and_then_stays_the_latest_one() -> None:
    sub = _sub(DigestPeriod.WEEKLY, weekday=0)  # Monday
    tuesday = datetime(2026, 11, 10, 12, tzinfo=UTC)

    assert latest_due(sub, tuesday, AMS, 7) == date(2026, 11, 9)


def test_a_monthly_digest_is_due_on_its_day() -> None:
    sub = _sub(DigestPeriod.MONTHLY, month_day=15)
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)

    assert latest_due(sub, now, AMS, 7) == date(2026, 11, 15)
    assert runs_on(sub, date(2026, 12, 15))


def test_the_windows_end_yesterday_and_start_a_period_earlier() -> None:
    day = date(2026, 11, 9)

    assert window_of(_sub(DigestPeriod.DAILY), day) == (date(2026, 11, 8), date(2026, 11, 8))
    assert window_of(_sub(DigestPeriod.WEEKLY), day) == (date(2026, 11, 2), date(2026, 11, 8))
    assert window_of(_sub(DigestPeriod.MONTHLY, month_day=9), day) == (
        date(2026, 10, 9),
        date(2026, 11, 8),
    )
    assert window_of(_sub(DigestPeriod.MONTHLY, month_day=5), date(2027, 1, 5)) == (
        date(2026, 12, 5),
        date(2027, 1, 4),
    )


def test_a_digest_is_late_only_after_the_allowed_hours() -> None:
    day = date(2026, 11, 9)  # due 07:00 local = 06:00 UTC
    on_time = datetime(2026, 11, 10, 5, tzinfo=UTC)
    late = datetime(2026, 11, 10, 7, tzinfo=UTC)

    assert due_at(day, AMS, 7).astimezone(UTC).hour == 6
    assert not is_late(day, on_time, AMS, 7, 24)
    assert is_late(day, late, AMS, 7, 24)


@pytest.mark.parametrize(("weekday", "month_day"), [(7, 1), (-1, 1), (0, 0), (0, 29)])
def test_a_weekday_or_day_that_does_not_exist_is_refused(weekday: int, month_day: int) -> None:
    with pytest.raises(ValueError, match="out of range"):
        _sub(DigestPeriod.WEEKLY, weekday, month_day)
