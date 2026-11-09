"""Change requests in SQLite (ADR 0023 step 2 and 2b): stored, read back, found by phone, pruned."""

from dataclasses import replace
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.change_requests import ChangeRequest
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import ADA, NOW, SLOT_0900, SLOT_0930, TEST_TOKEN_KEY, book


async def test_a_request_is_stored_found_replaced_and_pruned(engine: AsyncEngine) -> None:
    await seed_group(engine, capacity=1)
    uow = SqlUnitOfWork(engine)
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY), contact=ADA)
    booking = receipt.booking
    booking_id = booking.id or 0
    expires = NOW + timedelta(hours=1)

    async with uow.locked() as tx:
        by_phone = await tx.find_bookings_by_phone(booking.group_id, "+31612345678")
        other = await tx.find_bookings_by_phone(booking.group_id, "+31687654321")
        matched = await tx.add_change_request(
            ChangeRequest(
                "chr_a", "opc_bot", booking.group_id, SLOT_0930, booking_id, SLOT_0900, NOW, expires
            )
        )
        await tx.add_change_request(
            ChangeRequest("chr_b", "opc_bot", booking.group_id, None, None, None, NOW, expires)
        )
        await tx.save_change_request(replace(matched, superseded_at=NOW))
    async with uow.read() as tx:
        latest = await tx.get_latest_change_request(booking_id)
        unmatched = await tx.get_change_request("chr_b")
        missing = await tx.get_change_request("chr_c")

    assert [b.id for b in by_phone] == [booking_id]
    assert other == []
    assert latest == replace(matched, superseded_at=NOW)
    assert latest.target_start == SLOT_0930
    assert unmatched is not None
    assert unmatched.booking_id is None
    assert missing is None

    async with uow.locked() as tx:
        kept = await tx.delete_change_requests_expired_before(expires)
        removed = await tx.delete_change_requests_expired_before(expires + timedelta(seconds=1))
    async with uow.read() as tx:
        assert await tx.get_change_request("chr_a") is None
    assert (kept, removed) == (0, 2)
