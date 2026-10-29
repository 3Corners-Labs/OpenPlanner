"""Changes staff make to existing bookings, shared by the closure and the planner (ADR 0044).

The emergency closure (ADR 0024) and the planner both cancel bookings on the citizen's
behalf; the citizen is always told. Moving is ``BookingService.relocate``, shared with the
citizen's own reschedule. This module holds the steps both callers run inside their own
locked transaction, and the planner's limits:

    planner may change?  confirmed, scheduled, not dispatched, starts after the lead
    cancel               status cancelled (+ reason) ─► jobs: end task, delete meeting,
                         cancellation mail (and SMS when reachable), purge
"""

from datetime import datetime

from app.domain.booking import Booking, BookingKind, ChangeReason
from app.domain.channels import ChannelKind, reachable
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import plan_for_cancellation
from app.domain.ports import Transaction
from app.domain.scheduling import Group


def ensure_planner_may_change(booking: Booking, group: Group, now: datetime) -> None:
    """Refuse a planner's change that needs a phone call instead of a message.

    A booking can be changed while it is confirmed, scheduled (not a direct call), not yet
    dispatched to the contact center, and starts more than the group's planner lead from now
    (never less than the dispatch lead).

    Raises:
        DomainError: ``booking_not_active`` or ``planner_too_late``.
    """
    if not booking.is_active or booking.kind is not BookingKind.SCHEDULED:
        raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
    lead = max(group.planner_lead, group.dispatch_lead)
    if booking.wxcc_task_id is not None or now >= booking.start - lead:
        raise DomainError(ErrorCode.PLANNER_TOO_LATE)


def sms_reachable(booking: Booking) -> bool:
    """Return whether the citizen can get an SMS, as for reminders (ADR 0022)."""
    contact = booking.contact
    return contact is not None and reachable(ChannelKind.SMS, contact, booking.consents)


async def cancel(
    tx: Transaction,
    booking: Booking,
    now: datetime,
    *,
    reason: ChangeReason | None = None,
    sms: bool = False,
) -> Booking:
    """Cancel ``booking`` with the cancellation message and the usual purge; return it.

    Args:
        tx: The caller's locked transaction.
        booking: A confirmed or suspended booking.
        now: Current time.
        reason: The planner's fixed reason, shown in the message.
        sms: Also send the cancellation as an SMS when the citizen is reachable there.

    Raises:
        DomainError: ``booking_not_active`` if it holds no place.
    """
    cancelled = booking.cancel(now, reason)
    await tx.save_booking(cancelled)
    jobs = plan_for_cancellation(cancelled_at=now, sms=sms and sms_reachable(booking))
    await tx.replace_jobs(_id(booking), jobs)
    return cancelled


def _id(booking: Booking) -> int:
    """Return the id of a stored booking; a missing id is a programming error."""
    if booking.id is None:
        raise ValueError("booking has not been stored")
    return booking.id
