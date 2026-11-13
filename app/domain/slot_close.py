"""What a slot that has ended tells the reports (ADR 0117 step 3; pure functions).

A slot is closed for reporting once it has ended and the late reports of Webex and WxCC have
had time to arrive (``CLOSE_DELAY``). Then, for each slot, one set of facts is written, keyed
on the slot's start so the cohort of a slot is one hour of the report:

    ``slot.closed``      capacity snapshot and the bookings that count             (C1, C2, C3)
    ``slot.shortfall``   agents on duty and bookings, when the slot had more bookings than
                         agents, as ``open`` or ``accepted`` by a planner            (C5)
    ``booking.outcome``  ``completed`` or ``no_show`` of each confirmed booking      (U2, U3)
    ``call.not_picked_up``  a confirmed booking nobody picked up within the group's
                         ``not_picked_up_minutes``, keyed on the slot start too      (Q6)

Nothing in the application sets a booking's status to ``completed`` or ``no_show``. The
outcome is therefore *derived from the call*, and only where the call was observed:

    no meeting was made, or it failed          no outcome (nothing was observed)
    scheduled, guest seen and host joined      ``completed``
    scheduled, guest seen, host never joined   no outcome (the participant came; this is Q6)
    scheduled, no guest seen                   ``no_show``
    direct, guest seen and host joined         ``completed``; any other direct call: no outcome

A slot of a counted pool (ADR 0122) is closed too, with its own rule: its outcome is the
operator's explicit evidence (A27), never an observation of a meeting. Its capacity belongs to a
pool that several services share, not to the service, so it writes no ``slot.closed`` and no
``slot.shortfall`` (a pool's shortage is listed on the pool's own page):

    pool booking completed by an operator        ``completed``
    pool booking marked as a no show             ``no_show``
    any other pool booking                       no outcome (expiry is not evidence of absence)
    confirmed, and nobody claimed or started it  ``call.not_picked_up``

A guest is *seen* when a guest-joined event or a poll showed someone besides the host in the
meeting, or two people were in it at once. ``no_show`` means that no join was observed. Where
Webex's webhooks are not set up and nobody opened the agenda, a join cannot be seen; the
documentation says so. The count of the slot is an upper bound too: a slot exists only when it
had a booking, so hours nobody booked are not in C1 to C3.

Pure Python, no I/O: ``slot_close_service`` loads the ended slots and stores the facts.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.booking import BookingKind, BookingStatus
from app.domain.booking_facts import BookingFacts, keyed
from app.domain.calls import CallState, CallStatus
from app.domain.scheduling import DispatchKind, Group
from app.domain.usage import ActorClass, FactKind, UsageFact, UsageOutcome

CLOSE_DELAY = timedelta(hours=2)
"""How long after a slot's end it is closed for reporting. The join page closes 30 minutes after
the end and a poll or a late webhook follows within minutes; two hours leaves room for a worker
that was down. Fixed on purpose: a shorter delay would count a join that arrives late as a
no show, and nobody needs a longer one (the call status outlives the purge)."""

BATCH_SLOTS = 200
"""Slots one transaction closes: a fixed bound on the write lock, like the rollup's batch."""

OUTCOME_RULE = """
Nothing in the application sets a booking's status to `completed` or `no_show`, so the outcome is
derived from the call once the slot has ended and the late reports have arrived (two hours):

| What the call showed | Outcome |
|---|---|
| no meeting was made, or it failed | none (nothing was observed) |
| scheduled; the participant was seen and the host joined | `completed` |
| scheduled; the participant was seen, the host never joined | none (they came: see not picked up) |
| scheduled; the participant was not seen | `no_show` |
| direct; the participant was seen and the host joined | `completed` |
| direct; anything else | none |

The participant is *seen* when a guest-joined event or a poll showed someone besides the host in
the meeting, or two people were in it at once. `no_show` therefore means that no join was observed:
where Webex's webhooks are not set up and nobody opened the agenda, a join cannot be seen. A slot
exists only when it had a booking, so hours nobody booked are not in utilisation. Slots that ended
before the upgrade are not closed afterwards, and `completed` and `no_show` cannot be backfilled.

An appointment of a flow on a counted pool (ADR 0122) has no meeting to observe: its outcome is
the operator's explicit *Complete* (`completed`), or `no_show` where the booking was marked so;
work that expired unstarted has no outcome. It counts as not picked up when, at closing, nobody
had claimed it or started it. Its places belong to a pool that several services share, so it adds
nothing to utilisation, fill or idle capacity (C1 to C3) and no shortfall (C5): a pool's shortage
is on the pool's own page. Work completed more than two hours after the appointment's end (work
in recovery, among others) is closed without its outcome.
"""
"""The rule of ``outcome_of`` in words, as published in ``docs/reporting.md``."""

