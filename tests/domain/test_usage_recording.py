"""Each booking state change writes its usage fact, free of personal data (ADR 0117 step 2)."""

from dataclasses import fields
from datetime import timedelta

import pytest

from app.domain import booking_changes
from app.domain.authorization import Role
from app.domain.booking import ChangeReason
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.errors import DomainError
from app.domain.privacy import USAGE_FIELDS
from app.domain.scheduling import DispatchKind
from app.domain.services import BookingService
from app.domain.usage import ActorClass, FactKind, StaffFact, UsageFact, UsageOutcome, hour_of
from app.domain.usage_recording import LEAD_GRAIN_SECONDS, MOVE_REASON, coarse
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.closures import NOTICES
from tests.support.domain import (
    ADA,
    NOW,
    SLOT_0900,
    SLOT_0930,
    TUESDAY_0900,
    book,
    book_pending,
    participant,
)
from tests.support.principals import principal

GROUP_ADMIN = principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))


def _facts(uow: InMemoryUnitOfWork) -> list[UsageFact]:
    found = uow.state.usage_facts
    assert all(isinstance(f, UsageFact) for f in found)
    return [f for f in found if isinstance(f, UsageFact)]


def _kinds(uow: InMemoryUnitOfWork) -> list[FactKind]:
    return [f.kind for f in _facts(uow)]


