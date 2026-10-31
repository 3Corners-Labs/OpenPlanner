"""Calls: what leaves OpenPlanner for video and WxCC, and what comes back (ADR 0017, 0018, 0010).

What leaves OpenPlanner is built here, so the privacy rules live in one place: a meeting
request carries the group's name and times only; a work item carries the booking reference,
the first name and the meeting link (CLAUDE.md). Everything the platforms report back becomes
a ``BookingEvent`` and updates one aggregated ``CallStatus`` per booking.

    booked ─► meeting_created ─► task_created ─► task_connected ─► host_joined
          │                          │                                 guest_joined …
          └► meeting_failed (reason) └► task_failed (reason)        ─► meeting_ended
    cancelled ─► task_marked_cancelled (v2) ─► task_end_requested (v1) ─► task_ended
                                            └► end_not_possible (reason)       (ADR 0021)

No names, email addresses or IP addresses of participants are stored: only counts, whether a
host joined, and times. Pure Python, no I/O; ``app.domain.integration`` records and shows them.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.domain.booking import Booking
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Group, JoinClient

BROWSER_LINK_PARAMS: tuple[tuple[str, str], ...] = (("launchApp", "true"),)
"""What Webex's own *join from this browser* button adds to the meeting link (ADR 0075).

With it Webex's join page skips the choice between its app and the browser and opens the web
app's prepare-to-join screen. A constant, not a setting: it is Webex's mechanism, and the group
check tells us when Webex stops carrying it through."""


class EventKind(StrEnum):
    """Everything that can happen to a booking's call, in order of a normal call."""

    MEETING_CREATED = "meeting_created"
    MEETING_FAILED = "meeting_failed"
    """The meeting could not be created (detail: the code, e.g. ``rejected_400``)."""
    MEETING_DELETED = "meeting_deleted"
    JOIN_PAGE_OPENED = "join_page_opened"
    JOIN_CLICKED = "join_clicked"
    """The participant pressed the button on the join page (detail: the page they were sent to,
    ``browser`` or ``choice``, ADR 0075)."""
    TRIAL_CREATED = "trial_created"
    """The participant opened a trial call on Webex (ADR 0071)."""
    TRIAL_JOINED = "trial_joined"
    """The participant followed the link into the trial call (detail: the page they were sent to,
    ``browser`` or ``choice``, ADR 0075)."""
    TRIAL_ENTERED = "trial_entered"
    """Webex reports that someone joined the trial meeting (detail ``host`` when a host did,
    which nobody from the team should). A click that is never followed by this means the
    guest did not get in, for example because they wait in the lobby."""
    TRIAL_LEFT = "trial_left"
    """Webex reports that someone left the trial meeting."""
    TRIAL_FAILED = "trial_failed"
    """The trial meeting could not be created (detail: the code, e.g. ``rejected_400``)."""
    TRIAL_DELETED = "trial_deleted"
    TASK_CREATED = "task_created"
    TASK_FAILED = "task_failed"
    TASK_CONNECTED = "task_connected"
    TASK_ENDED = "task_ended"
    TASK_END_REQUESTED = "task_end_requested"
    END_NOT_POSSIBLE = "end_not_possible"
    """WxCC refused to end the task (detail: the code, e.g. ``rejected_403``)."""
    TASK_MARKED_CANCELLED = "task_marked_cancelled"
    CITIZEN_LEFT = "citizen_left"
    """The participant stopped waiting for a direct call (detail: ``waiting`` or ``agent``)."""
    MEETING_STARTED = "meeting_started"
    HOST_JOINED = "host_joined"
    GUEST_JOINED = "guest_joined"
    PARTICIPANT_LEFT = "participant_left"
    MEETING_ENDED = "meeting_ended"
    INTEGRATION_ERROR = "integration_error"


def join_link(link: str | None, client: JoinClient) -> str | None:
    """Return the link to send the participant to for ``client``, or ``None`` when there is none.

    The stored link comes from Webex and is personal data, so it is never altered in storage;
    this builds the redirect target. A link that is not ``https`` is refused, so a poisoned
    value can never become a redirect. ``JoinClient.BROWSER`` sets the parameters of
    ``BROWSER_LINK_PARAMS`` (replacing a value that says the opposite) and keeps every other
    part of the link; applying it twice gives the same link.
    """
    if not link or not link.startswith("https://"):
        return None
    if client is JoinClient.CHOICE:
        return link
    parts = urlsplit(link)
    names = {name for name, _ in BROWSER_LINK_PARAMS}
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in names]
    query.extend(BROWSER_LINK_PARAMS)
    return urlunsplit(parts._replace(query=urlencode(query)))


