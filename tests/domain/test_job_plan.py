from datetime import UTC, datetime, timedelta

from app.domain.booking import Contact
from app.domain.channels import ChannelKind, ReminderRule, reachable
from app.domain.job_plan import JobType, PlannedJob, plan_for_booking, plan_for_cancellation
from app.domain.scheduling import PhoneMode

LEAD = timedelta(minutes=5)

NOW = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
START = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
END = START + timedelta(minutes=30)
WITH_PHONE = Contact.create(
    name="Ada", email="ada@example.org", phone="0612345678", phone_mode=PhoneMode.OPTIONAL
)
NO_PHONE = Contact.create(
    name="Ada", email="ada@example.org", phone=None, phone_mode=PhoneMode.OPTIONAL
)


def plan(**kwargs: object) -> list[PlannedJob]:
    params: dict[str, object] = {
        "start": START,
        "end": END,
        "contact": WITH_PHONE,
        "consents": frozenset(),
        "rules": [],
        "now": NOW,
        "dispatch_lead": LEAD,
    }
    params.update(kwargs)
    return plan_for_booking(**params)  # type: ignore[arg-type]


def test_base_plan() -> None:
    assert plan() == [
        PlannedJob(JobType.CREATE_VIDEO_ROOM, NOW, START),
        PlannedJob(JobType.SEND_CONFIRMATION, NOW, START, ChannelKind.EMAIL),
        PlannedJob(JobType.DISPATCH_WXCC_TASK, START - timedelta(minutes=5), START),
        PlannedJob(JobType.PURGE_PII, END + timedelta(hours=24), None),
    ]


def test_rescheduled_booking_gets_a_change_message() -> None:
    types = [job.type for job in plan(message=JobType.SEND_CHANGE)]

    assert JobType.SEND_CHANGE in types
    assert JobType.SEND_CONFIRMATION not in types


def test_reminders_follow_the_rules_in_time_order() -> None:
    rules = [
        ReminderRule(ChannelKind.SMS, minutes_before=120),
        ReminderRule(ChannelKind.EMAIL, minutes_before=24 * 60),
    ]

    reminders = [job for job in plan(rules=rules) if job.type is JobType.SEND_REMINDER]

    assert reminders == [
        PlannedJob(JobType.SEND_REMINDER, START - timedelta(hours=24), START, ChannelKind.EMAIL),
        PlannedJob(JobType.SEND_REMINDER, START - timedelta(hours=2), START, ChannelKind.SMS),
    ]


def test_reminders_skip_disabled_duplicate_past_and_unreachable() -> None:
    rules = [
        ReminderRule(ChannelKind.EMAIL, minutes_before=60, enabled=False),
        ReminderRule(ChannelKind.EMAIL, minutes_before=24 * 60),
        ReminderRule(ChannelKind.EMAIL, minutes_before=24 * 60),
        ReminderRule(ChannelKind.EMAIL, minutes_before=7 * 24 * 60),  # before now
        ReminderRule(ChannelKind.SMS, minutes_before=120),  # no phone
        ReminderRule(ChannelKind.WHATSAPP, minutes_before=60),  # no consent
    ]

    reminders = [j for j in plan(rules=rules, contact=NO_PHONE) if j.type is JobType.SEND_REMINDER]

    assert reminders == [
        PlannedJob(JobType.SEND_REMINDER, START - timedelta(hours=24), START, ChannelKind.EMAIL)
    ]


