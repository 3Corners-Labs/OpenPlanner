"""Writes the usage facts of a booking's call, in the transaction that records the event.

The three places that append a call event to a booking's timeline - a webhook or a job through
``IntegrationService``, the agenda's poll (``MeetingStatusService``) and a participant leaving a
direct call (``BookingService.leave_direct``) - each call ``record_call_event`` just before they
write the event and the new call status. So a fact is written with the change it describes, and
rolled back with it (``Transaction.record_usage`` runs in a savepoint and never fails the
action). What the facts are is decided by ``call_facts``; this module only loads what they need.

    event ─► skipped unless ``call_facts.PRODUCES`` it (no booking is loaded for the rest)
          ─► booking + group ─► status before/after ─► ``call_facts.call_facts`` ─► record_usage
"""

from datetime import datetime

from app.domain.booking import Booking
from app.domain.call_facts import FIRST_ONLY, PRODUCES, call_facts, needs_load
from app.domain.calls import CallStatus, EventKind
from app.domain.direct_queue import QueuePhase, load_since, phase
from app.domain.ports import Transaction
from app.domain.scheduling import Group
from app.domain.usage_recording import facts_of_booking


async def record_call_event(
    tx: Transaction,
    booking_id: int,
    kind: EventKind,
    before: CallStatus,
    after: CallStatus,
    at: datetime,
    detail: str = "",
    *,
    booking: Booking | None = None,
    group: Group | None = None,
) -> None:
    """Record the facts that ``kind`` at ``at`` adds to the call of ``booking_id``.

    Call it before the event is added to the timeline: a trial event counts once per booking,
    which it finds out from the events that are there already. ``booking`` and ``group`` are
    passed by a caller that holds them; otherwise they are read. A booking or group that
    is gone makes no fact.
    """
    if kind not in PRODUCES:
        return
    booking = booking or await tx.get_booking(booking_id)
    if booking is None:
        return
    group = group or await tx.get_group_by_id(booking.group_id)
    if group is None:
        return
    b = facts_of_booking(booking, group)
    load = await _direct_load(tx, group, at) if needs_load(kind, b) else None
    first = True
    if kind in FIRST_ONLY:
        first = all(e.kind is not kind for e in await tx.get_booking_events(booking_id))
    for fact in call_facts(kind, b, before, after, at, detail, load=load, first_of_kind=first):
        await tx.record_usage(fact)


async def _direct_load(tx: Transaction, group: Group, at: datetime) -> int:
    """Return the group's direct calls that wait or are connected at ``at`` (metric Q9)."""
    calls = await tx.get_direct_calls(group.id, load_since(group, at))
    return sum(1 for c in calls if phase(c, group, at) is not QueuePhase.DONE)
