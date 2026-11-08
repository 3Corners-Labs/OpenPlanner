"""The usage fact of each booking transition, built in one place (ADR 0117 points 2 and 5).

Live code (``usage_recording``, which has a ``Booking`` and its ``Group``) and the backfill
(``usage_backfill``, which has the surviving columns of an old row) both describe a booking
as a ``BookingFacts`` and ask these builders for the fact, with the time the transition
happened. So a rule - which hour a fact is keyed on, how a lead time is rounded, what a move
carries - exists once, and the two paths cannot drift apart.

    placed ─► ``created`` (creation hour) + ``slotted`` (slot start)
    ``confirmed``, ``cancelled``, ``confirm_expired``, ``moved``, ``closure_change``
"""

from dataclasses import dataclass
from datetime import datetime

from app.domain.booking import BookingKind
from app.domain.scheduling import DispatchKind
from app.domain.usage import (
    MOVE_REASON,
    ActorClass,
    FactKind,
    UsageFact,
    UsageOutcome,
    cancelled_outcome,
    coarse,
    hour_of,
    usable_locale,
)


@dataclass(frozen=True)
class BookingFacts:
    """What the fact builders need to know about a booking: no personal data, no booking id.

    Attributes:
        organisation_id: The group's organisation.
        group_id: The group.
        booking_kind: Scheduled or direct.
        dispatch_kind: How the group's bookings reach staff.
        locale: The participant's language as stored; a value that is no language tag is left
            out of the fact.
        created_at: When the booking was made.
        start: The slot start.
        change_reason: The planner's fixed reason, if one asked for a change.
    """

    organisation_id: int
    group_id: int
    booking_kind: BookingKind
    dispatch_kind: DispatchKind
    locale: str | None
    created_at: datetime
    start: datetime
    change_reason: str | None = None


def _fact(kind: FactKind, b: BookingFacts, at: datetime, **extra: object) -> UsageFact:
    """Return a fact of ``kind`` keyed on ``at``, with the booking's group, kind and locale."""
    return UsageFact(
        kind,
        hour_of(at),
        b.organisation_id,
        b.group_id,
        booking_kind=b.booking_kind,
        dispatch_kind=b.dispatch_kind,
        locale=usable_locale(b.locale),
        **extra,  # type: ignore[arg-type]
    )


def created(b: BookingFacts, *, moved: bool = False) -> UsageFact:
    """A booking placed; ``moved`` marks the one that replaces a moved booking.

    The lead time is the seconds from creation to the slot start (metric U6).
    """
    lead = max(int((b.start - b.created_at).total_seconds()), 0)
    return _fact(
        FactKind.BOOKING_CREATED,
        b,
        b.created_at,
        reason_code=MOVE_REASON if moved else None,
        value_seconds=coarse(lead),
    )


def slotted(b: BookingFacts) -> UsageFact:
    """A booking in a slot, keyed on the slot start (the busiest hours, metric C4)."""
    return _fact(FactKind.BOOKING_SLOTTED, b, b.start)


def confirmed(b: BookingFacts, at: datetime) -> UsageFact:
    """A confirmation by the participant, with the time it took (metric U9)."""
    held = max(int((at - b.created_at).total_seconds()), 0)
    return _fact(
        FactKind.BOOKING_CONFIRMED,
        b,
        at,
        actor_class=ActorClass.PARTICIPANT,
        value_seconds=coarse(held),
    )


def cancelled(b: BookingFacts, at: datetime, actor: ActorClass, reason: str | None) -> UsageFact:
    """A cancellation by ``actor`` at ``at``, with the quarter hours left before the start.

    The lead time is signed: a booking cancelled after its start has a negative one.
    """
    return _fact(
        FactKind.BOOKING_CANCELLED,
        b,
        b.start,
        actor_class=actor,
        outcome=cancelled_outcome(actor),
        reason_code=reason,
        value_seconds=coarse(int((b.start - at).total_seconds())),
    )


def confirm_expired(b: BookingFacts, at: datetime) -> UsageFact:
    """A booking never confirmed in time (metrics U2 and U9), with how long it was held."""
    return _fact(
        FactKind.BOOKING_CONFIRM_EXPIRED,
        b,
        b.start,
        actor_class=ActorClass.SYSTEM,
        outcome=UsageOutcome.EXPIRED_UNCONFIRMED,
        value_seconds=coarse(int((at - b.created_at).total_seconds())),
    )


def moved(b: BookingFacts, at: datetime, actor: ActorClass | None) -> UsageFact:
    """A booking moved to another slot (metric U7); the actor is ``None`` when it is not known."""
    return _fact(FactKind.BOOKING_MOVED, b, at, actor_class=actor, reason_code=b.change_reason)


def closure_change(kind: FactKind, b: BookingFacts, at: datetime) -> UsageFact:
    """A booking suspended or resumed by an emergency closure (metric U10)."""
    return _fact(kind, b, at, actor_class=ActorClass.CLOSURE)
