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


def keyed(kind: FactKind, b: BookingFacts, at: datetime, **extra: object) -> UsageFact:
    """Return a fact of ``kind`` keyed on ``at``, with the booking's group, kind and locale.

    Public for the other fact builders (``call_facts``, ``queue_facts``), so the group, the kind
    and the locale of every fact come from one place.
    """
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
    return keyed(
        FactKind.BOOKING_CREATED,
        b,
        b.created_at,
        reason_code=MOVE_REASON if moved else None,
        value_seconds=coarse(lead),
    )


def slotted(b: BookingFacts) -> UsageFact:
    """A booking in a slot, keyed on the slot start (the busiest hours, metric C4)."""
    return keyed(FactKind.BOOKING_SLOTTED, b, b.start)


def confirmed(
    b: BookingFacts, at: datetime, actor: ActorClass = ActorClass.PARTICIPANT
) -> UsageFact:
    """A confirmation, with the time it took (metric U9).

    By the participant, or by a planner for them (ADR 0056 point 11), told apart by ``actor``.
    """
    held = max(int((at - b.created_at).total_seconds()), 0)
    return keyed(
        FactKind.BOOKING_CONFIRMED,
        b,
        at,
        actor_class=actor,
        value_seconds=coarse(held),
    )


def cancelled(b: BookingFacts, at: datetime, actor: ActorClass, reason: str | None) -> UsageFact:
    """A cancellation by ``actor`` at ``at``, with the quarter hours left before the start.

    The lead time is signed: a booking cancelled after its start has a negative one.
    """
    return keyed(
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
    return keyed(
        FactKind.BOOKING_CONFIRM_EXPIRED,
        b,
        b.start,
        actor_class=ActorClass.SYSTEM,
        outcome=UsageOutcome.EXPIRED_UNCONFIRMED,
        value_seconds=coarse(int((at - b.created_at).total_seconds())),
    )


def moved(b: BookingFacts, at: datetime, actor: ActorClass | None) -> UsageFact:
    """A booking moved to another slot (metric U7); the actor is ``None`` when it is not known."""
    return keyed(FactKind.BOOKING_MOVED, b, at, actor_class=actor, reason_code=b.change_reason)


def closure_change(kind: FactKind, b: BookingFacts, at: datetime) -> UsageFact:
    """A booking suspended or resumed by an emergency closure (metric U10)."""
    return keyed(kind, b, at, actor_class=ActorClass.CLOSURE)


def rebook_proposed(b: BookingFacts, at: datetime, actor: ActorClass) -> UsageFact:
    """A new time was asked of the participant (metric U8): by a planner, or by the assistant.

    The reason is the planner's fixed reason (``ChangeReason``), a closed list.
    """
    return keyed(FactKind.REBOOK_PROPOSED, b, at, actor_class=actor, reason_code=b.change_reason)


def rebook_accepted(b: BookingFacts, at: datetime, requested_at: datetime | None) -> UsageFact:
    """The participant chose a new time (metric U8), with the quarter hours it took.

    ``requested_at`` is unknown for a request made before it was recorded: no time then.
    """
    took = (
        None if requested_at is None else coarse(max(int((at - requested_at).total_seconds()), 0))
    )
    return keyed(
        FactKind.REBOOK_ACCEPTED,
        b,
        at,
        actor_class=ActorClass.PARTICIPANT,
        reason_code=b.change_reason,
        value_seconds=took,
    )


def rebook_expired(b: BookingFacts, at: datetime) -> UsageFact:
    """The request lapsed without a new time (metric U8)."""
    return keyed(
        FactKind.REBOOK_EXPIRED, b, at, actor_class=ActorClass.SYSTEM, reason_code=b.change_reason
    )


def queue_claimed(b: BookingFacts, at: datetime, visible_from: datetime) -> UsageFact:
    """An agent of the own queue claimed a booking, with the seconds since it became visible.

    No agent, not even a pseudonym, is in the fact: a team member's own figures are a
    ``StaffFact`` that step 7 writes while an organisation has switched the team view on.
    """
    waited = max(int((at - visible_from).total_seconds()), 0)
    return keyed(FactKind.QUEUE_CLAIMED, b, at, value_seconds=waited)


def queue_released(b: BookingFacts, at: datetime) -> UsageFact:
    """A claimed booking was put back in the queue (metric Q7)."""
    return keyed(FactKind.QUEUE_RELEASED, b, at)
