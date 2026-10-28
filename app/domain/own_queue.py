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

Pure Python, no I/O; ``QueueService`` loads the bookings and claims in a locked transaction.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.booking import Booking, BookingKind, Claim
from app.domain.calls import CallStatus
from app.domain.scheduling import Group

__all__ = ["Claim", "QueueEntry", "next_up", "not_claimed_in_time", "visible"]


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
