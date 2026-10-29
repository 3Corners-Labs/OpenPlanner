"""OpenPlanner's own queue: what agents see and which booking is next (ADR 0043)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.domain.booking import Booking, BookingKind, BookingStatus, Contact
from app.domain.calls import CallStatus
from app.domain.own_queue import Claim, QueueEntry, next_up, not_claimed_in_time, visible
from tests.support.domain import make_group

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
GROUP = make_group(queue_visible_from_minutes=15)


def entry(
    booking_id: int,
    minutes_from_now: int,
    *,
    lobby: bool = False,
    claimed: bool = False,
    status: BookingStatus = BookingStatus.CONFIRMED,
) -> QueueEntry:
    start = NOW + timedelta(minutes=minutes_from_now)
    booking = Booking(
        id=booking_id,
        group_id=1,
        slot_id=booking_id,
        start=start,
        end=start + timedelta(minutes=30),
        status=status,
        contact=Contact("Ada", "Lovelace", "ada@example.org", None),
        locale="nl",
        token_hash="h",
        created_at=NOW - timedelta(days=1),
        claim=Claim(NOW, "pseudonym") if claimed else None,
    )
    return QueueEntry(booking, CallStatus(booking_id, participants=1 if lobby else 0))


def ids(entries: list[QueueEntry]) -> list[int | None]:
    return [e.booking.id for e in entries]


def test_shows_bookings_from_the_visible_window_until_they_end_in_start_order() -> None:
    entries = [
        entry(1, 16),  # too far ahead
        entry(2, 15),  # just visible
        entry(3, -29),  # started, not ended
        entry(4, -30),  # ended now
        entry(5, 0, status=BookingStatus.CANCELLED),
    ]

    assert ids(visible(entries, GROUP, NOW)) == [3, 2]


def test_next_is_the_citizen_in_the_lobby_before_the_earliest_start() -> None:
    entries = [entry(1, -10), entry(2, -5, lobby=True), entry(3, -20, claimed=True)]

    nxt = next_up(entries)

    assert nxt is not None
    assert nxt.booking.id == 2
    assert nxt.in_lobby


def test_next_is_the_earliest_start_when_nobody_waits() -> None:
    assert (nxt := next_up([entry(1, 10), entry(2, 5)])) is not None
    assert nxt.booking.id == 2


def test_nothing_is_next_when_everything_is_claimed() -> None:
    assert next_up([entry(1, 0, claimed=True)]) is None


def test_a_host_in_the_meeting_is_not_a_waiting_citizen() -> None:
    waiting = entry(1, 0, lobby=True)
    joined = replace(waiting, status=replace(waiting.status, host_joined=True, participants=2))

    assert waiting.in_lobby
    assert not joined.in_lobby


def test_not_claimed_in_time_after_the_groups_limit() -> None:
    limit = timedelta(minutes=10)

    assert not_claimed_in_time(entry(1, -11), NOW, limit)
    assert not not_claimed_in_time(entry(2, -10), NOW, limit)
    assert not not_claimed_in_time(entry(3, -11, claimed=True), NOW, limit)


def test_direct_calls_are_marked() -> None:
    direct = entry(1, 0)
    direct = replace(direct, booking=replace(direct.booking, kind=BookingKind.DIRECT))

    assert direct.is_direct
    assert not entry(2, 0).is_direct
