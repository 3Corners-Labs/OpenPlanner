"""Which background jobs a booking needs, and when (HANDOVER §5).

Planning is pure; the worker (WP4) executes the jobs. The dispatch lead (T−5 min by default)
is a per-group setting (``Group.dispatch_lead_minutes``). Time-based jobs carry ``pinned_start``:
on run, a job re-reads the booking and does nothing if it is no longer confirmed or its start
changed. ``purge_pii`` is not pinned: it must also run for cancelled bookings.

    booked ──► create_video_room (now) ─► send_confirmation | send_change (now)
           ──► send_reminder × rules (start − offset, if still ahead and reachable)
           ──► dispatch_wxcc_task (start − dispatch lead) ──► purge_pii (end + 24 h)
    cancelled ──► send_cancellation (now, unless rescheduled) ──► purge_pii (now + 24 h)
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.booking import Booking, Contact
from app.domain.channels import ChannelKind, ReminderRule, reachable


def change_deadline(start: datetime, dispatch_lead: timedelta) -> datetime:
    """Return the moment a booking becomes read-only: when its WxCC task is dispatched.

    From then on an agent may pick up the task and join the meeting, so cancelling or
    rescheduling would leave a task for an appointment that no longer exists.
    """
    return start - dispatch_lead


PURGE_AFTER = timedelta(hours=24)
"""Personal data retention after the slot end or the cancellation."""


class JobType(StrEnum):
    """Every kind of background job."""

    CREATE_VIDEO_ROOM = "create_video_room"
    SEND_CONFIRMATION = "send_confirmation"
    SEND_CHANGE = "send_change"
    SEND_REMINDER = "send_reminder"
    SEND_CANCELLATION = "send_cancellation"
    DISPATCH_WXCC_TASK = "dispatch_wxcc_task"
    PURGE_PII = "purge_pii"


@dataclass(frozen=True)
class PlannedJob:
    """A job to store for a booking. Carries no personal data (CLAUDE.md)."""

    type: JobType
    run_at: datetime
    pinned_start: datetime | None
    channel: ChannelKind | None = None


def plan_for_booking(
    *,
    start: datetime,
    end: datetime,
    contact: Contact,
    consents: frozenset[ChannelKind],
    rules: Iterable[ReminderRule],
    now: datetime,
    dispatch_lead: timedelta,
    rescheduled: bool = False,
) -> list[PlannedJob]:
    """Return the jobs of a newly confirmed booking, in execution order.

    Reminders are skipped when disabled, duplicated, not strictly in the future, or when the
    booking is not reachable on the channel.
    """
    message = JobType.SEND_CHANGE if rescheduled else JobType.SEND_CONFIRMATION
    jobs = [
        PlannedJob(JobType.CREATE_VIDEO_ROOM, now, start),
        PlannedJob(message, now, start, ChannelKind.EMAIL),
    ]
    seen: set[tuple[ChannelKind, int]] = set()
    reminders: list[PlannedJob] = []
    for rule in rules:
        key = (rule.channel, rule.minutes_before)
        run_at = start - timedelta(minutes=rule.minutes_before)
        if (
            not rule.enabled
            or key in seen
            or run_at <= now
            or not reachable(rule.channel, contact, consents)
        ):
            continue
        seen.add(key)
        reminders.append(PlannedJob(JobType.SEND_REMINDER, run_at, start, rule.channel))
    jobs.extend(sorted(reminders, key=lambda job: job.run_at))
    dispatch_at = max(now, change_deadline(start, dispatch_lead))
    jobs.append(PlannedJob(JobType.DISPATCH_WXCC_TASK, dispatch_at, start))
    jobs.append(PlannedJob(JobType.PURGE_PII, end + PURGE_AFTER, None))
    return jobs


def is_stale(pinned_start: datetime | None, booking: Booking) -> bool:
    """Return whether a job pinned to ``pinned_start`` must do nothing for ``booking``.

    Pinned jobs only act on a confirmed booking whose start is unchanged and whose personal
    data still exists (CLAUDE.md). Unpinned jobs (cancellation mail, purge) always run.
    """
    if pinned_start is None:
        return False
    return not booking.is_active or booking.start != pinned_start or booking.contact is None


def plan_for_cancellation(*, cancelled_at: datetime, notify: bool = True) -> list[PlannedJob]:
    """Return the jobs that replace all pending jobs of a cancelled booking.

    ``notify=False`` for the old booking of a reschedule: the citizen gets a change message
    for the new booking instead of a cancellation.
    """
    jobs = [PlannedJob(JobType.SEND_CANCELLATION, cancelled_at, None, ChannelKind.EMAIL)]
    purge = PlannedJob(JobType.PURGE_PII, cancelled_at + PURGE_AFTER, None)
    return [*jobs, purge] if notify else [purge]
