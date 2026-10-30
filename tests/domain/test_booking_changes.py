from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain import booking_changes
from app.domain.booking import BookingStatus, ChangeReason
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError
from app.domain.job_plan import PURGE_AFTER, JobType, PlannedJob, is_stale
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import ADA, NOW, SLOT_0900, SLOT_0930, book, book_confirmed

REASON = ChangeReason.STAFFING


async def _request(uow: InMemoryUnitOfWork, booking_id: int) -> None:
    async with uow.locked() as tx:
        booking = await tx.get_booking(booking_id)
        assert booking is not None
        await booking_changes.request_rebook(
            tx, booking, NOW, reason=REASON, rebook_days=14, reminder_days=3
        )


async def test_a_request_to_rebook_frees_the_place_and_plans_its_messages(
    uow: InMemoryUnitOfWork, bookings: BookingService
) -> None:
    receipt = await book(bookings, SLOT_0900, "demo")
    booking_id = receipt.booking.id or 0

    await _request(uow, booking_id)

    waiting = uow.state.bookings[booking_id]
    rebook_by = SLOT_0900 + timedelta(days=14)
    assert (waiting.status, waiting.change_reason, waiting.rebook_by) == (
        BookingStatus.AWAITING_REBOOK,
        REASON,
        rebook_by,
    )
    assert not waiting.holds_place
    assert waiting.on_hold
    assert uow.state.jobs[booking_id] == [
        PlannedJob(JobType.END_WXCC_TASK, NOW, None),
        PlannedJob(JobType.DELETE_VIDEO_ROOM, NOW, None),
        PlannedJob(JobType.SEND_REBOOK_REQUEST, NOW, None, ChannelKind.EMAIL),
        PlannedJob(JobType.SEND_REBOOK_REQUEST, NOW, None, ChannelKind.SMS),
        PlannedJob(
            JobType.SEND_REBOOK_REMINDER, rebook_by - timedelta(days=3), None, ChannelKind.EMAIL
        ),
        PlannedJob(
            JobType.SEND_REBOOK_REMINDER, rebook_by - timedelta(days=3), None, ChannelKind.SMS
        ),
        PlannedJob(JobType.LAPSE_REBOOK, rebook_by, None),
        PlannedJob(JobType.PURGE_PII, rebook_by + PURGE_AFTER, None),
    ]
    # The freed place can be booked again at once.
    await book(bookings, SLOT_0900, "demo")
    await book(bookings, SLOT_0900, "demo")


async def test_without_a_phone_number_the_request_goes_by_email_only(
    uow: InMemoryUnitOfWork, bookings: BookingService
) -> None:
    receipt = await book_confirmed(
        bookings, group_slug="demo", start=SLOT_0900, contact=replace(ADA, phone=None), locale="nl"
    )
    await _request(uow, receipt.booking.id or 0)

    channels = {j.channel for j in uow.state.jobs[receipt.booking.id or 0] if j.channel}
    assert channels == {ChannelKind.EMAIL}


async def test_only_a_confirmed_booking_can_be_asked_to_rebook(
    uow: InMemoryUnitOfWork, bookings: BookingService
) -> None:
    receipt = await book(bookings, SLOT_0900, "demo")
    await bookings.cancel(receipt.management_token)

    with pytest.raises(DomainError, match="booking_not_active"):
        await _request(uow, receipt.booking.id or 0)


async def test_the_citizen_chooses_a_new_time_without_a_deadline(
    uow: InMemoryUnitOfWork, bookings: BookingService, clock: FixedClock
) -> None:
    receipt = await book(bookings, SLOT_0900, "demo")
    await _request(uow, receipt.booking.id or 0)
    clock.advance(SLOT_0900 + timedelta(days=2) - NOW)  # past the original time

    details = await bookings.find(receipt.management_token)
    moved = await bookings.reschedule(receipt.management_token, SLOT_0930 + timedelta(days=7))

    assert details.changeable
    assert moved.booking.status is BookingStatus.CONFIRMED
    old = uow.state.bookings[receipt.booking.id or 0]
    assert old.status is BookingStatus.CANCELLED
    assert not any(j.type is JobType.LAPSE_REBOOK for j in uow.state.jobs[old.id or 0])


async def test_the_citizen_can_cancel_a_request_to_rebook(
    uow: InMemoryUnitOfWork, bookings: BookingService
) -> None:
    receipt = await book(bookings, SLOT_0900, "demo")
    await _request(uow, receipt.booking.id or 0)

    cancelled = await bookings.cancel(receipt.management_token)

    assert cancelled.status is BookingStatus.CANCELLED
    assert cancelled.change_reason is REASON


async def test_an_unanswered_request_lapses_with_a_message(
    uow: InMemoryUnitOfWork, bookings: BookingService
) -> None:
    receipt = await book(bookings, SLOT_0900, "demo")
    booking_id = receipt.booking.id or 0
    await _request(uow, booking_id)
    later = SLOT_0900 + timedelta(days=14)

    async with uow.locked() as tx:
        booking = await tx.get_booking(booking_id)
        assert booking is not None
        assert await booking_changes.lapse(tx, booking, later)

    lapsed = uow.state.bookings[booking_id]
    assert (lapsed.status, lapsed.cancelled_at) == (BookingStatus.CANCELLED, later)
    messages = [(j.type, j.channel) for j in uow.state.jobs[booking_id] if j.run_at == later]
    assert (JobType.SEND_REBOOK_LAPSED, ChannelKind.EMAIL) in messages
    assert (JobType.SEND_REBOOK_LAPSED, ChannelKind.SMS) in messages


async def test_a_lapse_after_the_citizen_acted_does_nothing(
    uow: InMemoryUnitOfWork, bookings: BookingService
) -> None:
    receipt = await book(bookings, SLOT_0900, "demo")
    booking_id = receipt.booking.id or 0
    await _request(uow, booking_id)
    await bookings.cancel(receipt.management_token)

    async with uow.locked() as tx:
        booking = await tx.get_booking(booking_id)
        assert booking is not None
        assert not await booking_changes.lapse(tx, booking, NOW)


def test_rebook_notices_go_only_while_the_request_is_open(
    uow: InMemoryUnitOfWork,
) -> None:
    from tests.support.closures import confirmed_booking

    waiting = confirmed_booking().request_rebook(REASON, SLOT_0900 + timedelta(days=14))

    assert not is_stale(None, waiting, JobType.SEND_REBOOK_REMINDER)
    assert is_stale(None, waiting.cancel(NOW), JobType.SEND_REBOOK_REMINDER)
    assert is_stale(None, confirmed_booking(), JobType.SEND_REBOOK_REQUEST)
