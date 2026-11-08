"""Writes the usage facts of a booking's state changes (ADR 0117 point 1, step 2).

Each state change of a booking calls one function here inside its own locked transaction, so a
fact is written together with the change it describes (``Transaction.record_usage`` runs in a
savepoint and never fails the participant's action). The facts are built by
``booking_facts`` from values the service already holds: the booking's times, its kind and
locale, and the group's organisation and dispatch kind. Nothing reads a purged column, and
nothing but the allow-listed fields of ``usage.UsageFact`` leaves this module.

    placed     ─► ``booking.created`` (creation hour) + ``booking.slotted`` (slot start)
    confirmed  ─► ``booking.confirmed``
    cancelled  ─► ``booking.cancelled`` (slot start; lead time; who, as a class)
    lapsed     ─► ``booking.confirm_expired`` (slot start)
    moved      ─► ``booking.moved`` (the new booking is a ``booking.created`` with reason ``move``)
    suspended / resumed by a closure ─► ``booking.suspended`` / ``booking.resumed``

    rebooking  ─► ``rebook.proposed`` / ``rebook.accepted`` / ``rebook.expired`` (metric U8)

How a booking ended ``completed`` or ``no_show`` is not written here: nothing in the
application sets those statuses, so ``slot_close`` derives the ``booking.outcome`` fact from the
call after the slot has ended (ADR 0117 step 3).
"""

from datetime import datetime, timedelta

from app.domain import booking_facts
from app.domain.booking import Booking, ChangeReason
from app.domain.booking_facts import BookingFacts
from app.domain.ports import Transaction
from app.domain.scheduling import Group
from app.domain.usage import ActorClass, FactKind


def facts_of_booking(booking: Booking, group: Group) -> BookingFacts:
    """Describe ``booking`` in ``group`` for the fact builders: no personal data."""
    return BookingFacts(
        organisation_id=group.organisation_id,
        group_id=group.id,
        booking_kind=booking.kind,
        dispatch_kind=group.dispatch_kind,
        locale=booking.locale,
        created_at=booking.created_at,
        start=booking.start,
        change_reason=booking.change_reason.value if booking.change_reason is not None else None,
    )


async def record_placed(
    tx: Transaction, booking: Booking, group: Group, *, moved: bool = False
) -> None:
    """Record a booking placed in a slot: ``booking.created`` and ``booking.slotted``.

    ``moved`` marks the booking that replaces a moved one, so *created* counts never include
    moves (metric U1).
    """
    b = facts_of_booking(booking, group)
    await tx.record_usage(booking_facts.created(b, moved=moved))
    await tx.record_usage(booking_facts.slotted(b))


async def record_confirmed(tx: Transaction, booking: Booking, group: Group, now: datetime) -> None:
    """Record a confirmation by the participant, with the seconds from creation (metric U9)."""
    await tx.record_usage(booking_facts.confirmed(facts_of_booking(booking, group), now))


async def record_cancelled(
    tx: Transaction,
    booking: Booking,
    group: Group,
    now: datetime,
    actor_class: ActorClass,
    *,
    reason: ChangeReason | None = None,
) -> None:
    """Record a cancellation by ``actor_class``, with the quarter hours left before the start."""
    fact = booking_facts.cancelled(
        facts_of_booking(booking, group),
        now,
        actor_class,
        reason.value if reason is not None else None,
    )
    await tx.record_usage(fact)


async def record_confirm_expired(
    tx: Transaction, booking: Booking, group: Group, now: datetime
) -> None:
    """Record a booking that was never confirmed in time (metrics U2 and U9)."""
    await tx.record_usage(booking_facts.confirm_expired(facts_of_booking(booking, group), now))


async def record_moved(
    tx: Transaction, booking: Booking, group: Group, now: datetime, actor_class: ActorClass
) -> None:
    """Record a booking moved to another slot (metric U7), with the planner's reason if any."""
    await tx.record_usage(booking_facts.moved(facts_of_booking(booking, group), now, actor_class))


async def record_closure_change(
    tx: Transaction, kind: FactKind, booking: Booking, group: Group, now: datetime
) -> None:
    """Record a booking suspended or resumed by an emergency closure (metric U10)."""
    fact = booking_facts.closure_change(kind, facts_of_booking(booking, group), now)
    await tx.record_usage(fact)


async def record_rebook_proposed(
    tx: Transaction, booking: Booking, group: Group, now: datetime, actor_class: ActorClass
) -> None:
    """Record a planner's request for a new time (metric U8); ``booking`` is the waiting one."""
    await tx.record_usage(
        booking_facts.rebook_proposed(facts_of_booking(booking, group), now, actor_class)
    )


async def record_rebook_accepted(
    tx: Transaction, booking: Booking, group: Group, now: datetime
) -> None:
    """Record the participant's choice of a new time, with the time it took (metric U8)."""
    fact = booking_facts.rebook_accepted(
        facts_of_booking(booking, group), now, booking.rebook_requested_at
    )
    await tx.record_usage(fact)


async def record_rebook_expired(
    tx: Transaction, booking: Booking, group: Group, now: datetime
) -> None:
    """Record a request for a new time that lapsed (metric U8)."""
    await tx.record_usage(booking_facts.rebook_expired(facts_of_booking(booking, group), now))


async def record_queue_claimed(
    tx: Transaction, booking: Booking, group: Group, now: datetime
) -> None:
    """Record a claim in the own queue (metric Q7).

    The queue shows a booking from ``queue_visible_from_minutes`` before its start, or from the
    moment it was made if that is later; the fact carries the seconds from then to the claim.
    """
    visible = max(
        booking.created_at, booking.start - timedelta(minutes=group.queue_visible_from_minutes)
    )
    await tx.record_usage(
        booking_facts.queue_claimed(facts_of_booking(booking, group), now, visible)
    )


async def record_queue_released(
    tx: Transaction, booking: Booking, group: Group, now: datetime
) -> None:
    """Record a claimed booking put back in the queue (metric Q7)."""
    await tx.record_usage(booking_facts.queue_released(facts_of_booking(booking, group), now))
