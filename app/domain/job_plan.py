"""Which background jobs a booking needs, and when (HANDOVER §5).

Planning is pure; the worker (WP4) executes the jobs. The dispatch lead (T−5 min by default)
is a per-group setting (``Group.dispatch_lead_minutes``). Time-based jobs carry ``pinned_start``:
on run, a job re-reads the booking and does nothing if it is no longer confirmed or its start
changed. ``purge_pii`` is not pinned: it must also run for cancelled bookings.

    pending ──► send_confirm_request (now) ──► send_confirm_reminder × n ──► expire_unconfirmed
               (confirm_by) ──► purge_pii (confirm_by + 24 h)            (ADR 0056)
    confirmed (participant clicked) ──► as booked below
    lapsed ──► send_unconfirmed_lapsed (now); the purge planned while pending stays
    booked ──► create_video_room (now) ─► send_confirmation | send_change (now)
           ──► send_reminder × rules (start − offset, if still ahead and reachable)
           ──► dispatch_wxcc_task (start − dispatch lead) ──► purge_pii (end + 24 h)
    cancelled ──► end_wxcc_task, delete_video_room (now) ─► send_cancellation (now, unless
                  rescheduled) ──► purge_pii (now + 24 h)
    suspended ──► end_wxcc_task (now) ─► send_suspension (now) ──► purge_pii (closure end
                  + 30 days, once known)
              ──► send_closure_update (when an admin sends an update)
    reinstated ──► as booked, with send_reinstatement instead of the confirmation

Closure notices are not pinned (the booking is no longer confirmed); they only run while the
booking is still suspended (``is_stale``). Requests to confirm only run while the booking still
awaits confirmation.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.booking import Booking, Contact
from app.domain.channels import ChannelKind, ReminderRule, reachable
from app.domain.confirmation import ConfirmWindow


def dispatch_moment(start: datetime, dispatch_lead: timedelta) -> datetime:
    """Return the moment a booking's WxCC task is dispatched.

    From then on an agent may pick up the task and join the meeting. A booking cancelled or
    moved after it gets its task marked cancelled (ADR 0021); the participant's own change lead
    (``Group.change_deadline``, ADR 0069) is never shorter than the dispatch lead.
    """
    return start - dispatch_lead


PURGE_AFTER = timedelta(hours=24)
"""Personal data retention after the slot end or the cancellation."""


class JobType(StrEnum):
    """Every kind of background job."""

    CREATE_VIDEO_ROOM = "create_video_room"
    DELETE_VIDEO_ROOM = "delete_video_room"
    DELETE_TRIAL_CALL = "delete_trial_call"
    SEND_CONFIRMATION = "send_confirmation"
    SEND_CHANGE = "send_change"
    SEND_REMINDER = "send_reminder"
    SEND_CANCELLATION = "send_cancellation"
    DISPATCH_WXCC_TASK = "dispatch_wxcc_task"
    END_WXCC_TASK = "end_wxcc_task"
    PURGE_PII = "purge_pii"
    SEND_SUSPENSION = "send_suspension"
    SEND_CLOSURE_UPDATE = "send_closure_update"
    SEND_REINSTATEMENT = "send_reinstatement"
    SEND_REBOOK_REQUEST = "send_rebook_request"
    SEND_REBOOK_REMINDER = "send_rebook_reminder"
    SEND_REBOOK_LAPSED = "send_rebook_lapsed"
    LAPSE_REBOOK = "lapse_rebook"
    SEND_CONFIRM_REQUEST = "send_confirm_request"
    SEND_CONFIRM_REMINDER = "send_confirm_reminder"
    EXPIRE_UNCONFIRMED = "expire_unconfirmed"
    SEND_UNCONFIRMED_LAPSED = "send_unconfirmed_lapsed"
    SEND_TEAM_INVITE = "send_team_invite"
    SEND_TEAM_CANCEL = "send_team_cancel"


TEAM_INVITES = frozenset({JobType.SEND_TEAM_INVITE, JobType.SEND_TEAM_CANCEL})
"""The calendar invite to an own-queue team's address and its cancellation (ADR 0043)."""


