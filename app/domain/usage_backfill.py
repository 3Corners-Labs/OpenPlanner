"""Usage facts for bookings that existed before reporting did (ADR 0117 point 5, step 4).

``python -m app.cli usage-backfill``, and the worker's first start after the upgrade, turn the
booking rows that still exist into the facts live code would have written, from the columns the
purge leaves (never one of ``privacy.PURGED_FIELDS``). What a row cannot say is left out rather
than invented:

- how long a call lasted, the queue method and who waited: not on the row (they start on the
  day the release is installed);
- when a booking was suspended or resumed: no time is stored, so no fact is made;
- who cancelled: derived (a planner's reason means the planner, a closure means the closure,
  else the participant) and documented as derived; a move is told apart because another booking
  names this one as the one it replaced, and its actor stays unknown.

Only what happened before the cutoff is backfilled. From the cutoff on, live code writes the
facts, so a transition after it is never made twice. Rows already done are recorded in
``usage_backfill``, so a second run does nothing (idempotent).
"""

from dataclasses import dataclass
from datetime import datetime

from app.domain import booking_facts
from app.domain.booking import BookingKind, BookingStatus
from app.domain.booking_facts import BookingFacts
from app.domain.scheduling import DispatchKind
from app.domain.usage import ActorClass, UsageFact

BATCH_SIZE = 200
"""Bookings backfilled per transaction: a fixed bound on the write lock, not a policy."""

MAX_BATCHES = 25
"""Batches the worker backfills per pass before it goes on with its jobs; the rest follows on the
next pass, until the backfill is complete. The CLI does not stop at it."""


@dataclass(frozen=True)
class BackfillBooking:
    """What the backfill reads of one booking row: no personal data and no identifier beyond id.

    Attributes:
        id: The row, to record it as done.
        organisation_id: The group's organisation.
        group_id: The group.
        dispatch_kind: How the group's bookings reach staff.
        kind: Scheduled or direct (from the slot).
        locale: The participant's language, a column the purge leaves.
        status: The booking's status now.
        created_at: When it was made.
        start: The slot start.
        confirmed_at: When the participant confirmed, if they did.
        confirm_by: The deadline to confirm, if it awaited confirmation.
        cancelled_at: When it was cancelled, if it was.
        closure_id: The closure that suspended it, if one did.
        change_reason: The planner's fixed reason, if one asked for a change.
        rescheduled_from_id: The booking this one replaced, if it was made by a move.
        was_moved: Whether another booking names this one as the one it replaced.
    """

    id: int
    organisation_id: int
    group_id: int
    dispatch_kind: DispatchKind
    kind: BookingKind
    locale: str | None
    status: BookingStatus
    created_at: datetime
    start: datetime
    confirmed_at: datetime | None = None
    confirm_by: datetime | None = None
    cancelled_at: datetime | None = None
    closure_id: int | None = None
    change_reason: str | None = None
    rescheduled_from_id: int | None = None
    was_moved: bool = False


def _cancelled_by(b: BackfillBooking) -> ActorClass:
    """Return who cancelled, as far as the row says (derived, see the module docstring)."""
    if b.change_reason is not None:
        return ActorClass.PLANNER
    if b.closure_id is not None:
        return ActorClass.CLOSURE
    return ActorClass.PARTICIPANT


def _describe(b: BackfillBooking) -> BookingFacts:
    """Describe the row for the shared fact builders."""
    return BookingFacts(
        organisation_id=b.organisation_id,
        group_id=b.group_id,
        booking_kind=b.kind,
        dispatch_kind=b.dispatch_kind,
        locale=b.locale,
        created_at=b.created_at,
        start=b.start,
        change_reason=b.change_reason,
    )


def facts_of(b: BackfillBooking, cutoff: datetime) -> list[UsageFact]:
    """Return the facts for what happened to ``b`` before ``cutoff``; none for a later booking."""
    if b.created_at >= cutoff:
        return []
    d = _describe(b)
    facts = [
        booking_facts.created(d, moved=b.rescheduled_from_id is not None),
        booking_facts.slotted(d),
    ]
    if b.confirmed_at is not None and b.confirmed_at < cutoff:
        facts.append(booking_facts.confirmed(d, b.confirmed_at))
    if (
        b.status is BookingStatus.CANCELLED
        and b.cancelled_at is not None
        and b.cancelled_at < cutoff
    ):
        facts.append(_ending(b, d, b.cancelled_at))
    return facts


def _ending(b: BackfillBooking, d: BookingFacts, cancelled_at: datetime) -> UsageFact:
    """Return how a cancelled booking ended: moved, lapsed unconfirmed, or cancelled."""
    if b.was_moved:
        return booking_facts.moved(d, cancelled_at, None)
    if b.confirmed_at is None and b.confirm_by is not None and cancelled_at >= b.confirm_by:
        return booking_facts.confirm_expired(d, cancelled_at)
    return booking_facts.cancelled(d, cancelled_at, _cancelled_by(b), b.change_reason)
