"""What an ended slot tells the reports: capacity, shortfall, outcomes, not picked up."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.booking import BookingKind, BookingStatus
from app.domain.calls import CallState, CallStatus
from app.domain.scheduling import DispatchKind
from app.domain.slot_close import (
    ClosedBooking,
    EndedSlot,
    SlotStaffing,
    closing_facts,
    in_order,
    not_picked_up,
    outcome_of,
)
from app.domain.usage import FactKind, UsageFact, UsageOutcome

START = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
END = START + timedelta(minutes=30)
LIMIT = timedelta(minutes=10)
SEEN = CallStatus(
    1,
    call=CallState.ENDED,
    host_joined=True,
    host_joined_at=START,
    guest_joined_at=START,
    peak_participants=2,
)


def _booking(status: BookingStatus = BookingStatus.CONFIRMED, **call: object) -> ClosedBooking:
    return ClosedBooking(
        status,
        "nl",
        START - timedelta(days=1),
        START,
        False,
        replace(CallStatus(1, call=CallState.SCHEDULED), **call),  # type: ignore[arg-type]
    )


def _slot(*bookings: ClosedBooking, kind: BookingKind = BookingKind.SCHEDULED) -> EndedSlot:
    return EndedSlot(7, 2, kind, START, END, 3, bookings)


def _facts(slot: EndedSlot, staffing: SlotStaffing | None = None) -> list[UsageFact]:
    return closing_facts(slot, 1, DispatchKind.CONTACT_CENTER, LIMIT, staffing)


def test_a_guest_and_a_host_in_the_meeting_is_a_completed_booking() -> None:
    assert outcome_of(_booking(**vars_of(SEEN)), BookingKind.SCHEDULED) is UsageOutcome.COMPLETED


def vars_of(status: CallStatus) -> dict[str, object]:
    return {
        "call": status.call,
        "host_joined": status.host_joined,
        "host_joined_at": status.host_joined_at,
        "guest_joined_at": status.guest_joined_at,
        "peak_participants": status.peak_participants,
    }


def test_two_people_at_once_count_as_a_guest_even_when_the_join_was_not_seen() -> None:
    booking = _booking(host_joined=True, peak_participants=2)

    assert outcome_of(booking, BookingKind.SCHEDULED) is UsageOutcome.COMPLETED


def test_nobody_in_an_observed_meeting_is_a_no_show() -> None:
    assert outcome_of(_booking(), BookingKind.SCHEDULED) is UsageOutcome.NO_SHOW


def test_a_host_who_waited_alone_is_a_no_show() -> None:
    booking = _booking(host_joined=True, peak_participants=1)

    assert outcome_of(booking, BookingKind.SCHEDULED) is UsageOutcome.NO_SHOW


def test_a_guest_nobody_received_is_neither_completed_nor_a_no_show() -> None:
    booking = _booking(guest_joined_at=START, peak_participants=1)

    assert outcome_of(booking, BookingKind.SCHEDULED) is None


@pytest.mark.parametrize("state", [CallState.NONE, CallState.FAILED])
def test_a_call_that_was_never_observed_has_no_outcome(state: CallState) -> None:
    assert outcome_of(_booking(call=state), BookingKind.SCHEDULED) is None


@pytest.mark.parametrize(
    "status",
    [BookingStatus.CANCELLED, BookingStatus.SUSPENDED, BookingStatus.AWAITING_REBOOK],
)
def test_only_a_confirmed_booking_has_an_outcome(status: BookingStatus) -> None:
    assert outcome_of(_booking(status, **vars_of(SEEN)), BookingKind.SCHEDULED) is None


def test_a_direct_call_is_completed_or_nothing_never_a_no_show() -> None:
    assert outcome_of(_booking(**vars_of(SEEN)), BookingKind.DIRECT) is UsageOutcome.COMPLETED
    assert outcome_of(_booking(), BookingKind.DIRECT) is None


def test_a_booking_without_a_host_within_the_limit_was_not_picked_up() -> None:
    queued = _booking(task_created_at=START - timedelta(minutes=5))
    late = _booking(
        task_created_at=START - timedelta(minutes=5), host_joined_at=START + timedelta(minutes=11)
    )
    in_time = _booking(
        task_created_at=START - timedelta(minutes=5), host_joined_at=START + timedelta(minutes=9)
    )

    assert not_picked_up(queued, DispatchKind.CONTACT_CENTER, LIMIT)
    assert not_picked_up(late, DispatchKind.CONTACT_CENTER, LIMIT)
    assert not not_picked_up(in_time, DispatchKind.CONTACT_CENTER, LIMIT)


def test_nothing_that_never_reached_staff_is_not_picked_up() -> None:
    assert not not_picked_up(_booking(), DispatchKind.CONTACT_CENTER, LIMIT)
    assert not_picked_up(_booking(), DispatchKind.OPENPLANNER_QUEUE, LIMIT)
    assert not_picked_up(_booking(fallback=True), DispatchKind.CONTACT_CENTER, LIMIT)


def test_a_claim_in_the_own_queue_is_a_pickup() -> None:
    claimed = replace(_booking(), claimed=True)

    assert not not_picked_up(claimed, DispatchKind.OPENPLANNER_QUEUE, LIMIT)


def test_a_cancelled_booking_was_not_left_unanswered() -> None:
    cancelled = _booking(BookingStatus.CANCELLED, task_created_at=START)

    assert not not_picked_up(cancelled, DispatchKind.CONTACT_CENTER, LIMIT)


def test_a_closed_slot_counts_the_bookings_that_hold_a_confirmed_place() -> None:
    slot = _slot(
        _booking(),
        _booking(BookingStatus.CANCELLED),
        _booking(BookingStatus.PENDING_CONFIRMATION),
        _booking(BookingStatus.SUSPENDED),
    )

    (closed, *_) = _facts(slot)

    assert (closed.kind, closed.capacity, closed.booked) == (FactKind.SLOT_CLOSED, 3, 1)
    assert closed.bucket_hour == START
    assert closed.dispatch_kind is DispatchKind.CONTACT_CENTER


def test_a_shortfall_is_a_fact_open_or_accepted() -> None:
    slot = _slot(_booking())

    open_ = _facts(slot, SlotStaffing(1, 2, accepted=False))[1]
    accepted = _facts(slot, SlotStaffing(1, 2, accepted=True))[1]
    none = _facts(slot, SlotStaffing(2, 2, accepted=False))

    assert (open_.kind, open_.reason_code, open_.capacity, open_.booked) == (
        FactKind.SLOT_SHORTFALL,
        "open",
        1,
        2,
    )
    assert accepted.reason_code == "accepted"
    assert [f.kind for f in none] == [FactKind.SLOT_CLOSED, FactKind.BOOKING_OUTCOME]


def test_a_direct_slot_has_no_capacity_facts() -> None:
    slot = _slot(_booking(**vars_of(SEEN)), kind=BookingKind.DIRECT)

    kinds = [f.kind for f in _facts(slot, SlotStaffing(1, 5, accepted=False))]

    assert kinds == [FactKind.BOOKING_OUTCOME]


def test_outcomes_and_not_picked_up_are_keyed_on_the_slot_start() -> None:
    slot = _slot(_booking(task_created_at=START), _booking(**vars_of(SEEN)))

    facts = _facts(slot)

    assert {f.bucket_hour for f in facts} == {START}
    assert sorted(f.kind.value for f in facts) == [
        "booking.outcome",
        "booking.outcome",
        "call.not_picked_up",
        "slot.closed",
    ]
    assert [f.outcome for f in facts if f.kind is FactKind.BOOKING_OUTCOME] == [
        UsageOutcome.NO_SHOW,
        UsageOutcome.COMPLETED,
    ]


def test_slots_are_closed_in_the_order_they_ended() -> None:
    late = replace(_slot(), slot_id=1, end=END + timedelta(hours=1))
    early = replace(_slot(), slot_id=2)

    assert [s.slot_id for s in in_order([late, early])] == [2, 1]
