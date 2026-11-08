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
    hold proposal        as request rebook, plus a held place in the proposed slot until
                         confirm_by ─► jobs: proposal now, one reminder, expiry (ADR 0057)
    release proposal     the held place goes (confirmed, chosen, cancelled, expired)
    expire proposal      still open at confirm_by ─► released, "you can still choose" message
    lapse                still awaiting at rebook_by ─► cancelled, lapse message
    lapse unconfirmed    still pending at confirm_by ─► cancelled at confirm_by, lapse
                         message (ADR 0056; the participant's own lapse, kept beside the other)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.booking import Booking, BookingKind, ChangeReason, Slot
from app.domain.channels import ChannelKind, reachable
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import (
    plan_for_cancellation,
    plan_for_lapse,
    plan_for_proposal,
    plan_for_rebook,
    plan_for_released_proposal,
    plan_for_unconfirmed_lapse,
)
from app.domain.ports import Transaction
from app.domain.reschedule_proposal import (
    HeldProposal,
    ProposalOutcome,
    proposal_deadline,
    proposal_reminder,
)
from app.domain.scheduling import Group
from app.domain.usage import ActorClass
from app.domain.usage_recording import (
    record_cancelled,
    record_confirm_expired,
    record_rebook_expired,
    record_rebook_proposed,
)


@dataclass(frozen=True)
class ProposalTiming:
    """The organisation's periods for a proposal (ADR 0044 and 0057 settings, class D).

    Attributes:
        rebook_days: ``planner_rebook_days``: the request to choose lapses this long after the
            original start.
        rebook_reminder_days: ``planner_rebook_reminder_days``: its one reminder before the lapse.
        confirm_hours: ``reschedule_proposal_confirm_hours``: how long the place is held.
        reminder_hours: ``reschedule_proposal_reminder_hours``: the proposal's reminder before
            its deadline; 0 sends none.
    """

    rebook_days: int
    rebook_reminder_days: int
    confirm_hours: int
    reminder_hours: int


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
    if booking.contact_center_task is not None or now >= booking.start - lead:
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
    group: Group,
    now: datetime,
    *,
    actor: ActorClass,
    reason: ChangeReason | None = None,
    sms: bool = False,
    team_invite: bool = False,
) -> Booking:
    """Cancel ``booking`` with the cancellation message and the usual purge; return it.

    Args:
        tx: The caller's locked transaction.
        booking: A confirmed or suspended booking.
        group: Its group, for the usage fact.
        now: Current time.
        reason: The planner's fixed reason, shown in the message.
        sms: Also send the cancellation as an SMS when the participant is reachable there.
        team_invite: Withdraw the calendar invite of the group's team (ADR 0043).
        actor: Who cancels, as a class, for the usage facts (ADR 0117): the participant, the
            planner, or the closure when an admin cancels a booking on hold.

    Raises:
        DomainError: ``booking_not_active`` if it holds no place.
    """
    cancelled = booking.cancel(now, reason)
    await release_proposal(tx, booking, now, ProposalOutcome.CANCELLED)
    await tx.save_booking(cancelled)
    await record_cancelled(tx, booking, group, now, actor, reason=reason)
    jobs = plan_for_cancellation(
        cancelled_at=now, sms=sms and sms_reachable(booking), team_invite=team_invite
    )
    await tx.replace_jobs(_id(booking), jobs)
    return cancelled


