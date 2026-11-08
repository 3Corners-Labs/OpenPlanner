"""OpenPlanner's own queue (ADR 0043): which bookings agents see and which one is next.

A group with ``DispatchKind.OPENPLANNER_QUEUE`` sends no task to a contact center. Its agents
sign in and claim a booking; the claim, not a routing engine, decides who takes it. The
participant always gets the first free agent: "pick up next" takes the participant waiting in the
lobby first, then the earliest start.

    visible ─ confirmed, start ≤ now + queue_visible_from_minutes, end > now
    next_up ─ unclaimed; in the lobby first (longest waiting first), then by start, then by id

"In the lobby" means the participant joined the meeting (Webex webhook, ADR 0010) and no host
has. ``CallStatus.lobby_since`` records when, so among waiting participants the one who joined
first goes first; a status from before that field falls back to its last change, then the
start. A direct call (ADR 0019) is the request to talk now, so its participant waits from its
start, whether or not they opened the link yet; a scheduled appointment's participant waits from
the lobby join.

Every row also says whether the video link is ready, still being prepared or failed, and the
claimed booking where the participant is (ADR 0072): an agent never waits for a link that will
not come.
``summarise`` and ``works_queue`` feed the overview's *Queue across own groups* widget (ADR
0061 §2): how many participants wait per group, for how long at most, and how many staff who may
work the queue are signed in.

Pure Python, no I/O; ``QueueService`` loads the bookings and claims in a locked transaction.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.authorization import Grant, Permission, Principal, Role, Target, decide
from app.domain.booking import Booking, BookingKind, Claim
from app.domain.calls import CallState, CallStatus
from app.domain.scheduling import Group
from app.domain.staff import StaffMember

__all__ = [
    "ParticipantState",
    "Claim",
    "QueueEntry",
    "QueueSummary",
    "VideoState",
    "participant_state",
    "next_up",
    "not_claimed_in_time",
    "not_picked_up",
    "summarise",
    "video_state",
    "visible",
    "works_queue",
]

NO_WAIT = timedelta(0)


class VideoState(StrEnum):
    """Whether a booking's meeting can be joined (ADR 0072)."""

    READY = "ready"
    PENDING = "pending"
    """Not created yet: the worker is on it, or retrying (ADR 0019)."""
    FAILED = "failed"
    """The provider refused; the reason is on the booking's timeline (ADR 0065)."""


class ParticipantState(StrEnum):
    """Where the participant is, as the Webex webhooks report it (ADR 0010)."""

    NOT_YET = "not_yet"
    IN_LOBBY = "in_lobby"
    IN_CALL = "in_call"
    """The host joined, so the meeting is under way."""
    ENDED = "ended"


def video_state(booking: Booking, status: CallStatus) -> VideoState:
    """Return whether ``booking``'s meeting is ready, pending or failed."""
    if status.call is CallState.FAILED:
        return VideoState.FAILED
    if booking.meeting is not None and booking.meeting.link:
        return VideoState.READY
    return VideoState.PENDING


def participant_state(status: CallStatus) -> ParticipantState:
    """Return where the participant of a booking with ``status`` is."""
    if status.call is CallState.ENDED:
        return ParticipantState.ENDED
    if status.host_joined:
        return ParticipantState.IN_CALL
    if status.participants > 0:
        return ParticipantState.IN_LOBBY
    return ParticipantState.NOT_YET


