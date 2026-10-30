"""The group's own queue of direct calls (ADR 0026): agents taken, places in line, usual wait.

A group can cap how many direct calls are open at once, so the WxCC queue does not fill up with
calls nobody can take; a waiting citizen sees their place in line and how long a pickup usually
takes. Both come only from what OpenPlanner already knows (its own direct calls and their
``CallStatus``), not from WxCC's live statistics. Scheduled calls in the same WxCC queue are
not counted.

    WAITING   ─ task none or queued, no host yet, at most not_picked_up_minutes after the start
    CONNECTED ─ task connected, booking claimed (own queue, ADR 0043) or host joined, until the
                meeting or task ends, at most JOIN_CLOSES_AFTER after the end
    DONE      ─ anything else: ended, failed, cancelled, or stale (a lost webhook frees the place)

``wait_state`` is what the citizen's waiting page tells them (ADR 0071), for both dispatch
kinds: a claim in the own queue counts as accepted, a failed meeting is said so.

Pure Python, no I/O; ``BookingService`` loads the calls and applies the cap in its locked
transaction.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.booking import Booking
from app.domain.calls import JOIN_CLOSES_AFTER, CallState, CallStatus, TaskState
from app.domain.scheduling import Group

SAMPLE_SIZE = 20
"""How many of the latest pickups the usual wait is based on."""
MIN_SAMPLES = 3
"""Below this many pickups no usual wait is shown: it would say more than we know."""
ESTIMATE_WINDOW = timedelta(days=7)
"""How far back pickups count for the usual wait."""


class DirectAvailability(StrEnum):
    """Whether a citizen can start a direct call now, and if not, why."""

    AVAILABLE = "available"
    FULL = "full"
    """Open, but the cap of open direct calls is reached."""
    UNAVAILABLE = "unavailable"
    """Switched off, outside opening hours, on a holiday or during a closure."""


class QueuePhase(StrEnum):
    """Where a direct call is in the group's own queue."""

    WAITING = "waiting"
    CONNECTED = "connected"
    DONE = "done"


@dataclass(frozen=True)
class DirectCall:
    """A direct call as the queue sees it; no personal data.

    Attributes:
        booking_id: The direct booking.
        start: When the citizen started the call.
        end: The end of the booking (start plus the group's slot length).
        active: Whether the booking is still confirmed (not cancelled).
        status: The aggregated call status (ADR 0010).
        claimed: Whether an agent of the own queue picked the call up (ADR 0043).
    """

    booking_id: int
    start: datetime
    end: datetime
    active: bool
    status: CallStatus
    claimed: bool = False


class WaitState(StrEnum):
    """What the citizen's waiting page says about a direct call (ADR 0071)."""

    SEARCHING = "searching"
    """No agent yet; the citizen may wait in the lobby once the meeting is ready."""
    ACCEPTED = "accepted"
    """The task is connected, or an agent of the own queue claimed the booking."""
    IN_CALL = "in_call"
    """The host joined the meeting."""
    UNAVAILABLE = "unavailable"
    """The task failed, or nobody accepted within ``not_picked_up_minutes``."""
    FAILED = "failed"
    """The meeting could not be created; nobody can join, so the citizen is told."""
    INACTIVE = "inactive"
    """The booking is no longer confirmed."""


@dataclass(frozen=True)
class QueueInfo:
    """What a citizen is told about the group's queue of direct calls.

    Attributes:
        waiting: Direct calls waiting for an agent now.
        position: The citizen's place in line (1 is next), if their call is waiting.
        typical_wait_minutes: The usual time to a pickup, if there are enough recent pickups.
    """

    waiting: int
    position: int | None
    typical_wait_minutes: int | None


def phase(call: DirectCall, group: Group, now: datetime) -> QueuePhase:
    """Return where ``call`` is in ``group``'s queue at ``now``."""
    status = call.status
    finished = status.task in (TaskState.ENDED, TaskState.FAILED, TaskState.CANCELLED)
    if not call.active or finished or status.call in (CallState.ENDED, CallState.FAILED):
        return QueuePhase.DONE
    if status.task is TaskState.CONNECTED or status.host_joined or call.claimed:
        return QueuePhase.CONNECTED if now <= call.end + JOIN_CLOSES_AFTER else QueuePhase.DONE
    if now - call.start > timedelta(minutes=group.not_picked_up_minutes):
        return QueuePhase.DONE
    return QueuePhase.WAITING


def wait_state(booking: Booking, status: CallStatus, group: Group, now: datetime) -> WaitState:
    """Return what the waiting page tells the citizen about their direct ``booking`` at ``now``.

    The same rule serves both dispatch kinds (ADR 0043): a WxCC task that connected and a
    claim in the own queue both mean an agent accepted. A meeting that failed is said so, and
    a call nobody accepted within ``group.not_picked_up_minutes`` is unavailable whether or
    not a task was ever created.
    """
    if not booking.is_active:
        return WaitState.INACTIVE
    if status.call is CallState.FAILED:
        return WaitState.FAILED
    if status.host_joined:
        return WaitState.IN_CALL
    accepted = status.task is TaskState.CONNECTED or booking.claim is not None
    if status.task is TaskState.FAILED:
        return WaitState.UNAVAILABLE
    if accepted:
        return WaitState.ACCEPTED
    if now - booking.start > timedelta(minutes=group.not_picked_up_minutes):
        return WaitState.UNAVAILABLE
    return WaitState.SEARCHING


def load_since(group: Group, now: datetime) -> datetime:
    """Return the earliest start of a direct call that can still be WAITING or CONNECTED."""
    waiting = timedelta(minutes=group.not_picked_up_minutes)
    connected = timedelta(minutes=group.slot_minutes) + JOIN_CLOSES_AFTER
    return now - max(waiting, connected)


def has_room(group: Group, calls: Iterable[DirectCall], now: datetime) -> bool:
    """Return whether another direct call fits under ``group``'s cap (no cap: always)."""
    limit = group.direct_call_max_concurrent
    if limit is None:
        return True
    taken = sum(1 for c in calls if phase(c, group, now) is not QueuePhase.DONE)
    return taken < limit


def typical_wait(calls: Iterable[DirectCall]) -> int | None:
    """Return the median time to a pickup of the latest ``SAMPLE_SIZE`` calls, in minutes.

    Rounded up to a whole minute and at least one; ``None`` below ``MIN_SAMPLES`` pickups.
    With an even number of pickups the upper middle one counts (rather too long than too short).
    """
    pickups = sorted(
        (
            (c.status.connected_at, c.status.connected_at - c.status.task_created_at)
            for c in calls
            if c.status.connected_at is not None and c.status.task_created_at is not None
        ),
        reverse=True,
    )[:SAMPLE_SIZE]
    if len(pickups) < MIN_SAMPLES:
        return None
    waits = sorted(wait for _, wait in pickups)
    median = waits[len(waits) // 2]
    return max(1, math.ceil(median.total_seconds() / 60))


def queue_info(
    calls: Sequence[DirectCall], group: Group, now: datetime, booking_id: int | None = None
) -> QueueInfo:
    """Return the queue as a citizen sees it, with the place of ``booking_id`` if it waits.

    Calls that started at the same moment are ordered by booking id.
    """
    waiting = sorted(
        (c.start, c.booking_id) for c in calls if phase(c, group, now) is QueuePhase.WAITING
    )
    ids = [booking for _, booking in waiting]
    position = ids.index(booking_id) + 1 if booking_id in ids else None
    return QueueInfo(len(waiting), position, typical_wait(calls))