def test_reminder_exactly_at_now_is_skipped() -> None:
    rules = [
        ReminderRule(ChannelKind.EMAIL, minutes_before=int((START - NOW).total_seconds() // 60))
    ]

    assert not [j for j in plan(rules=rules) if j.type is JobType.SEND_REMINDER]


def test_dispatch_follows_the_groups_lead() -> None:
    dispatch = [
        j for j in plan(dispatch_lead=timedelta(minutes=15)) if j.type is JobType.DISPATCH_WXCC_TASK
    ]

    assert dispatch == [
        PlannedJob(JobType.DISPATCH_WXCC_TASK, START - timedelta(minutes=15), START)
    ]


def test_dispatch_is_not_planned_in_the_past() -> None:
    late_now = START - timedelta(minutes=2)

    dispatch = [j for j in plan(now=late_now) if j.type is JobType.DISPATCH_WXCC_TASK]

    assert dispatch == [PlannedJob(JobType.DISPATCH_WXCC_TASK, late_now, START)]


def test_cancellation_plan() -> None:
    assert plan_for_cancellation(cancelled_at=NOW) == [
        PlannedJob(JobType.END_WXCC_TASK, NOW, None),
        PlannedJob(JobType.DELETE_VIDEO_ROOM, NOW, None),
        PlannedJob(JobType.SEND_CANCELLATION, NOW, None, ChannelKind.EMAIL),
        PlannedJob(JobType.PURGE_PII, NOW + timedelta(hours=24), None),
    ]


def test_reschedule_cancellation_plan_sends_nothing() -> None:
    assert plan_for_cancellation(cancelled_at=NOW, notify=False) == [
        PlannedJob(JobType.END_WXCC_TASK, NOW, None),
        PlannedJob(JobType.DELETE_VIDEO_ROOM, NOW, None),
        PlannedJob(JobType.PURGE_PII, NOW + timedelta(hours=24), None),
    ]


def test_reachability_per_channel() -> None:
    assert reachable(ChannelKind.EMAIL, NO_PHONE, frozenset())
    assert not reachable(ChannelKind.SMS, NO_PHONE, frozenset())
    assert reachable(ChannelKind.SMS, WITH_PHONE, frozenset())
    assert not reachable(ChannelKind.WHATSAPP, WITH_PHONE, frozenset())
    assert reachable(ChannelKind.WHATSAPP, WITH_PHONE, frozenset({ChannelKind.WHATSAPP}))
    assert not reachable(ChannelKind.WHATSAPP, NO_PHONE, frozenset({ChannelKind.WHATSAPP}))


def test_reminder_rule_bounds() -> None:
    import pytest

    from app.domain.errors import DomainError

    for minutes in (0, 4, 30 * 24 * 60 + 1):
        with pytest.raises(DomainError):
            ReminderRule(ChannelKind.EMAIL, minutes_before=minutes)
    ReminderRule(ChannelKind.EMAIL, minutes_before=5)
    ReminderRule(ChannelKind.EMAIL, minutes_before=30 * 24 * 60)


def test_stale_jobs() -> None:
    from dataclasses import replace

    from app.domain.booking import Booking, BookingStatus
    from app.domain.job_plan import is_stale

    booking = Booking(
        id=1,
        group_id=1,
        slot_id=1,
        start=START,
        end=END,
        status=BookingStatus.CONFIRMED,
        contact=WITH_PHONE,
        locale="nl",
        token_hash="h",
        created_at=NOW,
    )

    assert not is_stale(START, booking)
    assert not is_stale(None, replace(booking, status=BookingStatus.CANCELLED))
    assert is_stale(START, replace(booking, status=BookingStatus.CANCELLED))
    assert is_stale(START - timedelta(days=1), booking)
    assert is_stale(START, replace(booking, contact=None))


def test_team_invite_is_planned_now_and_cancelled_with_the_booking() -> None:
    from app.domain.job_plan import plan_for_suspension

    booked = plan_for_booking(
        start=START,
        end=END,
        contact=NO_PHONE,
        consents=frozenset(),
        rules=[],
        now=NOW,
        dispatch_lead=LEAD,
        team_invite=True,
    )
    invite = [j for j in booked if j.type is JobType.SEND_TEAM_INVITE]
    cancelled = plan_for_cancellation(cancelled_at=NOW, team_invite=True)
    moved = plan_for_cancellation(cancelled_at=NOW, notify=False, team_invite=True)
    suspended = plan_for_suspension(now=NOW, purge_at=None, team_invite=True)

    assert [(j.run_at, j.pinned_start) for j in invite] == [(NOW, START)]
    for jobs in (cancelled, moved, suspended):
        assert [j.run_at for j in jobs if j.type is JobType.SEND_TEAM_CANCEL] == [NOW]
    plain = plan_for_cancellation(cancelled_at=NOW)
    assert not any(j.type is JobType.SEND_TEAM_CANCEL for j in plain)
