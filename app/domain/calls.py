"""Calls: what leaves OpenPlanner for video and WxCC, and what comes back (ADR 0017, 0018, 0010).

What leaves OpenPlanner is built here, so the privacy rules live in one place: a meeting
request carries the group's name and times only; a work item carries the booking reference,
the first name and the meeting link (CLAUDE.md). Everything the platforms report back becomes
a ``BookingEvent`` and updates one aggregated ``CallStatus`` per booking.

    booked ─► meeting_created ─► task_created ─► task_connected ─► host_joined
                                     │                                 guest_joined …
                                     └► task_failed (reason)        ─► meeting_ended

No names, email addresses or IP addresses of participants are stored: only counts, whether a
host joined, and times. Pure Python, no I/O; ``app.domain.integration`` records and shows them.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.booking import Booking
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Group


class EventKind(StrEnum):
    """Everything that can happen to a booking's call, in order of a normal call."""

    MEETING_CREATED = "meeting_created"
    MEETING_DELETED = "meeting_deleted"
    JOIN_PAGE_OPENED = "join_page_opened"
    JOIN_CLICKED = "join_clicked"
    TASK_CREATED = "task_created"
    TASK_FAILED = "task_failed"
    TASK_CONNECTED = "task_connected"
    TASK_ENDED = "task_ended"
    MEETING_STARTED = "meeting_started"
    HOST_JOINED = "host_joined"
    GUEST_JOINED = "guest_joined"
    PARTICIPANT_LEFT = "participant_left"
    MEETING_ENDED = "meeting_ended"
    INTEGRATION_ERROR = "integration_error"


class JoinState(StrEnum):
    """Whether the citizen's join page can send them to the meeting now."""

    TOO_EARLY = "too_early"
    OPEN = "open"
    CLOSED = "closed"
    NO_MEETING = "no_meeting"
    INACTIVE = "inactive"


JOIN_OPENS_BEFORE = timedelta(minutes=15)
JOIN_CLOSES_AFTER = timedelta(minutes=30)


def join_state(booking: Booking, now: datetime) -> JoinState:
    """Return whether ``booking``'s meeting can be joined at ``now``.

    The page opens 15 minutes before the start and stays open until 30 minutes after the end.
    """
    if not booking.is_active:
        return JoinState.INACTIVE
    if booking.meeting is None or booking.meeting.link is None:
        return JoinState.NO_MEETING
    if now < booking.start - JOIN_OPENS_BEFORE:
        return JoinState.TOO_EARLY
    if now > booking.end + JOIN_CLOSES_AFTER:
        return JoinState.CLOSED
    return JoinState.OPEN


class CallState(StrEnum):
    """State of the video meeting."""

    NONE = "none"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    ENDED = "ended"


class TaskState(StrEnum):
    """State of the WxCC work item."""

    NONE = "none"
    QUEUED = "queued"
    CONNECTED = "connected"
    ENDED = "ended"
    FAILED = "failed"


@dataclass(frozen=True)
class BookingEvent:
    """One entry of a booking's timeline; ``detail`` is a reason code, never personal data."""

    booking_id: int
    at: datetime
    kind: EventKind
    detail: str = ""


@dataclass(frozen=True)
class CallStatus:
    """The aggregated state of a booking's meeting and work item (ADR 0010)."""

    booking_id: int
    call: CallState = CallState.NONE
    task: TaskState = TaskState.NONE
    participants: int = 0
    host_joined: bool = False
    host_joined_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    task_created_at: datetime | None = None
    connected_at: datetime | None = None
    updated_at: datetime | None = None


def new_status(booking_id: int) -> CallStatus:
    """Return the status of a booking nothing has happened to yet."""
    return CallStatus(booking_id)


def apply_event(status: CallStatus, kind: EventKind, at: datetime) -> CallStatus:
    """Return ``status`` after ``kind`` happened at ``at``; unrelated events change nothing."""
    changes: dict[str, object] = {"updated_at": at}
    if kind is EventKind.MEETING_CREATED:
        changes["call"] = CallState.SCHEDULED
    elif kind is EventKind.MEETING_STARTED:
        changes |= {"call": CallState.IN_PROGRESS, "started_at": status.started_at or at}
    elif kind in (EventKind.HOST_JOINED, EventKind.GUEST_JOINED):
        changes["participants"] = status.participants + 1
        if kind is EventKind.HOST_JOINED:
            changes |= {"host_joined": True, "host_joined_at": status.host_joined_at or at}
    elif kind is EventKind.PARTICIPANT_LEFT:
        changes["participants"] = max(0, status.participants - 1)
    elif kind is EventKind.MEETING_ENDED:
        changes |= {"call": CallState.ENDED, "ended_at": at, "participants": 0}
    elif kind is EventKind.TASK_CREATED:
        changes |= {"task": TaskState.QUEUED, "task_created_at": at}
    elif kind is EventKind.TASK_CONNECTED:
        changes |= {"task": TaskState.CONNECTED, "connected_at": status.connected_at or at}
    elif kind is EventKind.TASK_ENDED:
        changes["task"] = TaskState.ENDED
    elif kind is EventKind.TASK_FAILED:
        changes["task"] = TaskState.FAILED
    return replace(status, **changes)  # type: ignore[arg-type]


def not_picked_up(status: CallStatus, start: datetime, now: datetime, limit: timedelta) -> bool:
    """Return whether a dispatched call is still without a host ``limit`` after its start."""
    return (
        status.task in (TaskState.QUEUED, TaskState.CONNECTED)
        and not status.host_joined
        and now - start > limit
    )


@dataclass(frozen=True)
class BookingOverview:
    """A booking with its call status, for the admin agenda."""

    booking: Booking
    status: CallStatus


@dataclass(frozen=True)
class MeetingRequest:
    """What the video provider needs to schedule a booking's meeting (no citizen data)."""

    title: str
    start: datetime
    end: datetime
    timezone: str
    host_email: str


@dataclass(frozen=True)
class WorkItem:
    """What WxCC receives for a booking: reference, first name and meeting link only."""

    reference: str
    first_name: str = field(repr=False)
    start_label: str
    group_name: str
    meeting_link: str = field(repr=False)
    kind: str
    business_address: str
    channel: str
    routing_value: str


def meeting_request(booking: Booking, group: Group) -> MeetingRequest:
    """Return the meeting to schedule for ``booking`` in ``group``.

    Raises:
        DomainError: ``integration_not_ready`` (``video_host_email``) without a host.
    """
    if group.video_host_email is None:
        raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "video_host_email")
    return MeetingRequest(
        title=f"Videogesprek {group.name}",
        start=booking.start,
        end=booking.end,
        timezone=group.timezone,
        host_email=group.video_host_email,
    )


def work_item(booking: Booking, group: Group, *, start_label: str) -> WorkItem:
    """Return the work item to dispatch for ``booking``.

    ``start_label`` is the start time as the agent reads it (formatted at the edge).

    Raises:
        DomainError: ``integration_not_ready`` naming what is missing (``wxcc``,
            ``meeting`` or ``contact``).
    """
    if group.wxcc_business_address is None or group.wxcc_channel is None:
        raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "wxcc")
    if booking.meeting is None or booking.meeting.link is None:
        raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "meeting")
    if booking.contact is None:
        raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "contact")
    return WorkItem(
        reference=booking.reference,
        first_name=booking.contact.first_name,
        start_label=start_label,
        group_name=group.name,
        meeting_link=booking.meeting.link,
        kind=booking.kind.value,
        business_address=group.wxcc_business_address,
        channel=group.wxcc_channel,
        routing_value=group.routing_value,
    )
