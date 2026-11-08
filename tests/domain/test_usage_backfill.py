"""What can honestly be said about old bookings from the columns that survive (ADR 0117 point 5)."""

from datetime import UTC, datetime, timedelta

from app.domain.booking import BookingKind, BookingStatus
from app.domain.scheduling import DispatchKind
from app.domain.usage import ActorClass, FactKind, UsageOutcome, coarse, hour_of
from app.domain.usage_backfill import BackfillBooking, facts_of

CUTOFF = datetime(2026, 11, 10, 8, tzinfo=UTC)
CREATED = datetime(2026, 11, 1, 9, 13, tzinfo=UTC)
START = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


def _booking(**extra: object) -> BackfillBooking:
    fields: dict[str, object] = {
        "id": 1,
        "organisation_id": 4,
        "group_id": 7,
        "dispatch_kind": DispatchKind.CONTACT_CENTER,
        "kind": BookingKind.SCHEDULED,
        "locale": "nl",
        "status": BookingStatus.CONFIRMED,
        "created_at": CREATED,
        "start": START,
        "confirmed_at": CREATED + timedelta(minutes=20),
    }
    fields.update(extra)
    return BackfillBooking(**fields)  # type: ignore[arg-type]


def _kinds(booking: BackfillBooking) -> list[FactKind]:
    return [f.kind for f in facts_of(booking, CUTOFF)]


def test_a_confirmed_booking_was_created_slotted_and_confirmed() -> None:
    facts = {f.kind: f for f in facts_of(_booking(), CUTOFF)}

    assert set(facts) == {
        FactKind.BOOKING_CREATED,
        FactKind.BOOKING_SLOTTED,
        FactKind.BOOKING_CONFIRMED,
    }
    created = facts[FactKind.BOOKING_CREATED]
    assert created.bucket_hour == hour_of(CREATED)
    assert created.value_seconds == coarse(int((START - CREATED).total_seconds()))
    assert (created.organisation_id, created.group_id, created.locale) == (4, 7, "nl")
    assert facts[FactKind.BOOKING_SLOTTED].bucket_hour == hour_of(START)
    assert facts[FactKind.BOOKING_CONFIRMED].value_seconds == 20 * 60 // 900 * 900


def test_a_cancellation_with_a_planner_reason_is_the_planners() -> None:
    cancelled = _booking(
        status=BookingStatus.CANCELLED,
        cancelled_at=CREATED + timedelta(days=1),
        change_reason="staffing",
    )

    fact = next(f for f in facts_of(cancelled, CUTOFF) if f.kind is FactKind.BOOKING_CANCELLED)

    assert fact.actor_class is ActorClass.PLANNER
    assert fact.outcome is UsageOutcome.CANCELLED_BY_PLANNER
    assert fact.reason_code == "staffing"


def test_a_cancellation_of_a_booking_on_hold_is_the_closures() -> None:
    cancelled = _booking(
        status=BookingStatus.CANCELLED, cancelled_at=CREATED + timedelta(days=1), closure_id=3
    )

    fact = next(f for f in facts_of(cancelled, CUTOFF) if f.kind is FactKind.BOOKING_CANCELLED)

    assert fact.outcome is UsageOutcome.CANCELLED_BY_CLOSURE


def test_a_plain_cancellation_is_the_participants() -> None:
    cancelled = _booking(status=BookingStatus.CANCELLED, cancelled_at=CREATED + timedelta(days=1))

    fact = next(f for f in facts_of(cancelled, CUTOFF) if f.kind is FactKind.BOOKING_CANCELLED)

    assert fact.outcome is UsageOutcome.CANCELLED_BY_PARTICIPANT
    assert fact.bucket_hour == hour_of(START)


def test_a_booking_never_confirmed_and_cancelled_after_its_deadline_expired() -> None:
    expired = _booking(
        status=BookingStatus.CANCELLED,
        confirmed_at=None,
        confirm_by=CREATED + timedelta(hours=24),
        cancelled_at=CREATED + timedelta(hours=25),
    )

    kinds = _kinds(expired)

    assert FactKind.BOOKING_CONFIRM_EXPIRED in kinds
    assert FactKind.BOOKING_CANCELLED not in kinds
    assert FactKind.BOOKING_CONFIRMED not in kinds


def test_an_old_booking_that_was_moved_is_one_move_not_a_cancellation() -> None:
    moved = _booking(
        status=BookingStatus.CANCELLED, cancelled_at=CREATED + timedelta(days=1), was_moved=True
    )

    facts = facts_of(moved, CUTOFF)
    kinds = [f.kind for f in facts]

    assert FactKind.BOOKING_MOVED in kinds
    assert FactKind.BOOKING_CANCELLED not in kinds
    assert next(f for f in facts if f.kind is FactKind.BOOKING_MOVED).actor_class is None


def test_the_booking_that_replaces_a_moved_one_is_no_new_booking() -> None:
    replacement = _booking(rescheduled_from_id=9)

    created = next(f for f in facts_of(replacement, CUTOFF) if f.kind is FactKind.BOOKING_CREATED)

    assert created.reason_code == "move"


def test_what_happened_after_the_cutoff_has_its_own_live_facts() -> None:
    """Live facts exist from the cutoff on, so the backfill must not repeat them."""
    late_cancel = _booking(status=BookingStatus.CANCELLED, cancelled_at=CUTOFF + timedelta(hours=1))
    created_after = _booking(created_at=CUTOFF + timedelta(minutes=1))

    assert FactKind.BOOKING_CANCELLED not in _kinds(late_cancel)
    assert FactKind.BOOKING_CREATED in _kinds(late_cancel)
    assert _kinds(created_after) == []


def test_a_direct_call_keeps_its_kind() -> None:
    facts = facts_of(_booking(kind=BookingKind.DIRECT), CUTOFF)

    assert {f.booking_kind for f in facts} == {BookingKind.DIRECT}


def test_a_locale_that_is_no_language_tag_is_left_out() -> None:
    assert {f.locale for f in facts_of(_booking(locale="nl_NL"), CUTOFF)} == {None}


def test_suspensions_are_not_invented_without_a_time() -> None:
    suspended = _booking(status=BookingStatus.SUSPENDED, closure_id=3)

    assert FactKind.BOOKING_SUSPENDED not in _kinds(suspended)