SHORTFALL_OPEN = "open"
SHORTFALL_ACCEPTED = "accepted"
SHORTFALL_RESOLVED = "resolved"
"""The ``reason_code`` of a ``slot.shortfall`` fact (metric C5): still short when the slot
started, short but accepted by a planner, or no longer short after a planner's bulk request."""


@dataclass(frozen=True)
class ClosedBooking:
    """A booking of an ended slot, as the closing needs it: no name, no address, no link.

    Attributes:
        status: The booking's status when the slot is closed.
        locale: The participant's language as stored.
        created_at: When the booking was made.
        start: The slot start.
        claimed: Whether an agent of the own queue claimed it.
        call: Its aggregated call status.
    """

    status: BookingStatus
    locale: str | None
    created_at: datetime
    start: datetime
    claimed: bool
    call: CallStatus


@dataclass(frozen=True)
class EndedSlot:
    """A slot that has ended, with its bookings.

    Attributes:
        slot_id: The slot's row id (not written to any fact).
        group_id: The group.
        kind: Scheduled, or the slot of a direct call.
        start: The slot start.
        end: The slot end.
        capacity: The capacity snapshot of the slot.
        bookings: Every booking in it, whatever its status.
        pool_id: The counted pool the slot draws on (ADR 0122), ``None`` for the service's
            own (legacy) slot.
    """

    slot_id: int
    group_id: int
    kind: BookingKind
    start: datetime
    end: datetime
    capacity: int
    bookings: tuple[ClosedBooking, ...]
    pool_id: int | None = None

    @property
    def pooled(self) -> bool:
        """Whether the slot draws on a counted pool rather than the service's own staffing."""
        return self.pool_id is not None


@dataclass(frozen=True)
class EndedSlots:
    """The ended slots one pass reads.

    Attributes:
        slots: Every slot ending after the watermark up to ``covered_until`` (all slots that
            end at the same moment are in, never half of them).
        covered_until: The moment the watermark moves to.
    """

    slots: tuple[EndedSlot, ...]
    covered_until: datetime


@dataclass(frozen=True)
class SlotStaffing:
    """The agenda's view of a scheduled slot when it is closed (ADR 0044).

    Attributes:
        agents: Agents on duty for the whole slot under the current staffing.
        booked: Confirmed bookings in it.
        accepted: Whether a planner accepted the shortfall at exactly these counts.
    """

    agents: int
    booked: int
    accepted: bool


def slot_facts(
    group: Group, start: datetime, kind: BookingKind = BookingKind.SCHEDULED
) -> BookingFacts:
    """Describe a slot of ``group`` for the fact builders (there is no booking to describe)."""
    return BookingFacts(
        group.organisation_id, group.id, kind, group.dispatch_kind, None, start, start
    )


def shortfall_resolved(group: Group, start: datetime, agents: int, booked: int) -> UsageFact:
    """A planner's bulk request left the slot at ``start`` with no shortfall (metric C5)."""
    return keyed(
        FactKind.SLOT_SHORTFALL,
        slot_facts(group, start),
        start,
        actor_class=ActorClass.PLANNER,
        reason_code=SHORTFALL_RESOLVED,
        capacity=agents,
        booked=booked,
    )


def counts(booking: ClosedBooking) -> bool:
    """Return whether ``booking`` counts as booked: confirmed, not cancelled, not pending."""
    return booking.status in (
        BookingStatus.CONFIRMED,
        BookingStatus.COMPLETED,
        BookingStatus.NO_SHOW,
    )


def guest_seen(call: CallStatus) -> bool:
    """Return whether the participant was seen in the meeting."""
    return call.guest_joined_at is not None or call.peak_participants >= 2


def outcome_of(booking: ClosedBooking, kind: BookingKind) -> UsageOutcome | None:
    """Return how ``booking`` ended, or ``None`` when the call gives no evidence (see module)."""
    if booking.status is not BookingStatus.CONFIRMED:
        return None
    if booking.call.call in (CallState.NONE, CallState.FAILED):
        return None
    seen, host = guest_seen(booking.call), booking.call.host_joined
    if seen and host:
        return UsageOutcome.COMPLETED
    if kind is BookingKind.SCHEDULED and not seen:
        return UsageOutcome.NO_SHOW
    return None