CLOSURE_NOTICES = frozenset({JobType.SEND_SUSPENSION, JobType.SEND_CLOSURE_UPDATE})
"""Messages about an emergency closure; sent only while the booking is suspended."""

REBOOK_NOTICES = frozenset({JobType.SEND_REBOOK_REQUEST, JobType.SEND_REBOOK_REMINDER})
"""A planner's request to choose a new time (ADR 0044); sent only while it is unanswered."""

CONFIRM_NOTICES = frozenset({JobType.SEND_CONFIRM_REQUEST, JobType.SEND_CONFIRM_REMINDER})
"""Requests to confirm a booking by email link (ADR 0056); sent only while it is pending."""


@dataclass(frozen=True)
class PlannedJob:
    """A job to store for a booking. Carries no personal data (CLAUDE.md)."""

    type: JobType
    run_at: datetime
    pinned_start: datetime | None
    channel: ChannelKind | None = None


class JobStatus(StrEnum):
    """Where a stored job is in its life (``app.adapters.db.jobs``)."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class JobSummary:
    """One stored job as the booking page shows it (ADR 0068): ids and codes only."""

    type: JobType
    status: JobStatus
    run_at: datetime
    attempts: int = 0
    last_error: str = ""
    channel: ChannelKind | None = None


@dataclass(frozen=True)
class JobCounts:
    """What the group check reports about a group's queue (ADR 0068).

    Attributes:
        failed: Jobs that failed after their last attempt.
        overdue: Pending jobs whose planned time lies longer ago than the overdue threshold.
    """

    failed: int = 0
    overdue: int = 0


RETRYABLE_JOBS = frozenset({JobType.CREATE_VIDEO_ROOM, JobType.DISPATCH_WXCC_TASK})
"""Jobs an admin may plan again from the booking page (ADR 0068): the ones that create
something at a platform. Messages are not among them: the participant must not be mailed twice."""


def plan_for_booking(
    *,
    start: datetime,
    end: datetime,
    contact: Contact,
    consents: frozenset[ChannelKind],
    rules: Iterable[ReminderRule],
    now: datetime,
    dispatch_lead: timedelta,
    message: JobType = JobType.SEND_CONFIRMATION,
    sms: bool = False,
    team_invite: bool = False,
) -> list[PlannedJob]:
    """Return the jobs of a newly confirmed booking, in execution order.

    ``message`` is the first email: the confirmation, ``send_change`` after a reschedule or
    ``send_reinstatement`` when a suspended booking goes ahead after all. ``sms`` sends it as
    an SMS as well when the participant is reachable there (a planner's move, ADR 0044). Reminders
    are skipped
    when disabled, duplicated, not strictly in the future, or when the booking is not reachable
    on the channel. ``team_invite`` adds the invite to the team's address (ADR 0043).
    """
    jobs = [
        PlannedJob(JobType.CREATE_VIDEO_ROOM, now, start),
        PlannedJob(message, now, start, ChannelKind.EMAIL),
    ]
    if sms and reachable(ChannelKind.SMS, contact, consents):
        jobs.append(PlannedJob(message, now, start, ChannelKind.SMS))
    if team_invite:
        jobs.append(PlannedJob(JobType.SEND_TEAM_INVITE, now, start, ChannelKind.EMAIL))
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
    dispatch_at = max(now, dispatch_moment(start, dispatch_lead))
    jobs.append(PlannedJob(JobType.DISPATCH_WXCC_TASK, dispatch_at, start))
    jobs.append(PlannedJob(JobType.PURGE_PII, end + PURGE_AFTER, None))
    return jobs


def is_stale(
    pinned_start: datetime | None, booking: Booking, job_type: JobType | None = None
) -> bool:
    """Return whether a job pinned to ``pinned_start`` must do nothing for ``booking``.

    Pinned jobs only act on a confirmed booking whose start is unchanged and whose personal
    data still exists (CLAUDE.md). Closure notices only act on a booking that is still
    suspended; requests to rebook only while the booking still awaits a new time; requests to
    confirm only while it still awaits confirmation. Other unpinned jobs (cancellation mail,
    lapse, purge) always run.
    """
    if job_type in CLOSURE_NOTICES:
        return not booking.is_suspended or booking.contact is None
    if job_type in REBOOK_NOTICES:
        return not booking.awaits_rebook or booking.contact is None
    if job_type in CONFIRM_NOTICES:
        return not booking.awaits_confirmation or booking.contact is None
    if pinned_start is None:
        return False
    return not booking.is_active or booking.start != pinned_start or booking.contact is None


def plan_for_cancellation(
    *, cancelled_at: datetime, notify: bool = True, sms: bool = False, team_invite: bool = False
) -> list[PlannedJob]:
    """Return the jobs that replace all pending jobs of a cancelled booking.

    ``notify=False`` for the old booking of a reschedule: the participant gets a change message
    for the new booking instead of a cancellation. ``sms`` adds the SMS form of the
    cancellation (a planner's change, ADR 0044; the caller checks the participant is reachable).
    The work item is ended and the meeting removed first (ADR 0021); those jobs do nothing
    when the booking never got them.
    ``team_invite`` withdraws the team's calendar invite, also on a reschedule (ADR 0043).
    """
    end = PlannedJob(JobType.END_WXCC_TASK, cancelled_at, None)
    delete = PlannedJob(JobType.DELETE_VIDEO_ROOM, cancelled_at, None)
    trial = PlannedJob(JobType.DELETE_TRIAL_CALL, cancelled_at, None)
    message = PlannedJob(JobType.SEND_CANCELLATION, cancelled_at, None, ChannelKind.EMAIL)
    text = PlannedJob(JobType.SEND_CANCELLATION, cancelled_at, None, ChannelKind.SMS)
    purge = PlannedJob(JobType.PURGE_PII, cancelled_at + PURGE_AFTER, None)
    notices = ([message, text] if sms else [message]) if notify else []
    return [end, delete, trial, *notices, *_team_cancel(cancelled_at, team_invite), purge]


def plan_for_direct(*, start: datetime, end: datetime) -> list[PlannedJob]:
    """Return the jobs of a direct call: meeting, confirmation and work item now, then the purge.

    The web request usually creates the meeting and the work item itself (ADR 0019); these
    jobs are the fallback and do nothing once that has happened (they are idempotent).
    """
    return [
        PlannedJob(JobType.CREATE_VIDEO_ROOM, start, start),
        PlannedJob(JobType.SEND_CONFIRMATION, start, start, ChannelKind.EMAIL),
        PlannedJob(JobType.DISPATCH_WXCC_TASK, start, start),
        PlannedJob(JobType.PURGE_PII, end + PURGE_AFTER, None),
    ]


def plan_for_rebook(
    *,
    now: datetime,
    rebook_by: datetime,
    reminder_at: datetime | None,
    sms: bool,
    team_invite: bool = False,
) -> list[PlannedJob]:
    """Return the jobs that replace all pending jobs of a booking awaiting a new time.

    A planner asked the participant to choose a new time (ADR 0044). The meeting goes (the
    booking never goes ahead at this time) and so do the reminders and the planned task. The
    participant gets the request now and one reminder at ``reminder_at`` if that lies ahead; at
    ``rebook_by`` an unanswered request lapses (``LAPSE_REBOOK`` cancels the booking and
    sends the lapse message) and the personal data is purged ``PURGE_AFTER`` later. ``sms``
    adds the SMS forms (the caller checks the participant is reachable). Choosing a new time or
    cancelling replaces all of these jobs. ``team_invite`` withdraws the team's calendar
    invite (ADR 0043).
    """
    channels = [ChannelKind.EMAIL, ChannelKind.SMS] if sms else [ChannelKind.EMAIL]
    jobs = [
        PlannedJob(JobType.END_WXCC_TASK, now, None),
        PlannedJob(JobType.DELETE_VIDEO_ROOM, now, None),
        PlannedJob(JobType.DELETE_TRIAL_CALL, now, None),
        *(PlannedJob(JobType.SEND_REBOOK_REQUEST, now, None, c) for c in channels),
        *_team_cancel(now, team_invite),
    ]
    if reminder_at is not None and reminder_at > now:
        jobs.extend(
            PlannedJob(JobType.SEND_REBOOK_REMINDER, reminder_at, None, c) for c in channels
        )
    jobs.append(PlannedJob(JobType.LAPSE_REBOOK, rebook_by, None))
    jobs.append(PlannedJob(JobType.PURGE_PII, rebook_by + PURGE_AFTER, None))
    return jobs


def plan_for_lapse(*, now: datetime, sms: bool) -> list[PlannedJob]:
    """Return the message jobs when an unanswered request to rebook lapses (ADR 0044).

    The purge planned with the request stays.
    """
    channels = [ChannelKind.EMAIL, ChannelKind.SMS] if sms else [ChannelKind.EMAIL]
    return [PlannedJob(JobType.SEND_REBOOK_LAPSED, now, None, c) for c in channels]


def plan_for_pending(*, now: datetime, window: ConfirmWindow) -> list[PlannedJob]:
    """Return the jobs of a booking that awaits the participant's confirmation (ADR 0056).

    The request to confirm goes now, the reminders at ``window.reminders``, all by email (the
    point is to verify that address). At ``window.confirm_by`` ``EXPIRE_UNCONFIRMED`` cancels
    a booking that is still pending, and its personal data is purged ``PURGE_AFTER`` later.
    No meeting, reminder of the appointment or WxCC task yet: confirming replaces all of
    these jobs with ``plan_for_booking``.
    """
    return [
        PlannedJob(JobType.SEND_CONFIRM_REQUEST, now, None, ChannelKind.EMAIL),
        *(
            PlannedJob(JobType.SEND_CONFIRM_REMINDER, at, None, ChannelKind.EMAIL)
            for at in window.reminders
        ),
        PlannedJob(JobType.EXPIRE_UNCONFIRMED, window.confirm_by, None),
        PlannedJob(JobType.PURGE_PII, window.confirm_by + PURGE_AFTER, None),
    ]


def plan_for_unconfirmed_lapse(*, now: datetime) -> list[PlannedJob]:
    """Return the message job when an unconfirmed booking lapses (ADR 0056).

    The purge planned with the request to confirm stays.
    """
    return [PlannedJob(JobType.SEND_UNCONFIRMED_LAPSED, now, None, ChannelKind.EMAIL)]


def plan_for_suspension(
    *, now: datetime, purge_at: datetime | None, team_invite: bool = False
) -> list[PlannedJob]:
    """Return the jobs that replace all pending jobs of a booking put on hold (ADR 0024).

    Reminders and the planned WxCC task go, and a work item that already exists is ended;
    the meeting stays, so a reinstated booking keeps its link. An open trial call is deleted
    (ADR 0071): replacing the jobs would otherwise drop its pending clean-up. The purge waits
    for the end of the closure (``Closure.purge_at``); while the closure is open-ended there is
    none, and ``plan_purge`` adds it once the end is known.
    """
    jobs = [
        PlannedJob(JobType.END_WXCC_TASK, now, None),
        PlannedJob(JobType.DELETE_TRIAL_CALL, now, None),
        PlannedJob(JobType.SEND_SUSPENSION, now, None, ChannelKind.EMAIL),
    ]
    return jobs + _team_cancel(now, team_invite) + plan_purge(purge_at)


def _team_cancel(now: datetime, team_invite: bool) -> list[PlannedJob]:
    """Return the withdrawal of the team's calendar invite, if the group sends one."""
    return (
        [PlannedJob(JobType.SEND_TEAM_CANCEL, now, None, ChannelKind.EMAIL)] if team_invite else []
    )


def plan_purge(purge_at: datetime | None) -> list[PlannedJob]:
    """Return the purge of a suspended booking at ``purge_at`` (none while it is unknown)."""
    return [PlannedJob(JobType.PURGE_PII, purge_at, None)] if purge_at else []


def plan_closure_update(*, now: datetime) -> list[PlannedJob]:
    """Return the job that mails a suspended booking the closure's updated notice."""
    return [PlannedJob(JobType.SEND_CLOSURE_UPDATE, now, None, ChannelKind.EMAIL)]