async def request_rebook(
    tx: Transaction,
    booking: Booking,
    group: Group,
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
        group: Its group, for the usage fact.
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
    waiting = booking.request_rebook(reason, rebook_by, now)
    await tx.save_booking(waiting)
    await record_rebook_proposed(tx, waiting, group, now, ActorClass.PLANNER)
    jobs = plan_for_rebook(
        now=now,
        rebook_by=rebook_by,
        reminder_at=rebook_by - timedelta(days=reminder_days),
        sms=sms_reachable(booking),
        team_invite=team_invite,
    )
    await tx.replace_jobs(_id(booking), jobs)
    return waiting


async def hold_proposal(
    tx: Transaction,
    booking: Booking,
    group: Group,
    slot: Slot,
    now: datetime,
    *,
    reason: ChangeReason,
    timing: ProposalTiming,
    team_invite: bool = False,
) -> HeldProposal:
    """Propose ``slot`` as the new time of ``booking`` and hold a place there (ADR 0057 point 7).

    The booking awaits a new time as after ``request_rebook``, so its own place is free at once;
    the proposal holds one place in ``slot`` until its deadline. The caller has checked that the
    slot is offered and has room (``BookingService.propose_in``).

    Raises:
        DomainError: ``booking_not_active`` if it is not confirmed.
    """
    rebook_by = booking.start + timedelta(days=timing.rebook_days)
    waiting = booking.request_rebook(reason, rebook_by, now)
    await tx.save_booking(waiting)
    await record_rebook_proposed(tx, waiting, group, now, ActorClass.PLANNER)
    confirm_by = proposal_deadline(now, timing.confirm_hours, group.change_deadline(slot.start))
    proposal = await tx.add_proposal(
        HeldProposal(None, _id(booking), _slot_id(slot), slot.start, slot.end, confirm_by, now)
    )
    jobs = plan_for_proposal(
        now=now,
        rebook_by=rebook_by,
        rebook_reminder_at=rebook_by - timedelta(days=timing.rebook_reminder_days),
        proposed_start=slot.start,
        confirm_by=confirm_by,
        proposal_reminder_at=proposal_reminder(now, confirm_by, timing.reminder_hours),
        sms=sms_reachable(booking),
        team_invite=team_invite,
    )
    await tx.replace_jobs(_id(booking), jobs)
    return proposal


async def release_proposal(
    tx: Transaction, booking: Booking, now: datetime, outcome: ProposalOutcome
) -> HeldProposal | None:
    """Release the booking's open proposal with ``outcome``; return it, or ``None`` if none."""
    if booking.id is None or not booking.awaits_rebook:
        return None
    proposal = await tx.get_open_proposal(booking.id)
    if proposal is None:
        return None
    released = proposal.released(now, outcome)
    await tx.save_proposal(released)
    return released


async def expire_proposal(tx: Transaction, booking: Booking, now: datetime) -> bool:
    """Release a proposal nobody answered by its deadline; return whether one expired.

    The booking keeps awaiting a new time until the lapse of ADR 0044; the participant gets
    the short message that the proposed time was released and that they can still choose
    (ADR 0057 point 9). A proposal answered, withdrawn or replaced in time is left alone.
    """
    if not booking.awaits_rebook or booking.id is None:
        return False
    proposal = await tx.get_open_proposal(booking.id)
    if proposal is None or not proposal.is_due(now):
        return False
    await tx.save_proposal(proposal.released(now, ProposalOutcome.EXPIRED))
    await tx.add_jobs(booking.id, plan_for_released_proposal(now=now, sms=sms_reachable(booking)))
    return True


async def lapse(tx: Transaction, booking: Booking, now: datetime) -> bool:
    """Cancel ``booking`` if it still awaits a new time; return whether it lapsed.

    The participant gets the lapse message; the purge planned with the request stays.
    """
    if not booking.awaits_rebook:
        return False
    await release_proposal(tx, booking, now, ProposalOutcome.EXPIRED)
    await tx.save_booking(booking.cancel(now))
    group = await _group(tx, booking)
    await record_cancelled(tx, booking, group, now, ActorClass.SYSTEM)
    await record_rebook_expired(tx, booking, group, now)
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
    await record_confirm_expired(tx, booking, await _group(tx, booking), now)
    await tx.add_jobs(_id(booking), plan_for_unconfirmed_lapse(now=now))
    return True


async def _group(tx: Transaction, booking: Booking) -> Group:
    """Return the booking's group; a booking without one is a programming error."""
    group = await tx.get_group_by_id(booking.group_id)
    if group is None:
        raise ValueError("booking has no group")
    return group


def _slot_id(slot: Slot) -> int:
    """Return the id of a stored slot; a missing id is a programming error."""
    if slot.id is None:
        raise ValueError("slot has not been stored")
    return slot.id


def _id(booking: Booking) -> int:
    """Return the id of a stored booking; a missing id is a programming error."""
    if booking.id is None:
        raise ValueError("booking has not been stored")
    return booking.id
