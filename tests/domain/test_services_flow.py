"""Every new booking records its flow and revision (ADR 0121 step 3a); nothing else changes."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.booking import Booking
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import FlowRef
from app.domain.scheduling import Offer, ScheduleRule
from app.domain.service_flows import FlowState, IntakeMode
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import (
    ADA,
    NOW,
    SLOT_0900,
    SLOT_0930,
    TEST_TOKEN_KEY,
    book_pending,
    make_group,
    window,
)

MONDAY_0915 = datetime(2026, 1, 5, 8, 15, tzinfo=UTC)
GROUP = make_group(id=1, slug="balie")


def _uow(rules: list[ScheduleRule] | None = None) -> InMemoryUnitOfWork:
    state = State()
    state.groups[1] = GROUP
    state.rules[1] = rules or [ScheduleRule(weekday=0, window=window(capacity=3))]
    return InMemoryUnitOfWork(state)


async def _with_flows(uow: InMemoryUnitOfWork) -> dict[str, FlowRef]:
    """Create the legacy flows of group 1 as step 2e does; return their identities by code."""
    async with uow.locked() as tx:
        await tx.ensure_default_flows(1)
    return {
        f.code: FlowRef(f.id or 0, f.current.revision if f.current else 0)
        for f in uow.state.flows.values()
    }


def _service(uow: InMemoryUnitOfWork, now: datetime = NOW) -> BookingService:
    return BookingService(uow, FixedClock(now), TEST_TOKEN_KEY, treatment_buffer=0)


async def test_a_booking_records_the_default_flow_of_the_appointment_path() -> None:
    uow = _uow()
    flows = await _with_flows(uow)

    receipt = await book_pending(_service(uow))

    assert receipt.booking.flow == flows["appointment"]
    stored = await _stored(uow, receipt.booking.id)
    assert stored.flow == flows["appointment"]


async def _stored(uow: InMemoryUnitOfWork, booking_id: int | None) -> Booking:
    async with uow.read() as tx:
        booking = await tx.get_booking(booking_id or 0)
    assert booking is not None
    return booking


async def test_an_explicit_flow_that_resolves_is_recorded() -> None:
    uow = _uow()
    flows = await _with_flows(uow)

    receipt = await _service(uow).book(
        group_slug="balie", start=SLOT_0900, contact=ADA, locale="nl", flow="appointment"
    )

    assert receipt.booking.flow == flows["appointment"]


@pytest.mark.parametrize("code", ["unknown", "direct", "Appointment", "a b", "x" * 41, ""])
async def test_an_explicit_flow_that_does_not_resolve_is_refused_and_nothing_is_stored(
    code: str,
) -> None:
    uow = _uow([ScheduleRule(weekday=0, window=window(capacity=3, offer=Offer.BOTH))])
    await _with_flows(uow)

    with pytest.raises(DomainError) as error:
        await _service(uow).book(
            group_slug="balie", start=SLOT_0900, contact=ADA, locale="nl", flow=code
        )

    assert error.value.code is ErrorCode.FLOW_NOT_AVAILABLE  # never the default instead
    assert not uow.state.bookings
    assert not uow.state.slots


async def test_an_archived_explicit_flow_is_refused_while_the_default_path_still_books() -> None:
    uow = _uow()
    await _with_flows(uow)
    for flow_id, stored in list(uow.state.flows.items()):
        uow.state.flows[flow_id] = replace(stored, state=FlowState.ARCHIVED)

    with pytest.raises(DomainError) as error:
        await _service(uow).book(
            group_slug="balie", start=SLOT_0900, contact=ADA, locale="nl", flow="appointment"
        )
    assert error.value.code is ErrorCode.FLOW_NOT_AVAILABLE

    receipt = await book_pending(_service(uow))  # implicit: the service's settings decide

    assert receipt.booking.flow is None


async def test_a_service_without_flow_rows_still_books_without_a_flow() -> None:
    """A rollback window: a group made by an older image has no flows; never refuse for that."""
    uow = _uow()
    assert not uow.state.flows

    receipt = await book_pending(_service(uow))

    assert receipt.booking.flow is None


async def test_a_service_without_flow_rows_refuses_an_explicit_flow() -> None:
    uow = _uow()

    with pytest.raises(DomainError) as error:
        await _service(uow).book(
            group_slug="balie", start=SLOT_0900, contact=ADA, locale="nl", flow="appointment"
        )

    assert error.value.code is ErrorCode.FLOW_NOT_AVAILABLE


async def test_moving_a_booking_keeps_its_flow() -> None:
    """A23: the stored meaning is preserved when the participant picks another time."""
    uow = _uow()
    flows = await _with_flows(uow)
    service = _service(uow)
    first = await service.book(
        group_slug="balie", start=SLOT_0900, contact=ADA, locale="nl", flow="appointment"
    )
    confirmed = await service.confirm(first.management_token)
    assert confirmed.flow == flows["appointment"]

    moved = await service.reschedule(first.management_token, SLOT_0930)

    assert moved.booking.id != first.booking.id
    assert moved.booking.flow == flows["appointment"]
    assert (await _stored(uow, moved.booking.id)).flow == flows["appointment"]


async def test_a_booking_made_without_a_flow_stays_without_when_moved() -> None:
    uow = _uow()
    service = _service(uow)
    first = await book_pending(service)
    await service.confirm(first.management_token)
    await _with_flows(uow)  # the repair runs between the booking and the move

    moved = await service.reschedule(first.management_token, SLOT_0930)

    assert moved.booking.flow is None


async def test_cancelling_changes_nothing_about_the_flow() -> None:
    uow = _uow()
    flows = await _with_flows(uow)
    service = _service(uow)
    receipt = await book_pending(service)

    cancelled = await service.cancel(receipt.management_token)

    assert cancelled.flow == flows["appointment"]


async def test_a_direct_call_records_the_default_flow_of_the_on_demand_path() -> None:
    uow = _uow([ScheduleRule(weekday=0, window=window("09:00", "10:00", 1, Offer.BOTH))])
    flows = await _with_flows(uow)

    receipt = await _service(uow, MONDAY_0915).start_direct(
        group_slug="balie", contact=ADA, locale="nl"
    )

    assert receipt.booking.flow == flows["direct"]


async def test_an_explicit_appointment_flow_on_the_direct_path_is_refused() -> None:
    uow = _uow([ScheduleRule(weekday=0, window=window("09:00", "10:00", 1, Offer.BOTH))])
    await _with_flows(uow)

    with pytest.raises(DomainError) as error:
        await _service(uow, MONDAY_0915).start_direct(
            group_slug="balie", contact=ADA, locale="nl", flow="appointment"
        )

    assert error.value.code is ErrorCode.FLOW_NOT_AVAILABLE
    assert not uow.state.bookings


async def test_flow_for_checks_a_page_without_writing() -> None:
    uow = _uow()
    flows = await _with_flows(uow)
    service = _service(uow)

    default = await service.flow_for("balie", IntakeMode.APPOINTMENT, None)
    explicit = await service.flow_for("balie", IntakeMode.APPOINTMENT, "appointment")

    assert default is not None
    assert default.ref == flows["appointment"]
    assert explicit == default
    with pytest.raises(DomainError):
        await service.flow_for("balie", IntakeMode.APPOINTMENT, "nope")
    with pytest.raises(DomainError) as missing:
        await service.flow_for("elsewhere", IntakeMode.APPOINTMENT, None)
    assert missing.value.code is ErrorCode.GROUP_NOT_FOUND
