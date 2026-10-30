"""OpenPlanner's own queue (ADR 0043): which bookings agents see and which one is next.

A group with ``DispatchKind.OPENPLANNER_QUEUE`` sends no task to a contact center. Its agents
sign in and claim a booking; the claim, not a routing engine, decides who takes it. The
citizen always gets the first free agent: "pick up next" takes the citizen waiting in the
lobby first, then the earliest start.

    visible ─ confirmed, start ≤ now + queue_visible_from_minutes, end > now
    next_up ─ unclaimed; in the lobby first, then by start, then by booking id

"In the lobby" means the citizen joined the meeting (Webex webhook, ADR 0010) and no host
has. The lobby wait itself is not stored, so among waiting citizens the earliest start goes
first, which is nearly always the one waiting longest.

``summarise`` and ``works_queue`` feed the overview's *Queue across own groups* widget (ADR
0061 §2): how many citizens wait per group, for how long at most, and how many staff who may
work the queue are signed in.

Pure Python, no I/O; ``QueueService`` loads the bookings and claims in a locked transaction.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.authorization import Permission, Principal, Target, decide
from app.domain.booking import Booking, BookingKind, Claim
from app.domain.calls import CallStatus
from app.domain.scheduling import Group
from app.domain.staff import StaffMember

__all__ = [
    "Claim",
    "QueueEntry",
    "QueueSummary",
    "next_up",
    "not_claimed_in_time",
    "summarise",
    "visible",
    "works_queue",
]

NO_WAIT = timedelta(0)


@dataclass(frozen=True)
class QueueEntry:
    """A booking as the queue shows it, with its call status."""

    booking: Booking
    status: CallStatus

    @property
    def in_lobby(self) -> bool:
        """Whether the citizen has joined and waits for an agent."""
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
    def waiting_since(self) -> datetime:
        """When the waiting citizen's presence last changed: their join, nearly always.

        The lobby join itself is not stored (module docstring); the status's last change is
        the join for a citizen alone in the lobby. Without one, the booking's start.
        """
        return self.status.updated_at or self.booking.start


@dataclass(frozen=True)
class QueueSummary:
    """One group's queue at a glance: who waits and for how long at most.

    Attributes:
        waiting: Citizens in the lobby nobody picked up yet.
        longest_wait: How long the first of them has waited; zero when nobody waits.
    """

    waiting: int
    longest_wait: timedelta


def summarise(entries: Iterable[QueueEntry], now: datetime) -> QueueSummary:
    """Return how many entries wait unclaimed in the lobby and the longest wait among them."""
    waiting = [e for e in entries if e.in_lobby and not e.claimed]
    waits = [max(now - e.waiting_since, NO_WAIT) for e in waiting]
    return QueueSummary(len(waiting), max(waits, default=NO_WAIT))


def works_queue(member: StaffMember, group_slug: str, organisation_slug: str) -> bool:
    """Return whether ``member``'s roles and memberships grant ``queue.work`` on the group.

    Decided by the matrix from what the directory recorded at the last login (ADR 0046);
    custom roles are not in the directory, so a member with custom roles only does not count.
    """
    as_principal = Principal(
        member.subject,
        member.display_name,
        member.roles,
        frozenset(m.organisation for m in member.memberships if m.group is None),
        frozenset(m.group for m in member.memberships if m.group is not None),
    )
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
    return min(open_entries, key=lambda e: (not e.in_lobby, e.booking.start, e.booking.id or 0))


def not_claimed_in_time(entry: QueueEntry, now: datetime, limit: timedelta) -> bool:
    """Return whether nobody claimed ``entry`` within ``limit`` after its start."""
    return not entry.claimed and now - entry.booking.start > limit
