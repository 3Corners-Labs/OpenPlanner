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

A booking that is completed or went unattended has no producer yet: nothing in the application
sets ``completed`` or ``no_show`` (ADR 0117 build status, step 3 derives them from the call).
"""

from datetime import datetime

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


async def record_confirmed(
    tx: Transaction,
    booking: Booking,
    group: Group,
    now: datetime,
    actor: ActorClass = ActorClass.PARTICIPANT,
) -> None:
    """Record a confirmation, with the seconds from creation (metric U9), and who confirmed."""
    await tx.record_usage(booking_facts.confirmed(facts_of_booking(booking, group), now, actor))


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