def pool_outcome_of(booking: ClosedBooking) -> UsageOutcome | None:
    """Return how a pool booking ended: only from the explicit evidence of its status (A27).

    The operator's *Complete* sets the booking to ``completed``; nothing is derived from a
    meeting or from work that expired unstarted.
    """
    if booking.status is BookingStatus.COMPLETED:
        return UsageOutcome.COMPLETED
    if booking.status is BookingStatus.NO_SHOW:
        return UsageOutcome.NO_SHOW
    return None


def pool_not_picked_up(booking: ClosedBooking) -> bool:
    """Return whether nobody picked up a pool booking before its slot closed (metric Q6).

    A pool booking always reaches a queue (the own queue or a contact-center attempt), so it
    counts when it is still confirmed and nobody claimed it or started its work.
    """
    return booking.status is BookingStatus.CONFIRMED and not booking.claimed


def not_picked_up(booking: ClosedBooking, dispatch: DispatchKind, limit: timedelta) -> bool:
    """Return whether nobody picked ``booking`` up within ``limit`` of its start (metric Q6).

    Only a confirmed booking that reached staff counts: a work item was made, the booking fell
    back to the own queue, or the group has an own queue. In the own queue a claim is a pickup;
    otherwise it is the host joining the meeting.
    """
    if booking.status is not BookingStatus.CONFIRMED or booking.claimed:
        return False
    call = booking.call
    reached = (
        call.task_created_at is not None
        or call.fallback
        or dispatch is DispatchKind.OPENPLANNER_QUEUE
    )
    late = call.host_joined_at is None or call.host_joined_at - booking.start > limit
    return reached and late


def _facts_of(
    booking: ClosedBooking,
    organisation_id: int,
    group_id: int,
    kind: BookingKind,
    dispatch: DispatchKind,
) -> BookingFacts:
    """Describe ``booking`` for the fact builders."""
    return BookingFacts(
        organisation_id, group_id, kind, dispatch, booking.locale, booking.created_at, booking.start
    )


def closing_facts(
    slot: EndedSlot,
    organisation_id: int,
    dispatch: DispatchKind,
    not_picked_up_limit: timedelta,
    staffing: SlotStaffing | None,
) -> list[UsageFact]:
    """Return the facts that closing ``slot`` writes.

    A pool slot writes only the facts of its bookings, by the pool rule (see the module).

    Args:
        slot: The ended slot.
        organisation_id: The group's organisation.
        dispatch: How the group's bookings reach staff.
        not_picked_up_limit: The group's ``not_picked_up_minutes``.
        staffing: The agenda's view of a scheduled slot; ``None`` for a direct slot, a pool
            slot or a slot the staffing does not know.
    """
    facts: list[UsageFact] = []
    slot_facts = BookingFacts(
        organisation_id, slot.group_id, slot.kind, dispatch, None, slot.start, slot.start
    )
    if slot.kind is BookingKind.SCHEDULED and not slot.pooled:
        booked = sum(1 for b in slot.bookings if counts(b))
        facts.append(
            keyed(
                FactKind.SLOT_CLOSED, slot_facts, slot.start, capacity=slot.capacity, booked=booked
            )
        )
        if staffing is not None and staffing.booked > staffing.agents:
            state = SHORTFALL_ACCEPTED if staffing.accepted else SHORTFALL_OPEN
            facts.append(
                keyed(
                    FactKind.SLOT_SHORTFALL,
                    slot_facts,
                    slot.start,
                    reason_code=state,
                    capacity=staffing.agents,
                    booked=staffing.booked,
                )
            )
    for booking in slot.bookings:
        b = _facts_of(booking, organisation_id, slot.group_id, slot.kind, dispatch)
        if slot.pooled:
            outcome = pool_outcome_of(booking)
            unanswered = pool_not_picked_up(booking)
        else:
            outcome = outcome_of(booking, slot.kind)
            unanswered = not_picked_up(booking, dispatch, not_picked_up_limit)
        if outcome is not None:
            facts.append(
                keyed(
                    FactKind.BOOKING_OUTCOME,
                    b,
                    slot.start,
                    actor_class=ActorClass.SYSTEM,
                    outcome=outcome,
                )
            )
        if unanswered:
            facts.append(keyed(FactKind.CALL_NOT_PICKED_UP, b, slot.start))
    return facts


def in_order(slots: Sequence[EndedSlot]) -> list[EndedSlot]:
    """Return ``slots`` in the order they are closed: by end, then by id."""
    return sorted(slots, key=lambda s: (s.end, s.slot_id))
