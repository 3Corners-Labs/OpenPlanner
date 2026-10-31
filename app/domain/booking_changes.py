"""Changes staff make to existing bookings, shared by the closure and the planner (ADR 0044).

The emergency closure (ADR 0024) and the planner both cancel bookings on the participant's
behalf; the participant is always told. Moving is ``BookingService.relocate``, shared with the
participant's own reschedule. This module holds the steps both callers run inside their own
locked transaction, and the planner's limits:

    planner may change?  confirmed, scheduled, not dispatched, starts after the lead
    cancel               status cancelled (+ reason) ─► jobs: end task, delete meeting,
                         cancellation mail (and SMS when reachable), purge
    request rebook       status awaiting_rebook (+ reason, rebook_by), place freed ─► jobs:
                         end task, delete meeting, request now, one reminder, lapse at
                         rebook_by, purge 24 h later (ADR 0044)
    lapse                still awaiting at rebook_by ─► cancelled, lapse message
    lapse unconfirmed    still pending at confirm_by ─► cancelled at confirm_by, lapse
                         message (ADR 0056; the participant's own lapse, kept beside the other)
"""

from datetime import datetime, timedelta

from app.domain.booking import Booking, BookingKind, ChangeReason
from app.domain.channels import ChannelKind, reachable
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import (
    plan_for_cancellation,
    plan_for_lapse,
    plan_for_rebook,
    plan_for_unconfirmed_lapse,
)
from app.domain.ports import Transaction
from app.domain.scheduling import Group


def ensure_planner_may_change(booking: Booking, group: Group, now: datetime) -> None:
    """Refuse a planner's change that needs a phone call instead of a message.

    A booking can be changed while it is confirmed, scheduled (not a direct call), not yet
    dispatched to the contact center, and starts more than the group's planner lead from now
    (never less than the dispatch lead). A booking awaiting a new time can always be moved or
    cancelled: it does not go ahead at its time anyway.

    Raises:
        DomainError: ``booking_not_active`` or ``planner_too_late``.
    """
    if booking.kind is not BookingKind.SCHEDULED:
        raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
    if booking.awaits_rebook:
        return
    if not booking.is_active:
        raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
    lead = max(group.planner_lead, group.dispatch_lead)
    if booking.wxcc_task_id is not None or now >= booking.start - lead:
        raise DomainError(ErrorCode.PLANNER_TOO_LATE)


def planner_may_change(booking: Booking, group: Group, now: datetime) -> bool:
    """Return whether a planner may still change ``booking`` now (for links and lists)."""
    try:
        ensure_planner_may_change(booking, group, now)
    except DomainError:
        return False
    return True


def sms_reachable(booking: Booking) -> bool:
    """Return whether the participant can get an SMS, as for reminders (ADR 0022)."""
    contact = booking.contact
    return contact is not None and reachable(ChannelKind.SMS, contact, booking.consents)


async def cancel(
    tx: Transaction,
    booking: Booking,
    now: datetime,
    *,
    reason: ChangeReason | None = None,
    sms: bool = False,
    team_invite: bool = False,
) -> Booking:
    """Cancel ``booking`` with the cancellation message and the usual purge; return it.

    Args:
        tx: The caller's locked transaction.
        booking: A confirmed or suspended booking.
        now: Current time.
        reason: The planner's fixed reason, shown in the message.
        sms: Also send the cancellation as an SMS when the participant is reachable there.
        team_invite: Withdraw the calendar invite of the group's team (ADR 0043).

    Raises:
        DomainError: ``booking_not_active`` if it holds no place.
    """
    cancelled = booking.cancel(now, reason)
    await tx.save_booking(cancelled)
    jobs = plan_for_cancellation(
        cancelled_at=now, sms=sms and sms_reachable(booking), team_invite=team_invite
    )
    await tx.replace_jobs(_id(booking), jobs)
    return cancelled


async def request_rebook(
    tx: Transaction,
    booking: Booking,
    now: datetime,
    *,
    reason: ChangeReason,
    rebook_days: int,
    reminder_days: int,
    team_invite: bool = False,
) -> Booking:
    """Ask the participant to choose a new time for ``booking``; return it (ADR 0044).

    Args:
        tx: The caller's locked transaction.
        booking: A confirmed booking.
        now: Current time.
        reason: The planner's fixed reason, shown in the messages.
        rebook_days: ``planner_rebook_days``: the request lapses this long after the start.
        reminder_days: ``planner_rebook_reminder_days``: the one reminder goes this long
            before the request lapses.
        team_invite: Withdraw the calendar invite of the group's team (ADR 0043).

    Raises:
        DomainError: ``booking_not_active`` if it is not confirmed.
    """
    rebook_by = booking.start + timedelta(days=rebook_days)
    waiting = booking.request_rebook(reason, rebook_by)
    await tx.save_booking(waiting)
    jobs = plan_for_rebook(
        now=now,
        rebook_by=rebook_by,
        reminder_at=rebook_by - timedelta(days=reminder_days),
        sms=sms_reachable(booking),
        team_invite=team_invite,
    )
    await tx.replace_jobs(_id(booking), jobs)
    return waiting


async def lapse(tx: Transaction, booking: Booking, now: datetime) -> bool:
    """Cancel ``booking`` if it still awaits a new time; return whether it lapsed.

    The participant gets the lapse message; the purge planned with the request stays.
    """
    if not booking.awaits_rebook:
        return False
    await tx.save_booking(booking.cancel(now))
    await tx.add_jobs(_id(booking), plan_for_lapse(now=now, sms=sms_reachable(booking)))
    return True


async def lapse_unconfirmed(tx: Transaction, booking: Booking, now: datetime) -> bool:
    """Cancel ``booking`` if it still awaits confirmation; return whether it lapsed (ADR 0056).

    Run by ``expire_unconfirmed`` at ``confirm_by``, and by a confirmation that comes too
    late. The place is freed and the participant gets the short lapse message; the purge planned
    with the request to confirm stays.
    """
    if not booking.awaits_confirmation:
        return False
    await tx.save_booking(booking.expire(now))
    await tx.add_jobs(_id(booking), plan_for_unconfirmed_lapse(now=now))
    return True


def _id(booking: Booking) -> int:
    """Return the id of a stored booking; a missing id is a programming error."""
    if booking.id is None:
        raise ValueError("booking has not been stored")
    return booking.id