async def test_a_new_booking_is_created_and_slotted(
    bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await book_pending(bookings, slug="demo")

    created, slotted = _facts(uow)
    assert (created.kind, slotted.kind) == (FactKind.BOOKING_CREATED, FactKind.BOOKING_SLOTTED)
    assert created.bucket_hour == hour_of(NOW)
    assert slotted.bucket_hour == hour_of(SLOT_0900)
    assert (created.organisation_id, created.group_id) == (1, 1)
    assert created.dispatch_kind is DispatchKind.CONTACT_CENTER
    assert created.locale == "nl"
    assert created.value_seconds == coarse(int((SLOT_0900 - NOW).total_seconds()))
    assert created.reason_code is None


async def test_confirming_records_the_time_it_took(
    bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    receipt = await book_pending(bookings, slug="demo")
    clock.advance(timedelta(minutes=90))

    await bookings.confirm(receipt.management_token)

    confirmed = _facts(uow)[-1]
    assert confirmed.kind is FactKind.BOOKING_CONFIRMED
    assert confirmed.actor_class is ActorClass.PARTICIPANT
    assert confirmed.value_seconds == 90 * 60


async def test_a_participant_cancelling_is_keyed_on_the_slot_with_the_lead_time(
    bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book(bookings, slug="demo")

    await bookings.cancel(receipt.management_token)

    cancelled = _facts(uow)[-1]
    assert cancelled.kind is FactKind.BOOKING_CANCELLED
    assert cancelled.bucket_hour == hour_of(SLOT_0900)
    assert cancelled.actor_class is ActorClass.PARTICIPANT
    assert cancelled.outcome is UsageOutcome.CANCELLED_BY_PARTICIPANT
    assert cancelled.value_seconds == coarse(int((SLOT_0900 - NOW).total_seconds()))


async def test_a_move_is_one_move_one_new_booking_and_no_cancellation(
    bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book(bookings, slug="demo")
    before = len(uow.state.usage_facts)

    await bookings.reschedule(receipt.management_token, SLOT_0930)

    moved, created, slotted = _facts(uow)[before:]
    assert moved.kind is FactKind.BOOKING_MOVED
    assert moved.actor_class is ActorClass.PARTICIPANT
    assert (created.kind, created.reason_code) == (FactKind.BOOKING_CREATED, MOVE_REASON)
    assert slotted.bucket_hour == hour_of(SLOT_0930)
    assert FactKind.BOOKING_CANCELLED not in _kinds(uow)[before:]


async def test_a_closure_suspends_and_an_admin_cancelling_is_the_closures(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book(bookings, slug="demo")
    booking_id = receipt.booking.id or 0
    before = len(uow.state.usage_facts)

    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, TUESDAY_0900, NOTICES))
    await closures.cancel(GROUP_ADMIN, "demo", booking_id)

    suspended, cancelled = _facts(uow)[before:]
    assert (suspended.kind, suspended.actor_class) == (
        FactKind.BOOKING_SUSPENDED,
        ActorClass.CLOSURE,
    )
    assert cancelled.outcome is UsageOutcome.CANCELLED_BY_CLOSURE


async def test_a_planner_cancelling_carries_the_fixed_reason(
    bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    receipt = await book(bookings, slug="demo")
    async with uow.locked() as tx:
        stored = await tx.get_booking(receipt.booking.id or 0)
        assert stored is not None
        group = await tx.get_group_by_id(stored.group_id)
        assert group is not None
        await booking_changes.cancel(
            tx,
            stored,
            group,
            clock.now(),
            actor=ActorClass.PLANNER,
            reason=ChangeReason.STAFFING,
        )

    cancelled = _facts(uow)[-1]
    assert cancelled.actor_class is ActorClass.PLANNER
    assert cancelled.outcome is UsageOutcome.CANCELLED_BY_PLANNER
    assert cancelled.reason_code == ChangeReason.STAFFING.value


async def test_an_unconfirmed_booking_that_lapses_is_expired_by_the_system(
    bookings: BookingService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    receipt = await book_pending(bookings, slug="demo")
    clock.advance(timedelta(days=2))
    async with uow.locked() as tx:
        stored = await tx.get_booking(receipt.booking.id or 0)
        assert stored is not None
        assert await booking_changes.lapse_unconfirmed(tx, stored, clock.now())

    expired = _facts(uow)[-1]
    assert expired.kind is FactKind.BOOKING_CONFIRM_EXPIRED
    assert expired.outcome is UsageOutcome.EXPIRED_UNCONFIRMED
    assert expired.actor_class is ActorClass.SYSTEM
    assert expired.bucket_hour == hour_of(SLOT_0900)


async def test_a_locale_that_is_no_language_tag_is_left_out_not_refused(
    bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await bookings.book(group_slug="demo", start=SLOT_0900, contact=ADA, locale="nl_NL")

    assert _facts(uow)[0].locale is None


async def test_no_fact_holds_a_name_an_address_a_number_or_a_token(
    bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book(bookings, slug="demo")
    await bookings.cancel(receipt.management_token)

    text = " ".join(repr(f) for f in uow.state.usage_facts)

    for secret in ("Ada", "Lovelace", "ada@example.org", "06 1234 5678", receipt.management_token):
        assert secret not in text
    allowed = set(USAGE_FIELDS)
    assert all(
        {f.name for f in fields(fact)} <= allowed
        for fact in uow.state.usage_facts
        if isinstance(fact, UsageFact | StaffFact)
    )


@pytest.mark.parametrize("slug", ["demo", "andere-team"])
async def test_the_fact_names_the_groups_organisation(
    bookings: BookingService, uow: InMemoryUnitOfWork, slug: str
) -> None:
    await book_pending(bookings, slug=slug)

    created = _facts(uow)[0]
    assert created.organisation_id == (1 if slug == "demo" else 2)


@pytest.mark.parametrize(
    ("seconds", "kept"),
    [
        (0, 0),
        (899, 0),
        (900, 900),
        (1799, 900),
        (-1, 0),
        (-900, -900),
        (-1799, -900),
        (86_399, 85_500),
    ],
)
def test_lead_and_hold_times_are_kept_to_the_quarter_hour(seconds: int, kept: int) -> None:
    assert coarse(seconds) == kept
    assert coarse(seconds) % LEAD_GRAIN_SECONDS == 0


async def test_no_fact_holds_a_time_that_gives_back_the_booking_instant(
    bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    """R3: the booking row keeps exact times, so a fact must not carry them."""
    receipt = await book(bookings, slug="demo")
    await bookings.cancel(receipt.management_token)

    for fact in _facts(uow):
        assert fact.value_seconds is None or fact.value_seconds % LEAD_GRAIN_SECONDS == 0
        assert fact.bucket_hour.minute == fact.bucket_hour.second == 0


async def test_a_failed_move_leaves_no_fact_behind(
    bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await book(bookings, slug="demo")
    await book(bookings, SLOT_0930, slug="demo")
    await book(bookings, SLOT_0930, slug="demo", contact=participant(2))
    before = list(uow.state.usage_facts)

    with pytest.raises(DomainError):
        await bookings.reschedule(receipt.management_token, SLOT_0930)

    assert uow.state.usage_facts == before
