from datetime import UTC, datetime, timedelta

import pytest

from app.domain.booking import BookingStatus
from app.domain.channels import ChannelKind
from app.domain.closure import (
    MAX_NOTICE_LENGTH,
    RETENTION_AFTER_CLOSURE,
    Closure,
    clean_notice,
    covering,
)
from app.domain.errors import DomainError
from app.domain.job_plan import (
    JobType,
    PlannedJob,
    is_stale,
    plan_closure_update,
    plan_for_suspension,
    plan_purge,
)
from tests.support.closures import NOTICES, suspended_booking
from tests.support.domain import NOW

START = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
END = START + timedelta(days=2)


def closure(ends_at: datetime | None = END, **notices: str) -> Closure:
    return Closure(1, 1, START, ends_at, notices or NOTICES, NOW)


def test_closure_covers_start_times_from_its_start_until_its_end() -> None:
    c = closure()

    assert c.covers(START)
    assert c.covers(END - timedelta(minutes=30))
    assert not c.covers(END)
    assert not c.covers(START - timedelta(minutes=30))


def test_open_ended_closure_covers_everything_after_its_start() -> None:
    c = closure(ends_at=None)

    assert c.covers(START + timedelta(days=365))
    assert c.is_visible(START + timedelta(days=365))
    assert c.purge_at is None


def test_visibility_and_activity() -> None:
    c = closure()

    assert c.is_visible(NOW)
    assert not c.is_active(NOW)
    assert c.is_visible(START)
    assert c.is_active(START)
    assert not c.is_visible(END)
    assert not c.is_active(END)


def test_purge_follows_the_end_by_the_retention_period() -> None:
    assert closure().purge_at == END + RETENTION_AFTER_CLOSURE
    assert timedelta(days=30) == RETENTION_AFTER_CLOSURE


def test_notice_falls_back_to_dutch() -> None:
    c = closure()

    assert c.notice("en") == NOTICES["en"]
    assert c.notice("de") == NOTICES["nl"]


@pytest.mark.parametrize(
    ("starts", "ends", "field"),
    [
        (datetime(2026, 1, 5, 8, 0), None, "starts_at"),  # noqa: DTZ001 - naive on purpose
        (START, START - timedelta(minutes=1), "ends_at"),
        (START, datetime(2026, 1, 6, 8, 0), "ends_at"),  # noqa: DTZ001 - naive on purpose
    ],
)
def test_invalid_window_names_the_field(
    starts: datetime, ends: datetime | None, field: str
) -> None:
    with pytest.raises(DomainError, match=field):
        Closure(None, 1, starts, ends, NOTICES, NOW)


def test_both_notices_are_required() -> None:
    with pytest.raises(DomainError, match="notice_en"):
        Closure(None, 1, START, END, {"nl": "Gesloten"}, NOW)


@pytest.mark.parametrize(
    "raw", ["", "   ", "x" * (MAX_NOTICE_LENGTH + 1), "bad\x00text", "bell\x07"]
)
def test_invalid_notice_texts_are_refused(raw: str) -> None:
    with pytest.raises(DomainError, match="notice_nl"):
        clean_notice(raw, "notice_nl")


def test_notice_keeps_line_breaks_and_trims() -> None:
    assert clean_notice("  Regel 1\r\nRegel 2\rRegel 3\t \n", "notice_nl") == (
        "Regel 1\nRegel 2\nRegel 3"
    )


def test_covering_returns_the_closure_of_a_start() -> None:
    c = closure()

    assert covering([c], START) is c
    assert covering([c], END) is None
    assert covering([], START) is None


# --- bookings on hold --------------------------------------------------------------------


def test_suspend_reinstate_and_cancel() -> None:
    booking = suspended_booking()

    assert booking.status is BookingStatus.SUSPENDED
    assert booking.holds_place
    assert not booking.is_active
    assert booking.reinstate().status is BookingStatus.CONFIRMED
    cancelled = booking.cancel(NOW)
    assert cancelled.status is BookingStatus.CANCELLED
    assert not cancelled.holds_place
    with pytest.raises(DomainError, match="booking_not_active"):
        booking.suspend(2)
    with pytest.raises(DomainError, match="booking_not_active"):
        booking.reinstate().reinstate()
    with pytest.raises(DomainError, match="booking_not_active"):
        cancelled.cancel(NOW)


def test_suspension_plan_mails_now_and_purges_after_the_closure() -> None:
    purge_at = END + RETENTION_AFTER_CLOSURE

    assert plan_for_suspension(now=NOW, purge_at=purge_at) == [
        PlannedJob(JobType.END_WXCC_TASK, NOW, None),
        PlannedJob(JobType.DELETE_TRIAL_CALL, NOW, None),
        PlannedJob(JobType.SEND_SUSPENSION, NOW, None, ChannelKind.EMAIL),
        PlannedJob(JobType.PURGE_PII, purge_at, None),
    ]
    assert [j.type for j in plan_for_suspension(now=NOW, purge_at=None)] == [
        JobType.END_WXCC_TASK,
        JobType.DELETE_TRIAL_CALL,
        JobType.SEND_SUSPENSION,
    ]
    assert plan_purge(None) == []
    assert plan_closure_update(now=NOW) == [
        PlannedJob(JobType.SEND_CLOSURE_UPDATE, NOW, None, ChannelKind.EMAIL)
    ]


def test_closure_notices_only_go_to_bookings_still_on_hold() -> None:
    on_hold = suspended_booking()
    purged = suspended_booking(contact=None)

    for job_type in (JobType.SEND_SUSPENSION, JobType.SEND_CLOSURE_UPDATE):
        assert not is_stale(None, on_hold, job_type)
        assert is_stale(None, on_hold.reinstate(), job_type)
        assert is_stale(None, on_hold.cancel(NOW), job_type)
        assert is_stale(None, purged, job_type)


def test_pinned_jobs_of_a_suspended_booking_are_stale() -> None:
    booking = suspended_booking()

    assert is_stale(booking.start, booking, JobType.SEND_REMINDER)
    assert is_stale(booking.start, booking)
    assert not is_stale(None, booking, JobType.PURGE_PII)