TRIAL_MEETING_EVENTS: dict[EventKind, EventKind] = {
    EventKind.GUEST_JOINED: EventKind.TRIAL_ENTERED,
    EventKind.HOST_JOINED: EventKind.TRIAL_ENTERED,
    EventKind.PARTICIPANT_LEFT: EventKind.TRIAL_LEFT,
}
"""How an event of a trial meeting (ADR 0071) is recorded: as its own kinds, so the
appointment's call status (participants, host joined) never counts the trial. The meeting's
start and end are left out: our own jobs open and delete it."""


class JoinState(StrEnum):
    """Whether the participant's join page can send them to the meeting now."""

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
    FAILED = "failed"


class TaskState(StrEnum):
    """State of the WxCC work item."""

    NONE = "none"
    QUEUED = "queued"
    CONNECTED = "connected"
    ENDED = "ended"
    FAILED = "failed"
    CANCELLED = "cancelled"
    """Marked cancelled by a v2 task message; ended once WxCC reports it (ADR 0021)."""


@dataclass(frozen=True)
class TaskSnapshot:
    """What the contact center says about a work item when asked (ADR 0082 point 1).

    Attributes:
        state: The task's state as OpenPlanner knows it; ``NONE`` when the platform does not
            know the task (or no longer does).
        status: The platform's own status word, lower case, for a finding or event; empty
            when unknown. Never the task's attributes or data.
        changed_at: When the platform last changed the task, if it says.
    """

    state: TaskState
    status: str = ""
    changed_at: datetime | None = None


@dataclass(frozen=True)
class BookingEvent:
    """One entry of a booking's timeline; ``detail`` is a reason code, never personal data.

    ``message`` is the provider's own reason for a failure (ADR 0065): masked, capped, and
    only from requests without participant data; empty for every other event. ``version`` is the
    ``APP_VERSION`` of the process that wrote the event (ADR 0068); empty in development and
    for events written before it was recorded.
    """

    booking_id: int
    at: datetime
    kind: EventKind
    detail: str = ""
    message: str = ""
    version: str = ""


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
    elif kind is EventKind.MEETING_FAILED:
        changes["call"] = CallState.FAILED
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
    elif kind is EventKind.TASK_MARKED_CANCELLED:
        changes["task"] = TaskState.CANCELLED
    return replace(status, **changes)  # type: ignore[arg-type]


@dataclass(frozen=True)
class MeetingStatus:
    """What the video provider reports about a meeting right now (ADR 0010 point 1).

    Aggregates only: the provider's participant list is reduced to a count and to whether the
    host (an agent) is in it before it leaves the adapter; names and addresses never get here.
    ``state`` is ``SCHEDULED``, ``IN_PROGRESS`` or ``ENDED``.
    """

    state: CallState
    participants: int = 0
    host_joined: bool = False
    started_at: datetime | None = None
    ended_at: datetime | None = None


def apply_polled(status: CallStatus, polled: MeetingStatus, at: datetime) -> CallStatus:
    """Return ``status`` after the provider reported ``polled`` at ``at`` (polling fallback).

    What the provider says now wins over counts built from webhooks, which drift when an event
    is lost. A meeting that has ended stays ended, and a host who joined once stays joined.
    """
    ended = polled.state is CallState.ENDED
    return replace(
        status,
        call=CallState.ENDED if status.call is CallState.ENDED else polled.state,
        participants=0 if ended else polled.participants,
        host_joined=status.host_joined or polled.host_joined,
        host_joined_at=status.host_joined_at or (at if polled.host_joined else None),
        started_at=status.started_at or polled.started_at,
        ended_at=status.ended_at or polled.ended_at or (at if ended else None),
        updated_at=at,
    )


def polled_events(before: CallStatus, after: CallStatus) -> list[EventKind]:
    """Return the timeline events a poll adds: only what changed since ``before``.

    A webhook would have recorded these; the poll records each transition once, so a quiet
    provider does not fill the timeline every 30 seconds.
    """
    events = []
    if before.call is not CallState.IN_PROGRESS and after.call is CallState.IN_PROGRESS:
        events.append(EventKind.MEETING_STARTED)
    if after.host_joined and not before.host_joined:
        events.append(EventKind.HOST_JOINED)
    if after.call is CallState.ENDED and before.call is not CallState.ENDED:
        events.append(EventKind.MEETING_ENDED)
    return events


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
    """What the video provider needs to schedule a booking's meeting (no participant data)."""

    title: str
    start: datetime
    end: datetime
    timezone: str
    host_email: str
    join_before_host: bool = False
    """A trial call (ADR 0071): the participant can enter alone, without the lobby."""


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