@dataclass(frozen=True)
class QueueEntry:
    """A booking as the queue shows it, with its call status."""

    booking: Booking
    status: CallStatus

    @property
    def in_lobby(self) -> bool:
        """Whether the participant has joined and waits for an agent."""
        return self.status.participants > 0 and not self.status.host_joined

    @property
    def claimed(self) -> bool:
        """Whether an agent picked the booking up."""
        return self.booking.claim is not None

    @property
    def is_direct(self) -> bool:
        """Whether this is a direct call (ADR 0019) rather than a scheduled appointment."""
        return self.booking.kind is BookingKind.DIRECT

    @property
    def video(self) -> VideoState:
        """Whether the meeting is ready, pending or failed (ADR 0072)."""
        return video_state(self.booking, self.status)

    @property
    def waiting(self) -> bool:
        """Whether the participant waits for an agent nobody picked up yet.

        A scheduled participant waits once in the lobby; a direct caller from the request itself,
        until an agent claims the call or joins the meeting.
        """
        if self.claimed or self.status.host_joined:
            return False
        return self.in_lobby or (self.is_direct and self.status.call is not CallState.ENDED)

    @property
    def waiting_since(self) -> datetime:
        """When the participant started waiting.

        A direct call waits from its start, which is the request (ADR 0019). A scheduled
        appointment waits from the lobby join (``CallStatus.lobby_since``); a status recorded
        before that field existed falls back to its last change, then to the booking's start.
        """
        if self.is_direct:
            return self.booking.start
        return self.status.lobby_since or self.status.updated_at or self.booking.start


@dataclass(frozen=True)
class QueueSummary:
    """One group's queue at a glance: who waits and for how long at most.

    Attributes:
        waiting: Participants nobody picked up yet: in the lobby, or direct callers since their
            request (``QueueEntry.waiting``).
        longest_wait: How long the first of them has waited; zero when nobody waits.
    """

    waiting: int
    longest_wait: timedelta


def summarise(entries: Iterable[QueueEntry], now: datetime) -> QueueSummary:
    """Return how many entries wait for an agent and the longest wait among them."""
    waiting = [e for e in entries if e.waiting]
    waits = [max(now - e.waiting_since, NO_WAIT) for e in waiting]
    return QueueSummary(len(waiting), max(waits, default=NO_WAIT))


def works_queue(member: StaffMember, group_slug: str, organisation_slug: str) -> bool:
    """Return whether ``member``'s roles and memberships grant ``queue.work`` on the group.

    Decided by the matrix from what the directory recorded at the last login (ADR 0046);
    custom roles are not in the directory, so a member with custom roles only does not count.
    """
    grants = {Grant(m.role, m.organisation, m.group) for m in member.memberships}
    if Role.SUPERUSER in member.roles:
        grants.add(Grant(Role.SUPERUSER.value))
    as_principal = Principal(member.subject, member.display_name, frozenset(grants))
    target = Target.for_group(group_slug, organisation_slug)
    return decide(as_principal, Permission.QUEUE_WORK, target).allowed


def visible(entries: Iterable[QueueEntry], group: Group, now: datetime) -> list[QueueEntry]:
    """Return the entries the group's queue shows at ``now``, by start."""
    horizon = now + timedelta(minutes=group.queue_visible_from_minutes)
    shown = (
        e
        for e in entries
        if e.booking.is_active and e.booking.start <= horizon and e.booking.end > now
    )
    return sorted(shown, key=lambda e: (e.booking.start, e.booking.id or 0))


def next_up(entries: Iterable[QueueEntry]) -> QueueEntry | None:
    """Return the unclaimed entry "pick up next" takes, or ``None`` when none is left."""
    open_entries = [e for e in entries if not e.claimed and e.booking.is_active]
    if not open_entries:
        return None
    return min(open_entries, key=_pick_order)


def _pick_order(entry: QueueEntry) -> tuple[bool, datetime, datetime, int]:
    """Order for "pick up next": in the lobby first, the longest waiting first, then by start."""
    since = entry.waiting_since if entry.in_lobby else entry.booking.start
    return (not entry.in_lobby, since, entry.booking.start, entry.booking.id or 0)


def not_claimed_in_time(entry: QueueEntry, now: datetime, limit: timedelta) -> bool:
    """Return whether nobody claimed ``entry`` within ``limit`` after its start."""
    return not entry.claimed and now - entry.booking.start > limit


def not_picked_up(entries: Iterable[QueueEntry], now: datetime, limit: timedelta) -> int:
    """Return how many entries nobody claimed within ``limit`` after their start (ADR 0043 §8)."""
    return sum(1 for e in entries if not_claimed_in_time(e, now, limit))
