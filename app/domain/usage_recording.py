"""Writes the usage facts of a booking's state changes (ADR 0117 point 1, step 2).

Each state change of a booking calls one function here inside its own locked transaction, so a
fact is written together with the change it describes (``Transaction.record_usage`` runs in a
savepoint and never fails the participant's action). The facts are built from values the
service already holds: the booking's times, its kind and locale, and the group's organisation
and dispatch kind. Nothing reads a purged column, and nothing but the allow-listed fields of
``usage.UsageFact`` leaves this module.

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

from app.domain.booking import Booking, ChangeReason
from app.domain.ports import Transaction
from app.domain.scheduling import Group
from app.domain.usage import (
    FACT_HOUR,
    ActorClass,
    FactKind,
    HourSource,
    UsageFact,
    UsageOutcome,
    hour_of,
    usable_locale,
)

MOVE_REASON = "move"
"""The ``reason_code`` of a ``booking.created`` fact that replaces a moved booking."""

_CANCELLED_BY = {
    ActorClass.PARTICIPANT: UsageOutcome.CANCELLED_BY_PARTICIPANT,
    ActorClass.PLANNER: UsageOutcome.CANCELLED_BY_PLANNER,
    ActorClass.CLOSURE: UsageOutcome.CANCELLED_BY_CLOSURE,
    ActorClass.SYSTEM: UsageOutcome.CANCELLED_BY_SYSTEM,
}


def _moment(kind: FactKind, booking: Booking, now: datetime) -> datetime:
    """Return the instant ``kind`` is keyed on: creation, slot start, or the event itself."""
    source = FACT_HOUR[kind]
    if source is HourSource.CREATION:
        return booking.created_at or now
    return booking.start if source is HourSource.SLOT_START else now


def booking_fact(
    kind: FactKind,
    booking: Booking,
    group: Group,
    now: datetime,
    *,
    actor_class: ActorClass | None = None,
    outcome: UsageOutcome | None = None,
    reason_code: str | None = None,
    value_seconds: int | None = None,
) -> UsageFact:
    """Return the fact ``kind`` for ``booking`` with the codes of its group and locale."""
    return UsageFact(
        kind,
        hour_of(_moment(kind, booking, now)),
        group.organisation_id,
        group.id,
        booking_kind=booking.kind,
        dispatch_kind=group.dispatch_kind,
        locale=usable_locale(booking.locale),
        actor_class=actor_class,
        outcome=outcome,
        reason_code=reason_code,
        value_seconds=value_seconds,
    )


async def _group_of(tx: Transaction, booking: Booking, group: Group | None) -> Group | None:
    """Return ``group``, or the booking's group when the caller does not hold it."""
    return group if group is not None else await tx.get_group_by_id(booking.group_id)


async def _record(tx: Transaction, fact: UsageFact | None) -> None:
    """Write ``fact`` unless the group is gone."""
    if fact is not None:
        await tx.record_usage(fact)


async def record_placed(
    tx: Transaction, booking: Booking, group: Group, now: datetime, *, moved: bool = False
) -> None:
    """Record a booking placed in a slot: ``booking.created`` and ``booking.slotted``.

    ``moved`` marks the booking that replaces a moved one: ``created`` carries the reason
    ``move`` so *created* counts never include moves (metric U1). The lead time is the
    seconds from creation to the slot start (metric U6).
    """
    lead = int((booking.start - (booking.created_at or now)).total_seconds())
    created = booking_fact(
        FactKind.BOOKING_CREATED,
        booking,
        group,
        now,
        reason_code=MOVE_REASON if moved else None,
        value_seconds=max(lead, 0),
    )
    await tx.record_usage(created)
    await tx.record_usage(booking_fact(FactKind.BOOKING_SLOTTED, booking, group, now))


async def record_confirmed(tx: Transaction, booking: Booking, group: Group, now: datetime) -> None:
    """Record a confirmation by the participant, with the seconds from creation (metric U9)."""
    held = int((now - (booking.created_at or now)).total_seconds())
    fact = booking_fact(
        FactKind.BOOKING_CONFIRMED,
        booking,
        group,
        now,
        actor_class=ActorClass.PARTICIPANT,
        value_seconds=max(held, 0),
    )
    await tx.record_usage(fact)


async def record_cancelled(
    tx: Transaction,
    booking: Booking,
    now: datetime,
    actor_class: ActorClass,
    *,
    group: Group | None = None,
    reason: ChangeReason | None = None,
) -> None:
    """Record a cancellation by ``actor_class``, with the seconds left before the start.

    The lead time is signed: a booking cancelled after its start (a lapse) has a negative one.
    """
    found = await _group_of(tx, booking, group)
    if found is None:
        return
    fact = booking_fact(
        FactKind.BOOKING_CANCELLED,
        booking,
        found,
        now,
        actor_class=actor_class,
        outcome=_CANCELLED_BY[actor_class],
        reason_code=reason.value if reason is not None else None,
        value_seconds=int((booking.start - now).total_seconds()),
    )
    await _record(tx, fact)


async def record_confirm_expired(tx: Transaction, booking: Booking, now: datetime) -> None:
    """Record a booking that was never confirmed in time (metrics U2 and U9)."""
    found = await _group_of(tx, booking, None)
    if found is None:
        return
    fact = booking_fact(
        FactKind.BOOKING_CONFIRM_EXPIRED,
        booking,
        found,
        now,
        actor_class=ActorClass.SYSTEM,
        outcome=UsageOutcome.EXPIRED_UNCONFIRMED,
        value_seconds=int((now - (booking.created_at or now)).total_seconds()),
    )
    await _record(tx, fact)


async def record_moved(
    tx: Transaction, booking: Booking, group: Group, now: datetime, actor_class: ActorClass
) -> None:
    """Record a booking moved to another slot (metric U7), with the planner's reason if any."""
    fact = booking_fact(
        FactKind.BOOKING_MOVED,
        booking,
        group,
        now,
        actor_class=actor_class,
        reason_code=booking.change_reason.value if booking.change_reason is not None else None,
    )
    await tx.record_usage(fact)


async def record_closure_change(
    tx: Transaction, kind: FactKind, booking: Booking, group: Group, now: datetime
) -> None:
    """Record a booking suspended or resumed by an emergency closure (metric U10)."""
    await tx.record_usage(booking_fact(kind, booking, group, now, actor_class=ActorClass.CLOSURE))
