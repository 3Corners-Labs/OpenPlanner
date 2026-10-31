"""A participant stops waiting for a direct call (ADR 0019 point 4, ADR 0021).

Leaving cancels the direct booking, which frees its place under the group's cap (ADR 0026), and
ends the work item through the same ``end_wxcc_task`` job as any cancellation. Nothing else is
sent: the participant did it themselves and the waiting page says what happened, so no cancellation
mail. If an agent already took the call (a connected task, a claim in the own queue, or the
host in the meeting) the meeting is *not* deleted: the agent is not cut off mid-sentence, and
the meeting runs out by itself. The participant's link stops working because the booking is
cancelled.

Pure Python, no I/O; ``BookingService.leave_direct`` stores the result in its locked transaction.
"""

from dataclasses import dataclass
from datetime import datetime

from app.domain.booking import Booking
from app.domain.calls import BookingEvent, CallStatus, EventKind, TaskState
from app.domain.job_plan import JobType, PlannedJob, plan_for_cancellation

DETAIL_WAITING = "waiting"
"""Timeline detail of a participant who left before an agent came."""
DETAIL_AGENT = "agent"
"""Timeline detail of a participant who left after an agent accepted or joined."""


@dataclass(frozen=True)
class DirectLeft:
    """What leaving a direct call did.

    Attributes:
        booking: The booking as it is now (cancelled, or unchanged when it was not active).
        changed: Whether this request cancelled it; ``False`` for a repeated request.
        agent_present: Whether an agent had taken the call, so the meeting was left alone.
    """

    booking: Booking
    changed: bool
    agent_present: bool = False


@dataclass(frozen=True)
class LeavePlan:
    """What to store when a participant leaves: the cancelled booking, its jobs and the event."""

    booking: Booking
    jobs: list[PlannedJob]
    event: BookingEvent
    agent_present: bool


def agent_took_the_call(booking: Booking, status: CallStatus) -> bool:
    """Return whether an agent accepted ``booking``'s call or is in its meeting."""
    return status.task is TaskState.CONNECTED or status.host_joined or booking.claim is not None


def plan_leave(booking: Booking, status: CallStatus, now: datetime) -> LeavePlan:
    """Return what leaving ``booking`` stores at ``now``.

    Raises:
        DomainError: ``booking_not_active`` if the booking is not confirmed any more.
    """
    present = agent_took_the_call(booking, status)
    jobs = plan_for_cancellation(cancelled_at=now, notify=False)
    if present:
        jobs = [job for job in jobs if job.type is not JobType.DELETE_VIDEO_ROOM]
    detail = DETAIL_AGENT if present else DETAIL_WAITING
    event = BookingEvent(booking.id or 0, now, EventKind.CITIZEN_LEFT, detail)
    return LeavePlan(booking.cancel(now), jobs, event, present)
